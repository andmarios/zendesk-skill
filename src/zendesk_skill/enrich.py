"""Resolve IDs in Zendesk responses to people and custom field labels.

Ticket and comment payloads only carry numeric user IDs and bare custom field
``{id, value}`` pairs. These helpers annotate the payloads in place so saved
responses (and exports) are readable.

Lookups are best effort - a token without permission must not break a command -
but never silent: every failure is appended to the caller's ``warnings`` list,
which the operations return in their result.
"""

import json
import time
from pathlib import Path
from typing import Any

from zendesk_skill import storage
from zendesk_skill.client import ZendeskAPIError, ZendeskAuthError, ZendeskClient

SHOW_MANY_BATCH_SIZE = 100  # Zendesk show_many accepts at most 100 IDs per call
FIELDS_CACHE_TTL = 3600  # seconds; ticket field definitions rarely change
FIELDS_PAGE_LIMIT = 20  # hard cap: 2000 fields

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


def _load_cached_fields(path: Path) -> dict[int, dict] | None:
    try:
        cached = json.loads(path.read_text())
        if time.time() - cached["fetched_at"] < FIELDS_CACHE_TTL:
            return {int(k): v for k, v in cached["fields"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        pass  # missing/corrupt/stale cache: just refetch
    return None


async def get_ticket_field_map(
    client: ZendeskClient, warnings: list[str] | None = None
) -> dict[int, dict]:
    """Ticket field definitions (id -> title/type/option labels).

    Cached on disk (in the private storage dir) because every CLI invocation is
    a new process. Returns an empty map, with a warning, if the token cannot
    read ticket fields.
    """
    path = _cache_path(client)
    cached = _load_cached_fields(path)
    if cached is not None:
        return cached

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
        return {}

    try:
        storage.ensure_private_dir(path.parent)
        storage.write_private_text(path, json.dumps({"fetched_at": time.time(), "fields": fields}))
    except OSError as e:
        _warn(warnings, f"Could not cache ticket field definitions ({e}); they will be refetched next time")
    return fields


async def annotate_custom_fields(
    client: ZendeskClient, tickets: list[dict], warnings: list[str] | None = None
) -> None:
    """Add title/type (and value_label for dropdowns) to each ticket's custom_fields, in place."""
    if not any(t.get("custom_fields") for t in tickets):
        return
    field_map = await get_ticket_field_map(client, warnings)
    for t in tickets:
        for cf in t.get("custom_fields") or []:
            info = field_map.get(cf.get("id"))
            if not info:
                continue
            cf["title"] = info["title"]
            cf["type"] = info["type"]
            options, value = info["options"], cf.get("value")
            if options and isinstance(value, list):
                cf["value_label"] = [options.get(v, v) for v in value]
            elif options and value in options:
                cf["value_label"] = options[value]


def with_warnings(result: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Attach lookup warnings to an operation result (only when there are any)."""
    if warnings:
        result["warnings"] = warnings
    return result
