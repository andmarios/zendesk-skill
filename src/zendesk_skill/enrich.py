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
from zendesk_skill.client import ZendeskClient, ZendeskClientError

SHOW_MANY_BATCH_SIZE = 100  # Zendesk show_many accepts at most 100 IDs per call
FIELDS_CACHE_TTL = 3600  # seconds; ticket field definitions rarely change
FIELDS_PER_PAGE = 100
FIELDS_PAGE_LIMIT = 20  # hard cap: 2000 fields
MISS_REFETCH_MIN_AGE = 60  # seconds; floor between refetches triggered by a cache miss

# API, auth and transport failures, plus ValueError for a body that is not JSON
# (e.g. a proxy's HTML error page served with a 200)
LOOKUP_ERRORS = (ZendeskClientError, ValueError)

# (ticket ids key, annotated key)
PEOPLE_LISTS = (
    ("email_cc_ids", "email_cc_users"),
    ("follower_ids", "follower_users"),
)


def _warn(warnings: list[str] | None, message: str) -> None:
    if warnings is not None and message not in warnings:
        warnings.append(message)


async def _get_object(client: ZendeskClient, endpoint: str, params: dict) -> dict:
    """GET that insists on a JSON object, so malformed bodies fail as a lookup error."""
    result = await client.get(endpoint, params=params)
    if not isinstance(result, dict):
        raise ValueError(f"unexpected {type(result).__name__} response from {endpoint}")
    return result


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def users_from_sideload(*results: Any) -> dict[int, dict]:
    """Collect {id: {name, email}} from the ``users`` sideload of API responses."""
    users: dict[int, dict] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        sideloaded = result.get("users")
        for u in sideloaded if isinstance(sideloaded, list) else []:
            if isinstance(u, dict) and isinstance(u.get("id"), int) and (u.get("name") or u.get("email")):
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
    ids = sorted({i for i in user_ids if isinstance(i, int) and i > 0} - set(users))
    for start in range(0, len(ids), SHOW_MANY_BATCH_SIZE):
        batch = ids[start:start + SHOW_MANY_BATCH_SIZE]
        try:
            result = await _get_object(
                client, "users/show_many.json", {"ids": ",".join(str(i) for i in batch)}
            )
        except LOOKUP_ERRORS as e:
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
    """Cached definitions and when they were fetched, or None if missing/malformed.

    Age is the caller's call: an expired cache is still the fallback when a
    refetch fails. Every entry is validated (a file from another version or a
    partial write must never reach the labelling code); anything wrong just
    means "no cache".
    """
    try:
        cached = json.loads(path.read_text())
        fetched_at = float(cached["fetched_at"])
        fields = {int(k): v for k, v in cached["fields"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not all(_valid_definition(v) for v in fields.values()):
        return None
    return fields, fetched_at


async def _fetch_fields(client: ZendeskClient, warnings: list[str] | None) -> dict[int, dict]:
    """Fetch all ticket field definitions (bounded paging).

    Raises one of LOOKUP_ERRORS on failure; warns (rather than silently caching
    a partial set as complete) if the page cap cuts the list short.
    """
    fields: dict[int, dict] = {}
    for page in range(1, FIELDS_PAGE_LIMIT + 1):
        result = await _get_object(
            client, "ticket_fields.json", {"page": page, "per_page": FIELDS_PER_PAGE}
        )
        for f in result.get("ticket_fields") or []:
            if not isinstance(f, dict) or not isinstance(f.get("id"), int):
                continue
            fields[f["id"]] = {
                "title": f.get("title") or f.get("raw_title"),
                "type": f.get("type"),
                "options": {
                    o.get("value"): o.get("name")
                    for o in f.get("custom_field_options") or []
                    if isinstance(o, dict)
                },
            }
        if not result.get("next_page"):
            break
    else:
        _warn(
            warnings,
            f"Only the first {FIELDS_PAGE_LIMIT * FIELDS_PER_PAGE} ticket field definitions were read; "
            "fields beyond that are shown without names",
        )
    return fields


def _write_cache(path: Path, fields: dict[int, dict], warnings: list[str] | None) -> None:
    try:
        directory = storage.ensure_private_dir(path.parent)  # use the path it secured, not the raw one
        storage.write_private_text(directory / path.name, json.dumps({"fetched_at": time.time(), "fields": fields}))
    except OSError as e:
        _warn(warnings, f"Could not cache ticket field definitions ({e}); they will be refetched next time")


def _has_value(value: Any) -> bool:
    """Is this custom field populated? 0 is a real value; None, "", [] and an unticked checkbox are not.

    Not ``value not in (None, "", [], False)``: 0 == False, so that drops numeric zeroes.
    """
    if value is None or value is False:
        return False
    return not (isinstance(value, (str, list)) and not value)


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


async def _load_fields(
    client: ZendeskClient, tickets: list[dict], warnings: list[str] | None
) -> tuple[dict[int, dict], bool] | None:
    """Field definitions for these tickets and whether they were just fetched.

    Every cache decision lives here:
    - a cache younger than FIELDS_CACHE_TTL is used as is (a future-dated one,
      written while the clock was ahead, counts as stale), unless
      ZD_REFRESH_FIELDS=1 is set, or a ticket uses a field or dropdown option it
      doesn't know (created since it was cached) and it is at least
      MISS_REFETCH_MIN_AGE old - so a field that can never be resolved
      (deleted/inactive) costs at most one request a minute, not one per command;
    - otherwise the definitions are fetched - straight-line code, at most ONCE
      per call - and cached;
    - if that fetch fails, any valid cache (even an expired one) is used, with a
      warning: slightly stale labels beat none.

    Returns None, with a warning, only when there is nothing to label with.
    """
    path = _cache_path(client)
    cached = _read_cache(path)
    if cached is not None and not _refresh_requested():
        fields, fetched_at = cached
        age = time.time() - fetched_at
        if 0 <= age < FIELDS_CACHE_TTL and not (
            age >= MISS_REFETCH_MIN_AGE
            and any(_is_miss(cf, fields) for t in tickets for cf in t.get("custom_fields") or [])
        ):
            return fields, False

    try:
        fetched = await _fetch_fields(client, warnings)
    except LOOKUP_ERRORS as e:
        if cached is None:
            _warn(warnings, f"Could not read ticket field definitions ({e}); custom fields shown without names")
            return None
        _warn(warnings, f"Could not read ticket field definitions ({e}); using the cached ones")
        return cached[0], False
    _write_cache(path, fetched, warnings)
    return fetched, True


async def annotate_custom_fields(
    client: ZendeskClient, tickets: list[dict], warnings: list[str] | None = None
) -> None:
    """Add title/type (and value_label for dropdowns) to each ticket's custom_fields, in place.

    Definitions come from the on-disk cache (every CLI invocation is a new
    process); see _load_fields for when they are refetched.
    """
    if not any(t.get("custom_fields") for t in tickets):
        return
    loaded = await _load_fields(client, tickets, warnings)
    if loaded is None:
        return
    fields, fresh = loaded

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


# ---------------------------------------------------------------------------
# Entry point for operations
# ---------------------------------------------------------------------------


async def enrich_tickets(
    client: ZendeskClient,
    tickets: list[dict],
    comments: list[dict] | None = None,
    sideloads: tuple[Any, ...] = (),
) -> list[str]:
    """Annotate tickets (and comments) with people and custom field labels; return the warnings.

    ``sideloads`` are the API responses fetched with a users sideload. Their
    users are used first (sparing show_many calls) and then REMOVED from the
    response: the full user records (phone, notes, signature, ...) are neither
    saved nor left unscanned - the names copied onto the tickets are what gets
    stored, and those are on the scan list.

    Enrichment runs before the response is saved, so it must never fail the
    command: anything a step did not anticipate becomes a warning.
    """
    warnings: list[str] = []
    known = users_from_sideload(*sideloads)
    for response in sideloads:
        if isinstance(response, dict):
            response.pop("users", None)
    try:
        await annotate_people(client, tickets=tickets, comments=comments, known_users=known, warnings=warnings)
    except Exception as e:  # best effort by design: reported, not swallowed
        _warn(warnings, f"Could not resolve user names ({type(e).__name__}: {e}); showing IDs only")
    try:
        await annotate_custom_fields(client, tickets, warnings)
    except Exception as e:  # best effort by design: reported, not swallowed
        _warn(warnings, f"Could not label custom fields ({type(e).__name__}: {e}); shown without names")
    return warnings


def with_warnings(result: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Attach lookup warnings to an operation result (only when there are any)."""
    if warnings:
        result["warnings"] = warnings
    return result
