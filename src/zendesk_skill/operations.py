"""Shared business logic for Zendesk operations.

This module contains all Zendesk API interaction logic used by both
the CLI and MCP server. All functions are async and return dicts.
"""

import tempfile
from pathlib import Path
from typing import Optional

from zendesk_skill.client import (
    ZendeskClient,
    ZendeskAuthError,
    ZendeskAPIError,
    get_auth_status,
    save_credentials,
    delete_credentials,
    save_slack_config,
    get_slack_config,
    get_slack_status,
    delete_slack_config,
    CONFIG_PATH,
)
from zendesk_skill.storage import save_response
from zendesk_skill.queries import get_queries_for_tool


def _get_client() -> ZendeskClient:
    """Get a new Zendesk client instance."""
    return ZendeskClient()


# =============================================================================
# Ticket Operations
# =============================================================================


async def search_tickets(
    query: str,
    page: int = 1,
    per_page: int = 25,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
    output_path: Optional[str] = None,
) -> dict:
    """Search Zendesk tickets.

    Args:
        query: Search query using Zendesk syntax
        page: Page number (default: 1)
        per_page: Results per page (default: 25, max: 100)
        sort_by: Field to sort by
        sort_order: Sort order (asc/desc)
        output_path: Custom output file path

    Returns:
        Dict with count, results count, file_path, and next_page
    """
    client = _get_client()

    params = {
        "query": f"type:ticket {query}",
        "page": page,
        "per_page": min(per_page, 100),
        "sort_order": sort_order,
    }
    if sort_by:
        params["sort_by"] = sort_by

    result = await client.get("search.json", params=params)
    suggested = get_queries_for_tool("search")
    file_path, stored = save_response(
        "search", {"query": query}, result, suggested, output_path
    )

    return {
        "count": result.get("count", 0),
        "results": len(result.get("results", [])),
        "file_path": str(file_path),
        "next_page": result.get("next_page"),
    }


async def get_ticket(
    ticket_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get a ticket by ID.

    Args:
        ticket_id: The ticket ID
        output_path: Custom output file path

    Returns:
        Dict with ticket summary and file_path
    """
    client = _get_client()

    result = await client.get(f"tickets/{ticket_id}.json")
    suggested = get_queries_for_tool("ticket")
    file_path, _ = save_response(
        "ticket", {"ticket_id": ticket_id}, result, suggested, output_path,
        ticket_id=ticket_id,
    )

    ticket = result.get("ticket", {})
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "file_path": str(file_path),
    }


async def get_ticket_details(
    ticket_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get ticket with all comments and metadata.

    Args:
        ticket_id: The ticket ID
        output_path: Custom output file path

    Returns:
        Dict with ticket summary, comment count, file_path, and suggested queries
    """
    client = _get_client()

    # Get ticket with sideloaded data
    result = await client.get(
        f"tickets/{ticket_id}.json",
        params={"include": "comment_count"}
    )

    # Get comments
    comments_result = await client.get(f"tickets/{ticket_id}/comments.json")

    # Combine
    combined = {
        "ticket": result.get("ticket", {}),
        "comments": comments_result.get("comments", []),
    }

    suggested = get_queries_for_tool("ticket_details")
    file_path, _ = save_response(
        "ticket_details", {"ticket_id": ticket_id}, combined, suggested, output_path,
        ticket_id=ticket_id,
    )

    ticket = combined["ticket"]
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "status": ticket.get("status"),
        "comment_count": len(combined["comments"]),
        "file_path": str(file_path),
        "suggested_queries": ["comments_slim", "attachments", "ticket_summary"],
    }


async def get_linked_incidents(
    ticket_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get incidents linked to a ticket.

    Args:
        ticket_id: The ticket ID
        output_path: Custom output file path

    Returns:
        Dict with ticket_id, incident_count, and file_path
    """
    client = _get_client()

    result = await client.get(f"tickets/{ticket_id}/incidents.json")
    suggested = get_queries_for_tool("linked_incidents")
    file_path, _ = save_response(
        "linked_incidents", {"ticket_id": ticket_id}, result, suggested, output_path,
        ticket_id=ticket_id,
    )

    return {
        "ticket_id": ticket_id,
        "incident_count": len(result.get("tickets", [])),
        "file_path": str(file_path),
    }


async def download_attachment(
    content_url: str,
    ticket_id: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict:
    """Download an attachment.

    Args:
        content_url: The attachment content URL
        ticket_id: Optional ticket ID for organizing downloads
        output_path: Custom output file path (overrides ticket_id)

    Returns:
        Dict with downloaded status, file_path, and size_bytes
    """
    from urllib.parse import urlparse, parse_qs, unquote

    client = _get_client()

    # Determine output path
    if output_path:
        out_path = Path(output_path)
    else:
        # Extract filename from URL query parameter (?name=filename.txt)
        parsed = urlparse(content_url)
        query_params = parse_qs(parsed.query)

        if "name" in query_params:
            filename = unquote(query_params["name"][0])
        else:
            # Fallback: try to get from path
            filename = parsed.path.split("/")[-1]
            if not filename:
                filename = "attachment"

        # Determine directory based on ticket_id (cross-platform)
        base_dir = Path(tempfile.gettempdir()) / "zendesk-skill"
        if ticket_id:
            attachments_dir = base_dir / ticket_id / "attachments"
        else:
            attachments_dir = base_dir / "attachments"

        attachments_dir.mkdir(parents=True, exist_ok=True)

        # Handle duplicate filenames by adding suffix
        out_path = attachments_dir / filename
        if out_path.exists():
            stem = out_path.stem
            suffix = out_path.suffix
            counter = 1
            while out_path.exists():
                out_path = attachments_dir / f"{stem}_{counter}{suffix}"
                counter += 1

    result_path = await client.download_file(content_url, out_path)

    return {
        "downloaded": True,
        "file_path": str(result_path),
        "size_bytes": result_path.stat().st_size,
    }


# =============================================================================
# Write Operations
# =============================================================================


async def update_ticket(
    ticket_id: str,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assignee_id: Optional[str] = None,
    subject: Optional[str] = None,
    tags: Optional[list[str]] = None,
    ticket_type: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict:
    """Update a ticket's properties.

    Args:
        ticket_id: The ticket ID
        status: New status
        priority: New priority
        assignee_id: New assignee ID
        subject: New subject
        tags: New tags (replaces existing)
        ticket_type: New ticket type
        output_path: Custom output file path

    Returns:
        Dict with updated status, id, status, and file_path

    Raises:
        ValueError: If no update fields provided
    """
    client = _get_client()

    # Build update payload
    ticket_data: dict = {}
    if status:
        ticket_data["status"] = status
    if priority:
        ticket_data["priority"] = priority
    if assignee_id:
        ticket_data["assignee_id"] = int(assignee_id)
    if subject:
        ticket_data["subject"] = subject
    if tags is not None:
        ticket_data["tags"] = tags
    if ticket_type:
        ticket_data["type"] = ticket_type

    if not ticket_data:
        raise ValueError("No update fields provided")

    result = await client.put(
        f"tickets/{ticket_id}.json",
        json_data={"ticket": ticket_data}
    )
    file_path, _ = save_response(
        "update_ticket", {"ticket_id": ticket_id}, result, [], output_path,
        ticket_id=ticket_id,
    )

    ticket = result.get("ticket", {})
    return {
        "updated": True,
        "id": ticket.get("id"),
        "status": ticket.get("status"),
        "file_path": str(file_path),
    }


async def create_ticket(
    subject: str,
    description: str,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    tags: Optional[list[str]] = None,
    ticket_type: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict:
    """Create a new ticket.

    Args:
        subject: Ticket subject
        description: Ticket description/first comment
        priority: Priority level
        status: Initial status
        tags: Tags to add
        ticket_type: Ticket type
        output_path: Custom output file path

    Returns:
        Dict with created status, id, subject, and file_path
    """
    client = _get_client()

    ticket_data: dict = {
        "subject": subject,
        "comment": {"body": description},
    }
    if priority:
        ticket_data["priority"] = priority
    if status:
        ticket_data["status"] = status
    if tags:
        ticket_data["tags"] = tags
    if ticket_type:
        ticket_data["type"] = ticket_type

    result = await client.post("tickets.json", json_data={"ticket": ticket_data})
    ticket = result.get("ticket", {})
    new_ticket_id = str(ticket.get("id")) if ticket.get("id") else None
    file_path, _ = save_response(
        "create_ticket", {"subject": subject}, result, [], output_path,
        ticket_id=new_ticket_id,
    )
    return {
        "created": True,
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "file_path": str(file_path),
    }


async def _add_ticket_comment(
    ticket_id: str,
    body: str,
    public: bool,
    output_path: Optional[str] = None,
) -> dict:
    """Add a comment to a ticket (internal helper).

    Args:
        ticket_id: The ticket ID
        body: Comment content
        public: Whether the comment is public
        output_path: Custom output file path

    Returns:
        Dict with added status, ticket_id, public flag, and file_path
    """
    client = _get_client()

    result = await client.put(
        f"tickets/{ticket_id}.json",
        json_data={
            "ticket": {
                "comment": {
                    "body": body,
                    "public": public,
                }
            }
        }
    )
    tool_name = "add_comment" if public else "add_note"
    file_path, _ = save_response(
        tool_name, {"ticket_id": ticket_id}, result, [], output_path,
        ticket_id=ticket_id,
    )

    return {
        "added": True,
        "ticket_id": ticket_id,
        "public": public,
        "file_path": str(file_path),
    }


async def add_private_note(
    ticket_id: str,
    note: str,
    output_path: Optional[str] = None,
) -> dict:
    """Add a private internal note to a ticket.

    Args:
        ticket_id: The ticket ID
        note: Note content
        output_path: Custom output file path

    Returns:
        Dict with added status, ticket_id, public flag, and file_path
    """
    return await _add_ticket_comment(ticket_id, note, public=False, output_path=output_path)


async def add_public_comment(
    ticket_id: str,
    comment: str,
    output_path: Optional[str] = None,
) -> dict:
    """Add a public comment to a ticket.

    Args:
        ticket_id: The ticket ID
        comment: Comment content
        output_path: Custom output file path

    Returns:
        Dict with added status, ticket_id, public flag, and file_path
    """
    return await _add_ticket_comment(ticket_id, comment, public=True, output_path=output_path)


# =============================================================================
# Metrics & Analytics
# =============================================================================


async def get_ticket_metrics(
    ticket_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get metrics for a ticket.

    Args:
        ticket_id: The ticket ID
        output_path: Custom output file path

    Returns:
        Dict with ticket_id, metrics summary, and file_path
    """
    client = _get_client()

    result = await client.get(f"tickets/{ticket_id}/metrics.json")
    suggested = get_queries_for_tool("ticket_metrics")
    file_path, _ = save_response(
        "ticket_metrics", {"ticket_id": ticket_id}, result, suggested, output_path,
        ticket_id=ticket_id,
    )

    metrics = result.get("ticket_metric", {})

    # Extract time-based metrics (calendar time in minutes)
    def get_time(field: str) -> int | None:
        val = metrics.get(field, {})
        return val.get("calendar") if isinstance(val, dict) else None

    return {
        "ticket_id": ticket_id,
        "replies": metrics.get("replies"),
        "reopens": metrics.get("reopens"),
        # Key time metrics (in minutes, calendar time)
        "first_reply_time": get_time("reply_time_in_minutes"),
        "first_resolution_time": get_time("first_resolution_time_in_minutes"),
        "full_resolution_time": get_time("full_resolution_time_in_minutes"),
        "requester_wait_time": get_time("requester_wait_time_in_minutes"),
        "agent_wait_time": get_time("agent_wait_time_in_minutes"),
        "on_hold_time": get_time("on_hold_time_in_minutes"),
        "file_path": str(file_path),
    }


async def list_ticket_metrics(
    page: int = 1,
    per_page: int = 25,
    output_path: Optional[str] = None,
) -> dict:
    """List ticket metrics.

    Args:
        page: Page number
        per_page: Results per page
        output_path: Custom output file path

    Returns:
        Dict with count and file_path
    """
    client = _get_client()

    result = await client.get(
        "ticket_metrics.json",
        params={"page": page, "per_page": per_page}
    )
    suggested = get_queries_for_tool("list_metrics")
    file_path, _ = save_response("list_metrics", {}, result, suggested, output_path)

    return {
        "count": len(result.get("ticket_metrics", [])),
        "file_path": str(file_path),
    }


async def get_satisfaction_ratings(
    score: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    per_page: int = 25,
    output_path: Optional[str] = None,
) -> dict:
    """List CSAT satisfaction ratings.

    Args:
        score: Filter by score (good, bad, offered, unoffered)
        start_time: Start time (Unix timestamp)
        end_time: End time (Unix timestamp)
        page: Page number
        per_page: Results per page
        output_path: Custom output file path

    Returns:
        Dict with count and file_path
    """
    client = _get_client()

    params: dict = {"page": page, "per_page": per_page}
    if score:
        params["score"] = score
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time

    result = await client.get("satisfaction_ratings.json", params=params)
    suggested = get_queries_for_tool("satisfaction_ratings")
    file_path, _ = save_response(
        "satisfaction_ratings", params, result, suggested, output_path
    )

    return {
        "count": len(result.get("satisfaction_ratings", [])),
        "file_path": str(file_path),
    }


async def get_satisfaction_rating(
    rating_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get a single satisfaction rating.

    Args:
        rating_id: The rating ID
        output_path: Custom output file path

    Returns:
        Dict with rating details and file_path
    """
    client = _get_client()

    result = await client.get(f"satisfaction_ratings/{rating_id}.json")
    file_path, _ = save_response(
        "satisfaction_rating", {"rating_id": rating_id}, result, [], output_path
    )

    rating = result.get("satisfaction_rating", {})
    return {
        "id": rating.get("id"),
        "score": rating.get("score"),
        "ticket_id": rating.get("ticket_id"),
        "file_path": str(file_path),
    }


# =============================================================================
# Views
# =============================================================================


async def list_views(
    active: Optional[bool] = None,
    output_path: Optional[str] = None,
) -> dict:
    """List available views.

    Args:
        active: Filter active views only
        output_path: Custom output file path

    Returns:
        Dict with count, views summary, and file_path
    """
    client = _get_client()

    params = {}
    if active is not None:
        params["active"] = active

    result = await client.get("views.json", params=params if params else None)
    suggested = get_queries_for_tool("views")
    file_path, _ = save_response("views", params, result, suggested, output_path)

    views = result.get("views", [])
    return {
        "count": len(views),
        "views": [{"id": v.get("id"), "title": v.get("title")} for v in views[:10]],
        "file_path": str(file_path),
    }


async def get_view_count(
    view_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get ticket count for a view.

    Args:
        view_id: The view ID
        output_path: Custom output file path

    Returns:
        Dict with view_id, count, fresh flag, and file_path
    """
    client = _get_client()

    result = await client.get(f"views/{view_id}/count.json")
    file_path, _ = save_response(
        "view_count", {"view_id": view_id}, result, [], output_path
    )

    count_data = result.get("view_count", {})
    return {
        "view_id": view_id,
        "count": count_data.get("value"),
        "fresh": count_data.get("fresh"),
        "file_path": str(file_path),
    }


async def get_view_tickets(
    view_id: str,
    page: int = 1,
    per_page: int = 25,
    output_path: Optional[str] = None,
) -> dict:
    """Get tickets from a view.

    Args:
        view_id: The view ID
        page: Page number
        per_page: Results per page
        output_path: Custom output file path

    Returns:
        Dict with view_id, count, and file_path
    """
    client = _get_client()

    result = await client.get(
        f"views/{view_id}/tickets.json",
        params={"page": page, "per_page": per_page}
    )
    suggested = get_queries_for_tool("view_tickets")
    file_path, _ = save_response(
        "view_tickets", {"view_id": view_id}, result, suggested, output_path
    )

    return {
        "view_id": view_id,
        "count": len(result.get("tickets", [])),
        "file_path": str(file_path),
    }


# =============================================================================
# Users & Organizations
# =============================================================================


async def get_user(
    user_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get a user by ID.

    Args:
        user_id: The user ID
        output_path: Custom output file path

    Returns:
        Dict with user details and file_path
    """
    client = _get_client()

    result = await client.get(f"users/{user_id}.json")
    suggested = get_queries_for_tool("user")
    file_path, _ = save_response(
        "user", {"user_id": user_id}, result, suggested, output_path
    )

    user = result.get("user", {})
    return {
        "id": user.get("id"),
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "file_path": str(file_path),
    }


async def search_users(
    query: str,
    output_path: Optional[str] = None,
) -> dict:
    """Search users by name or email.

    Args:
        query: Search query
        output_path: Custom output file path

    Returns:
        Dict with count, users summary, and file_path
    """
    client = _get_client()

    result = await client.get("users/search.json", params={"query": query})
    suggested = get_queries_for_tool("search_users")
    file_path, _ = save_response(
        "search_users", {"query": query}, result, suggested, output_path
    )

    users = result.get("users", [])
    return {
        "count": len(users),
        "users": [
            {"id": u.get("id"), "name": u.get("name"), "email": u.get("email")}
            for u in users[:10]
        ],
        "file_path": str(file_path),
    }


async def get_organization(
    org_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Get an organization by ID.

    Args:
        org_id: The organization ID
        output_path: Custom output file path

    Returns:
        Dict with organization details and file_path
    """
    client = _get_client()

    result = await client.get(f"organizations/{org_id}.json")
    suggested = get_queries_for_tool("organization")
    file_path, _ = save_response(
        "organization", {"org_id": org_id}, result, suggested, output_path
    )

    org = result.get("organization", {})
    return {
        "id": org.get("id"),
        "name": org.get("name"),
        "domain_names": org.get("domain_names"),
        "file_path": str(file_path),
    }


async def search_organizations(
    query: str,
    output_path: Optional[str] = None,
) -> dict:
    """Search organizations.

    Args:
        query: Search query
        output_path: Custom output file path

    Returns:
        Dict with count, organizations summary, and file_path
    """
    client = _get_client()

    result = await client.get("organizations/search.json", params={"query": query})
    suggested = get_queries_for_tool("search_organizations")
    file_path, _ = save_response(
        "search_organizations", {"query": query}, result, suggested, output_path
    )

    orgs = result.get("organizations", [])
    return {
        "count": len(orgs),
        "organizations": [{"id": o.get("id"), "name": o.get("name")} for o in orgs[:10]],
        "file_path": str(file_path),
    }


# =============================================================================
# Configuration
# =============================================================================


async def list_groups(
    output_path: Optional[str] = None,
) -> dict:
    """List support groups.

    Args:
        output_path: Custom output file path

    Returns:
        Dict with count, groups summary, and file_path
    """
    client = _get_client()

    result = await client.get("groups.json")
    suggested = get_queries_for_tool("groups")
    file_path, _ = save_response("groups", {}, result, suggested, output_path)

    groups = result.get("groups", [])
    return {
        "count": len(groups),
        "groups": [{"id": g.get("id"), "name": g.get("name")} for g in groups],
        "file_path": str(file_path),
    }


async def list_tags(
    output_path: Optional[str] = None,
) -> dict:
    """List popular tags.

    Args:
        output_path: Custom output file path

    Returns:
        Dict with count, tags summary, and file_path
    """
    client = _get_client()

    result = await client.get("tags.json")
    suggested = get_queries_for_tool("tags")
    file_path, _ = save_response("tags", {}, result, suggested, output_path)

    tags = result.get("tags", [])
    return {
        "count": len(tags),
        "tags": [t.get("name") for t in tags[:20]],
        "file_path": str(file_path),
    }


async def list_sla_policies(
    output_path: Optional[str] = None,
) -> dict:
    """List SLA policies.

    Args:
        output_path: Custom output file path

    Returns:
        Dict with count, policies summary, and file_path
    """
    client = _get_client()

    result = await client.get("slas/policies.json")
    suggested = get_queries_for_tool("sla_policies")
    file_path, _ = save_response("sla_policies", {}, result, suggested, output_path)

    policies = result.get("sla_policies", [])

    # Extract policy summaries with key targets
    def summarize_policy(p: dict) -> dict:
        summary = {"id": p.get("id"), "title": p.get("title")}
        # Extract first reply time targets per priority
        metrics = p.get("policy_metrics", [])
        targets = {}
        for m in metrics:
            if m.get("metric") == "first_reply_time":
                priority = m.get("priority", "unknown")
                target_mins = m.get("target")
                targets[priority] = target_mins
        if targets:
            summary["first_reply_targets_mins"] = targets
        return summary

    return {
        "count": len(policies),
        "policies": [summarize_policy(p) for p in policies],
        "file_path": str(file_path),
    }


async def get_current_user(
    output_path: Optional[str] = None,
) -> dict:
    """Get current authenticated user (test auth).

    Args:
        output_path: Custom output file path

    Returns:
        Dict with authenticated flag, user details, and file_path
    """
    client = _get_client()

    result = await client.get("users/me.json")
    file_path, _ = save_response("me", {}, result, [], output_path)

    user = result.get("user", {})
    return {
        "authenticated": True,
        "id": user.get("id"),
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "file_path": str(file_path),
    }


# =============================================================================
# Authentication Operations
# =============================================================================


async def check_auth_status(validate: bool = True) -> dict:
    """Check authentication configuration status.

    Args:
        validate: Whether to validate credentials by making an API call

    Returns:
        Dict with:
            - configured: bool
            - source: str | None ("env", "config", or None)
            - config_path: str
            - env_vars_set: list of set env vars
            - has_config_file: bool
            - user: dict | None (if validate=True and auth works)
            - error: str | None (if validate=True and auth fails)
            - guidance: str | None (if not configured)
    """
    status = get_auth_status()

    result = {
        **status,
        "user": None,
        "error": None,
        "guidance": None,
    }

    if not status["configured"]:
        result["guidance"] = (
            "No Zendesk credentials configured. Set up using:\n"
            "1. CLI: zendesk auth login\n"
            "2. Environment variables: ZENDESK_EMAIL, ZENDESK_TOKEN, ZENDESK_SUBDOMAIN\n"
            f"3. Config file: {status['config_path']}"
        )
        return result

    if validate:
        try:
            client = _get_client()
            api_result = await client.get("users/me.json")
            user = api_result.get("user", {})
            result["user"] = {
                "id": user.get("id"),
                "name": user.get("name"),
                "email": user.get("email"),
                "role": user.get("role"),
            }
        except ZendeskAuthError as e:
            result["error"] = str(e)
        except ZendeskAPIError as e:
            result["error"] = str(e)
        except Exception as e:
            result["error"] = f"Unexpected error: {e}"

    return result


async def auth_login(
    email: str,
    token: str,
    subdomain: str,
) -> dict:
    """Validate and save Zendesk credentials.

    Args:
        email: Zendesk email
        token: Zendesk API token
        subdomain: Zendesk subdomain

    Returns:
        Dict with success status, user info, and config path
    """
    # Validate credentials by creating a client and making an API call
    try:
        client = ZendeskClient(email=email, token=token, subdomain=subdomain)
        result = await client.get("users/me.json")
        user = result.get("user", {})
    except ZendeskAuthError as e:
        return {
            "success": False,
            "error": str(e),
            "user": None,
            "config_path": None,
        }
    except ZendeskAPIError as e:
        return {
            "success": False,
            "error": str(e),
            "user": None,
            "config_path": None,
        }

    # Credentials are valid, save them
    config_path = save_credentials(email, token, subdomain)

    return {
        "success": True,
        "error": None,
        "user": {
            "id": user.get("id"),
            "name": user.get("name"),
            "email": user.get("email"),
            "role": user.get("role"),
        },
        "config_path": str(config_path),
    }


def auth_logout() -> dict:
    """Remove saved credentials.

    Returns:
        Dict with deleted status, config path, and warning about env vars
    """
    status = get_auth_status()
    deleted = delete_credentials()

    result = {
        "deleted": deleted,
        "config_path": str(CONFIG_PATH),
        "warning": None,
    }

    if status["env_vars_set"]:
        result["warning"] = (
            f"Environment variables still set: {', '.join(status['env_vars_set'])}. "
            "These will continue to provide authentication."
        )

    return result


# =============================================================================
# Slack Integration
# =============================================================================


async def slack_login(webhook_url: str, channel: str) -> dict:
    """Validate and save Slack webhook configuration.

    Args:
        webhook_url: Slack incoming webhook URL
        channel: Default Slack channel (e.g., #channel-name)

    Returns:
        Dict with success status and config path
    """
    import httpx

    # Validate by sending a test message
    if not channel.startswith("#"):
        channel = f"#{channel}"

    test_payload = {
        "channel": channel,
        "text": "✅ Zendesk CLI Slack integration configured successfully!",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=test_payload,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            if response.text != "ok":
                return {
                    "success": False,
                    "error": f"Slack API error: {response.text}",
                    "config_path": None,
                }
    except httpx.RequestError as e:
        return {
            "success": False,
            "error": f"Failed to connect to Slack: {e}",
            "config_path": None,
        }

    # Webhook is valid, save config
    config_path = save_slack_config(webhook_url, channel)

    return {
        "success": True,
        "error": None,
        "channel": channel,
        "config_path": str(config_path),
    }


def check_slack_status() -> dict:
    """Get Slack configuration status.

    Returns:
        Dict with configuration status
    """
    status = get_slack_status()
    return {
        "configured": status["configured"],
        "source": status["source"],
        "channel": status["channel"],
        "env_vars_set": status["env_vars_set"],
        "has_config": status["has_config"],
        "config_path": str(CONFIG_PATH),
    }


def slack_logout() -> dict:
    """Remove Slack configuration.

    Returns:
        Dict with deleted status
    """
    status = get_slack_status()
    deleted = delete_slack_config()

    result = {
        "deleted": deleted,
        "config_path": str(CONFIG_PATH),
        "warning": None,
    }

    if status["env_vars_set"]:
        result["warning"] = (
            f"Environment variables still set: {', '.join(status['env_vars_set'])}. "
            "These will continue to provide Slack configuration."
        )

    return result


def _mins_to_human(mins: float | None) -> str:
    """Convert minutes to human-readable format."""
    if mins is None:
        return "N/A"
    if mins < 60:
        return f"{int(mins)}m"
    elif mins < 1440:
        return f"{mins/60:.1f}h"
    else:
        return f"{mins/1440:.1f}d"


async def send_slack_report(
    report_data: dict,
    channel: str | None = None,
    webhook_url: str | None = None,
) -> dict:
    """Send a support metrics report to Slack.

    Args:
        report_data: Dict with ticket_analysis, customer_stats, summary,
                     status_breakdown, priority_breakdown, frt_stats, resolution_stats
        channel: Override channel (uses config if not provided)
        webhook_url: Override webhook URL (uses config if not provided)

    Returns:
        Dict with success status
    """
    import httpx

    # Get config if not provided
    if not webhook_url or not channel:
        config = get_slack_config()
        if not config:
            return {
                "success": False,
                "error": "Slack not configured. Run 'zendesk auth login-slack' first.",
            }
        webhook_url = webhook_url or config[0]
        channel = channel or config[1]

    if not channel.startswith("#"):
        channel = f"#{channel}"

    # Extract report data
    summary = report_data.get("summary", {})
    customer_stats = report_data.get("customer_stats", {})
    ticket_analysis = report_data.get("ticket_analysis", [])
    status_breakdown = report_data.get("status_breakdown", {})
    priority_breakdown = report_data.get("priority_breakdown", {})
    frt_stats = report_data.get("frt_stats", {})
    resolution_stats = report_data.get("resolution_stats", {})
    reopen_count = report_data.get("reopen_count", 0)
    period = report_data.get("period", {})
    business_hours = report_data.get("business_hours", {})
    oncall_data = report_data.get("oncall", {})
    oncall_engagements = oncall_data.get("engagements", []) if oncall_data else []
    oncall_config = oncall_data.get("config", {}) if oncall_data else {}

    total_tickets = summary.get("total_tickets", 0)

    # Build customer fields (max 10 per section)
    customer_fields = []
    sorted_customers = sorted(
        customer_stats.items(),
        key=lambda x: x[1].get("tickets", 0),
        reverse=True,
    )[:6]  # Top 6 customers

    for customer, stats in sorted_customers:
        tickets = stats.get("tickets", 0)
        messages = stats.get("messages", 0)
        calls = stats.get("calls", 0)
        call_str = f" · {calls} call{'s' if calls != 1 else ''}" if calls else ""
        customer_fields.append({
            "type": "mrkdwn",
            "text": f"*{customer}*\n{tickets} ticket{'s' if tickets != 1 else ''} · {messages} msgs{call_str}",
        })

    # Build top tickets list
    sorted_tickets = sorted(
        ticket_analysis,
        key=lambda x: x.get("messages", 0),
        reverse=True,
    )[:10]  # Top 10

    ticket_lines = []
    for t in sorted_tickets:
        call_emoji = " 📞" if t.get("has_calls") else ""
        subject = (t.get("subject") or "")[:35]
        ticket_lines.append(
            f"• *#{t.get('ticket_id')}* – {t.get('messages', 0)} msgs{call_emoji} – _{subject}_"
        )

    # Build period string
    period_text = ""
    if period:
        start = period.get("start_date", "")
        end = period.get("end_date", "")
        days = period.get("days", 0)
        if start and end:
            period_text = f"📅 Period: {start} – {end} ({days} days)"

    # Build blocks
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 Support Metrics Report",
                "emoji": True,
            },
        },
    ]

    # Add period subtitle if available
    if period_text:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": period_text}],
        })

    blocks.extend([
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📈 Overview*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Total Tickets:*\n{total_tickets}"},
                {"type": "mrkdwn", "text": f"*Total Messages:*\n{summary.get('total_messages', 0)}"},
                {"type": "mrkdwn", "text": f"*Tickets w/ Calls:*\n{summary.get('tickets_with_calls', 0)}"},
                {"type": "mrkdwn", "text": f"*Unique Customers:*\n{summary.get('unique_customers', 0)}"},
            ],
        },
        {"type": "divider"},
    ])

    # Add FRT & Resolution metrics if available
    if frt_stats or resolution_stats:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*⏱️ Response & Resolution Metrics*"},
        })

        metrics_fields = []
        if frt_stats:
            metrics_fields.extend([
                {"type": "mrkdwn", "text": f"*Avg FRT:*\n{_mins_to_human(frt_stats.get('avg_mins'))}"},
                {"type": "mrkdwn", "text": f"*Median FRT:*\n{_mins_to_human(frt_stats.get('median_mins'))}"},
                {"type": "mrkdwn", "text": f"*FRT Range:*\n{_mins_to_human(frt_stats.get('min_mins'))} – {_mins_to_human(frt_stats.get('max_mins'))}"},
            ])
        if resolution_stats:
            resolved = resolution_stats.get("count", 0)
            metrics_fields.extend([
                {"type": "mrkdwn", "text": f"*Avg Resolution:*\n{_mins_to_human(resolution_stats.get('avg_mins'))}"},
                {"type": "mrkdwn", "text": f"*Resolved:*\n{resolved}/{total_tickets} ({100*resolved//total_tickets if total_tickets else 0}%)"},
            ])
        reopen_pct = 100 * reopen_count // total_tickets if total_tickets else 0
        metrics_fields.append({"type": "mrkdwn", "text": f"*Reopen Rate:*\n{reopen_pct}%"})

        blocks.append({"type": "section", "fields": metrics_fields[:6]})  # Max 6 fields
        blocks.append({"type": "divider"})

    # Add status breakdown if available
    if status_breakdown:
        status_icons = {"pending": "🟡", "open": "🔴", "closed": "⚫", "solved": "🟢", "hold": "🟠"}
        status_fields = []
        for status, count in sorted(status_breakdown.items(), key=lambda x: x[1], reverse=True):
            icon = status_icons.get(status, "⚪")
            status_fields.append({"type": "mrkdwn", "text": f"{icon} *{status.title()}:* {count}"})

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📋 Status Breakdown*"},
        })
        blocks.append({"type": "section", "fields": status_fields[:4]})

    # Add priority breakdown if available
    if priority_breakdown:
        priority_icons = {"urgent": "🔴", "high": "🟠", "normal": "🟡", "low": "🟢"}
        priority_fields = []
        for priority, count in sorted(priority_breakdown.items(), key=lambda x: ["urgent", "high", "normal", "low"].index(x[0]) if x[0] in ["urgent", "high", "normal", "low"] else 99):
            icon = priority_icons.get(priority, "⚪")
            pct = 100 * count // total_tickets if total_tickets else 0
            priority_fields.append({"type": "mrkdwn", "text": f"{icon} *{priority.title()}:* {count} ({pct}%)"})

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🚨 Priority Breakdown*"},
        })
        blocks.append({"type": "section", "fields": priority_fields[:4]})
        blocks.append({"type": "divider"})

    # Add business hours section (only if configured)
    if business_hours:
        bh_cfg = business_hours.get("config", {})
        start_h = bh_cfg.get("start_hour", 9)
        end_h = bh_cfg.get("end_hour", 18)
        tz_name = bh_cfg.get("timezone", "CET")

        tickets_ooh = business_hours.get("tickets_outside_hours", 0)
        cust_msgs_ooh = business_hours.get("customer_msgs_outside_hours", 0)
        support_ooh = business_hours.get("support_replies_outside_hours", 0)

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🕐 Outside Business Hours* ({start_h} AM - {end_h % 12 or 12} PM {tz_name})"},
        })
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Tickets Created:*\n{tickets_ooh}"},
                {"type": "mrkdwn", "text": f"*Customer Messages:*\n{cust_msgs_ooh}"},
                {"type": "mrkdwn", "text": f"*Support Replies:*\n{support_ooh}"},
            ],
        })

    # Add on-call engagements (only if configured and present)
    if oncall_engagements:
        oncall_start = oncall_config.get("start_hour", 19)
        oncall_end = oncall_config.get("end_hour", 9)
        oncall_customers = oncall_config.get("customers", [])
        customer_desc = ", ".join(oncall_customers) if oncall_customers else "all customers"

        oncall_lines = []
        for eng in oncall_engagements[:5]:  # Max 5
            oncall_lines.append(f"• *#{eng.get('ticket_id')}* – {eng.get('created_at_local', 'N/A')} – {eng.get('customer', '')} – _{eng.get('subject', '')[:25]}_")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🚨 On-Call Engagements* ({oncall_start % 12 or 12} PM - {oncall_end} AM or weekends, {customer_desc}): {len(oncall_engagements)}"},
        })
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(oncall_lines)},
        })
        blocks.append({"type": "divider"})

    # Add customer stats
    blocks.extend([
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🏢 Tickets & Messages per Customer*"},
        },
        {
            "type": "section",
            "fields": customer_fields,
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🎫 Top Tickets by Activity*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(ticket_lines)},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "📞 = Call detected (best-effort from keywords) | Generated by Zendesk CLI Skill",
                },
            ],
        },
    ])

    payload = {"channel": channel, "blocks": blocks}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            if response.text != "ok":
                return {
                    "success": False,
                    "error": f"Slack API error: {response.text}",
                }
    except httpx.RequestError as e:
        return {
            "success": False,
            "error": f"Failed to send to Slack: {e}",
        }

    return {
        "success": True,
        "channel": channel,
        "message": "Report sent to Slack successfully.",
    }
