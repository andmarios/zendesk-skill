"""Resolve IDs in Zendesk responses to people and custom field labels.

Ticket and comment payloads only carry numeric user IDs and bare custom field
``{id, value}`` pairs. These helpers annotate the payloads in place so saved
responses (and exports) are readable.

Lookups are best effort - a token without permission must not break a command -
but never silent: every failure is appended to the caller's ``warnings`` list,
which the operations return in their result.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from zendesk_skill import storage
from zendesk_skill.client import ZendeskAPIError, ZendeskAuthError, ZendeskClient

SHOW_MANY_BATCH_SIZE = 100  # Zendesk show_many accepts at most 100 IDs per call
FIELDS_CACHE_TTL = 3600  # seconds; ticket field definitions rarely change
FIELDS_PAGE_LIMIT = 20  # hard cap: 2000 fields
MISS_REFETCH_MIN_AGE = 60  # seconds; floor between refetches triggered by a cache miss

# (ticket ids key, annotated key)
PEOPLE_LISTS = (
    ("email_cc_ids", "email_cc_users"),
    ("follower_ids", "follower_users"),
)


def _warn(warnings: list[str] | None, message: str) -> None:
    if warnings is not None and message not in warnings:
        warnings.append(message)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def users_from_sideload(*results: dict) -> dict[int, dict]:
    """Collect {id: {name, email}} from the ``users`` sideload of API responses."""
    users: dict[int, dict] = {}
    for result in results:
        for u in (result or {}).get("users", []):
            if u.get("id") and (u.get("name") or u.get("email")):
                users[u["id"]] = {"name": u.get("name"), "email": u.get("email")}
    return users


async def resolve_users(
    client: ZendeskClient,
    user_ids,
    known: dict[int, dict] | None = None,
    warnings: list[str] | None = None,
) -> dict[int, dict]:
    """Resolve user IDs to {name, email}.

    IDs already in ``known`` (e.g. from a sideload) cost nothing; the rest are
    fetched in batches via users/show_many.
    """
    users = dict(known or {})
    ids = sorted({int(i) for i in user_ids if i} - set(users))
    for start in range(0, len(ids), SHOW_MANY_BATCH_SIZE):
        batch = ids[start:start + SHOW_MANY_BATCH_SIZE]
        try:
            result = await client.get(
                "users/show_many.json",
                params={"ids": ",".join(str(i) for i in batch)},
            )
        except (ZendeskAPIError, ZendeskAuthError) as e:
            _warn(warnings, f"Could not look up {len(batch)} user(s) ({e}); showing IDs only")
            continue
        users.update(users_from_sideload(result))
    return users


async def annotate_people(
    client: ZendeskClient,
    tickets: list[dict] | None = None,
    comments: list[dict] | None = None,
    known_users: dict[int, dict] | None = None,
    warnings: list[str] | None = None,
) -> dict[int, dict]:
    """Add people details to tickets and comments, in place.

    Tickets gain requester_name/requester_email, assignee_name/assignee_email,
    email_cc_users and follower_users (each a list of {id, name, email}).
    Comments gain author_name. Returns the id -> {name, email} map.
    """
    tickets = tickets or []
    comments = comments or []
    ids = set()
    for t in tickets:
        ids.add(t.get("requester_id"))
        ids.add(t.get("assignee_id"))
        for ids_key, _ in PEOPLE_LISTS:
            ids.update(t.get(ids_key) or [])
        ids.update(t.get("collaborator_ids") or [])
    for c in comments:
        ids.add(c.get("author_id"))

    users = await resolve_users(client, ids, known_users, warnings)

    def person(uid):
        return {"id": uid, **users[uid]}

    for t in tickets:
        for role in ("requester", "assignee"):
            u = users.get(t.get(f"{role}_id"))
            if u:
                if u.get("name"):
                    t[f"{role}_name"] = u["name"]
                if u.get("email"):
                    t[f"{role}_email"] = u["email"]
        for ids_key, out_key in PEOPLE_LISTS:
            resolved = [person(i) for i in t.get(ids_key) or [] if i in users]
            if resolved:
                t[out_key] = resolved
        # Accounts without the newer CC/follower split only populate collaborator_ids
        if not t.get("email_cc_users") and not t.get("follower_users"):
            legacy = [person(i) for i in t.get("collaborator_ids") or [] if i in users]
            if legacy:
                t["email_cc_users"] = legacy
    for c in comments:
        u = users.get(c.get("author_id"))
        if u and u.get("name"):
            c["author_name"] = u["name"]
    return users


# ---------------------------------------------------------------------------
# Custom fields
# ---------------------------------------------------------------------------


def _cache_path(client: ZendeskClient) -> Path:
    subdomain = getattr(getattr(client, "_auth_provider", None), "subdomain", None) or "default"
    safe = "".join(ch for ch in str(subdomain) if ch.isalnum() or ch in "-_")
    return Path(storage.DEFAULT_STORAGE_DIR) / f"ticket_fields_{safe}.json"


def _valid_definition(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("title"), (str, type(None)))
        and isinstance(entry.get("type"), (str, type(None)))
        and isinstance(entry.get("options"), dict)
    )


def _read_cache(path: Path) -> tuple[dict[int, dict], float] | None:
    """Cached definitions and when they were fetched, or None if missing/stale/malformed.

    Every entry is validated (a file from another version or a partial write must
    never reach the labelling code); anything wrong just means "refetch".
    """
    try:
        cached = json.loads(path.read_text())
        fetched_at = float(cached["fetched_at"])
        fields = {int(k): v for k, v in cached["fields"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if time.time() - fetched_at >= FIELDS_CACHE_TTL or not all(_valid_definition(v) for v in fields.values()):
        return None
    return fields, fetched_at


async def _fetch_fields(client: ZendeskClient, warnings: list[str] | None) -> dict[int, dict] | None:
    """Fetch all ticket field definitions (bounded paging); None, with a warning, on failure."""
    fields: dict[int, dict] = {}
    try:
        for page in range(1, FIELDS_PAGE_LIMIT + 1):
            result = await client.get("ticket_fields.json", params={"page": page, "per_page": 100})
            for f in result.get("ticket_fields", []):
                fields[f["id"]] = {
                    "title": f.get("title") or f.get("raw_title"),
                    "type": f.get("type"),
                    "options": {
                        o.get("value"): o.get("name") for o in f.get("custom_field_options") or []
                    },
                }
            if not result.get("next_page"):
                break
    except (ZendeskAPIError, ZendeskAuthError) as e:
        _warn(warnings, f"Could not read ticket field definitions ({e}); custom fields shown without names")
        return None
    return fields


def _write_cache(path: Path, fields: dict[int, dict], warnings: list[str] | None) -> None:
    try:
        storage.ensure_private_dir(path.parent)
        storage.write_private_text(path, json.dumps({"fetched_at": time.time(), "fields": fields}))
    except OSError as e:
        _warn(warnings, f"Could not cache ticket field definitions ({e}); they will be refetched next time")


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], False)


def _is_miss(cf: dict, fields: dict[int, dict]) -> bool:
    """Does this populated custom field show the definitions are out of date?

    Either its field is unknown, or it is a dropdown whose value is not among the
    known options (a field/option created after the definitions were cached).
    """
    value = cf.get("value")
    if not _has_value(value):
        return False  # unset fields are listed on every ticket; they prove nothing
    info = fields.get(cf.get("id"))
    if info is None:
        return True
    options = info["options"]
    if not options:
        return False
    values = value if isinstance(value, list) else [value]
    return any(v not in options for v in values)


def _refresh_requested() -> bool:
    return os.environ.get("ZD_REFRESH_FIELDS", "").lower() in ("1", "true", "yes")


async def get_ticket_field_map(
    client: ZendeskClient, warnings: list[str] | None = None, force: bool = False
) -> dict[int, dict]:
    """Ticket field definitions (id -> title/type/option labels), cached on disk.

    Every CLI invocation is a new process, so the cache lives in the private
    storage dir. Returns an empty map, with a warning, if the token cannot read
    ticket fields.
    """
    path = _cache_path(client)
    cached = None if force else _read_cache(path)
    if cached is not None:
        return cached[0]
    fields = await _fetch_fields(client, warnings)
    if fields is None:
        return {}
    _write_cache(path, fields, warnings)
    return fields


async def annotate_custom_fields(
    client: ZendeskClient, tickets: list[dict], warnings: list[str] | None = None
) -> None:
    """Add title/type (and value_label for dropdowns) to each ticket's custom_fields, in place.

    Definitions come from the on-disk cache. If a populated field or dropdown
    value is not covered by them (created since they were cached) they are
    refetched - straight-line code, at most ONCE per call, and only if the cache
    is at least MISS_REFETCH_MIN_AGE old, so a field that can never be resolved
    (deleted/inactive) costs at most one request a minute, not one per command.
    ZD_REFRESH_FIELDS=1 forces a refresh (e.g. after a field was renamed).
    """
    if not any(t.get("custom_fields") for t in tickets):
        return

    path = _cache_path(client)
    cached = None if _refresh_requested() else _read_cache(path)
    if cached is not None:
        fields, fetched_at = cached
        fresh = False
    else:
        fetched = await _fetch_fields(client, warnings)
        if fetched is None:
            return
        fields, fresh = fetched, True
        _write_cache(path, fields, warnings)

    if not fresh and time.time() - fetched_at >= MISS_REFETCH_MIN_AGE:
        if any(_is_miss(cf, fields) for t in tickets for cf in t.get("custom_fields") or []):
            refetched = await _fetch_fields(client, warnings)  # on failure keep the cached defs
            if refetched is not None:
                fields, fresh = refetched, True
                _write_cache(path, fields, warnings)

    unknown: set[int] = set()
    for t in tickets:
        for cf in t.get("custom_fields") or []:
            info = fields.get(cf.get("id"))
            if info is None:
                if _has_value(cf.get("value")):
                    unknown.add(cf.get("id"))
                continue
            cf["title"] = info["title"]
            cf["type"] = info["type"]
            options, value = info["options"], cf.get("value")
            if options and isinstance(value, list):
                cf["value_label"] = [options.get(v, v) for v in value]
            elif options and value in options:
                cf["value_label"] = options[value]

    if unknown and fresh:  # only claim "no definition" when the definitions are current
        ids = ", ".join(str(i) for i in sorted(unknown)[:10])
        _warn(warnings, f"No definition found for custom field(s) {ids}; shown without names")


def with_warnings(result: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Attach lookup warnings to an operation result (only when there are any)."""
    if warnings:
        result["warnings"] = warnings
    return result
