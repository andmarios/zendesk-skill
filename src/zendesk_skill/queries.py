"""jq query definitions for Zendesk API responses.

Each tool has predefined useful queries that can be executed via zendesk_query_stored.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

# Named queries organized by tool/response type
QUERIES: dict[str, dict[str, dict[str, str]]] = {
    # Ticket-related queries
    "ticket": {
        "summary": {
            "description": "Get ticket summary (id, subject, status, priority)",
            "query": ".data.ticket | {id, subject, status, priority, created_at, updated_at}",
        },
        "requester": {
            "description": "Get ticket requester info",
            "query": ".data.ticket | {requester_id, submitter_id, organization_id}",
        },
        "tags": {
            "description": "Get ticket tags",
            "query": ".data.ticket.tags",
        },
        "custom_fields": {
            "description": "Get custom field values",
            "query": ".data.ticket.custom_fields | map(select(.value != null))",
        },
    },
    "ticket_details": {
        "ticket_summary": {
            "description": "Get ticket summary without comments",
            "query": ".data.ticket | {id, subject, status, priority, description, created_at, updated_at, tags}",
        },
        "comments_slim": {
            "description": "Get comments with only essential fields (no HTML)",
            "query": ".data.comments | map({id, author_id, body: (.plain_body // .body | .[0:500]), public, created_at})",
        },
        "comments_full": {
            "description": "Get full comment bodies",
            "query": ".data.comments | map({id, author_id, body, public, created_at})",
        },
        "attachments": {
            "description": "List all attachments from comments",
            "query": "[.data.comments[].attachments[]? | {id, file_name, content_url, size, content_type}]",
        },
        "comment_count": {
            "description": "Count comments by type",
            "query": "{total: (.data.comments | length), public: ([.data.comments[] | select(.public == true)] | length), private: ([.data.comments[] | select(.public == false)] | length)}",
        },
        "latest_comment": {
            "description": "Get the most recent comment",
            "query": ".data.comments | last | {id, author_id, body: (.plain_body // .body | .[0:500]), public, created_at}",
        },
        "messages_by_author": {
            "description": "Count messages per author",
            "query": ".data.comments | group_by(.author_id) | map({author_id: .[0].author_id, message_count: length, public_count: ([.[] | select(.public == true)] | length), private_count: ([.[] | select(.public == false)] | length)}) | sort_by(.message_count) | reverse",
        },
        "conversation_stats": {
            "description": "Get conversation statistics",
            "query": "{total_messages: (.data.comments | length), public_messages: ([.data.comments[] | select(.public == true)] | length), private_notes: ([.data.comments[] | select(.public == false)] | length), unique_authors: ([.data.comments[].author_id] | unique | length), requester_id: .data.ticket.requester_id}",
        },
        "call_mentions": {
            "description": "Find comments mentioning calls/phone",
            "query": "[.data.comments[] | select((.plain_body // .body | ascii_downcase) | test(\"call|called|phone|spoke|speaking|conversation|rang|ring\")) | {id, author_id, created_at, snippet: (.plain_body // .body | .[0:200]), public}]",
        },
        "channel_analysis": {
            "description": "Analyze communication channels from comment metadata",
            "query": "[.data.comments[] | {channel: .via.channel, source: .via.source.from}] | group_by(.channel) | map({channel: .[0].channel, count: length})",
        },
    },
    "search": {
        "ids_only": {
            "description": "Get just ticket IDs",
            "query": "[.data.results[].id]",
        },
        "summary_list": {
            "description": "Get summary list of tickets",
            "query": ".data.results | map({id, subject, status, priority, created_at})",
        },
        "by_status": {
            "description": "Group tickets by status",
            "query": ".data.results | group_by(.status) | map({status: .[0].status, count: length, tickets: map(.id)})",
        },
        "by_priority": {
            "description": "Group tickets by priority",
            "query": ".data.results | group_by(.priority) | map({priority: .[0].priority, count: length})",
        },
        "by_requester": {
            "description": "Group tickets by requester (customer)",
            "query": ".data.results | group_by(.requester_id) | map({requester_id: .[0].requester_id, ticket_count: length, tickets: map(.id)}) | sort_by(.ticket_count) | reverse",
        },
        "by_organization": {
            "description": "Group tickets by organization",
            "query": ".data.results | group_by(.organization_id) | map({organization_id: .[0].organization_id, ticket_count: length, tickets: map(.id)}) | sort_by(.ticket_count) | reverse",
        },
        "top_requesters": {
            "description": "Top 10 requesters by ticket count",
            "query": ".data.results | group_by(.requester_id) | map({requester_id: .[0].requester_id, ticket_count: length}) | sort_by(.ticket_count) | reverse | .[0:10]",
        },
        "top_organizations": {
            "description": "Top 10 organizations by ticket count",
            "query": ".data.results | group_by(.organization_id) | map({organization_id: .[0].organization_id, ticket_count: length}) | sort_by(.ticket_count) | reverse | .[0:10]",
        },
        "pagination": {
            "description": "Get pagination info",
            "query": "{count: .data.count, next_page: .data.next_page, previous_page: .data.previous_page}",
        },
    },
    "linked_incidents": {
        "ids_only": {
            "description": "Get just incident IDs",
            "query": "[.data.tickets[].id]",
        },
        "summary_list": {
            "description": "Get summary of linked incidents",
            "query": ".data.tickets | map({id, subject, status, created_at})",
        },
    },
    # User-related queries
    "user": {
        "profile": {
            "description": "Get user profile summary",
            "query": ".data.user | {id, name, email, role, active, created_at}",
        },
        "contact_info": {
            "description": "Get user contact information",
            "query": ".data.user | {name, email, phone, organization_id}",
        },
    },
    "users_search": {
        "list": {
            "description": "Get user list",
            "query": ".data.users | map({id, name, email, role, active})",
        },
        "emails_only": {
            "description": "Get just emails",
            "query": "[.data.users[].email]",
        },
    },
    # Organization queries
    "organization": {
        "summary": {
            "description": "Get organization summary",
            "query": ".data.organization | {id, name, domain_names, created_at}",
        },
    },
    "organizations_search": {
        "list": {
            "description": "Get organization list",
            "query": ".data.organizations | map({id, name, domain_names})",
        },
    },
    # View queries
    "views": {
        "active_views": {
            "description": "Get active views",
            "query": "[.data.views[] | select(.active == true) | {id, title, position}]",
        },
        "all_views": {
            "description": "Get all views",
            "query": ".data.views | map({id, title, active, position})",
        },
    },
    "view_tickets": {
        "ids_only": {
            "description": "Get ticket IDs from view",
            "query": "[.data.tickets[].id]",
        },
        "summary_list": {
            "description": "Get ticket summaries from view",
            "query": ".data.tickets | map({id, subject, status, priority})",
        },
    },
    # Metrics queries
    "ticket_metrics": {
        "kpi_summary": {
            "description": "Get key KPIs (FRT, resolution, wait times)",
            "query": ".data.ticket_metric | {first_reply_time_mins: .reply_time_in_minutes.calendar, first_resolution_mins: .first_resolution_time_in_minutes.calendar, full_resolution_mins: .full_resolution_time_in_minutes.calendar, requester_wait_mins: .requester_wait_time_in_minutes.calendar, agent_wait_mins: .agent_wait_time_in_minutes.calendar, replies: .replies, reopens: .reopens}",
        },
        "times": {
            "description": "Get all time-based metrics (calendar & business hours)",
            "query": ".data.ticket_metric | {reply_time: .reply_time_in_minutes, first_resolution: .first_resolution_time_in_minutes, full_resolution: .full_resolution_time_in_minutes, requester_wait: .requester_wait_time_in_minutes, agent_wait: .agent_wait_time_in_minutes, on_hold: .on_hold_time_in_minutes}",
        },
        "efficiency": {
            "description": "Get efficiency indicators (reopens, replies, stations)",
            "query": ".data.ticket_metric | {reopens, replies, group_stations, assignee_stations}",
        },
        "timestamps": {
            "description": "Get key timestamps",
            "query": ".data.ticket_metric | {created_at, initially_assigned_at, solved_at, latest_comment_added_at}",
        },
    },
    # List metrics (bulk metrics analysis)
    "list_metrics": {
        "frt_summary": {
            "description": "First reply time summary across tickets",
            "query": "[.data.ticket_metrics[] | {ticket_id, frt_mins: .reply_time_in_minutes.calendar}] | sort_by(.frt_mins)",
        },
        "resolution_summary": {
            "description": "Resolution time summary across tickets",
            "query": "[.data.ticket_metrics[] | select(.full_resolution_time_in_minutes.calendar != null) | {ticket_id, resolution_mins: .full_resolution_time_in_minutes.calendar}] | sort_by(.resolution_mins)",
        },
        "avg_frt": {
            "description": "Calculate average first reply time",
            "query": "[.data.ticket_metrics[].reply_time_in_minutes.calendar | select(. != null)] | if length > 0 then {count: length, avg_minutes: (add / length), min: min, max: max} else {count: 0, avg_minutes: null} end",
        },
        "reopen_rate": {
            "description": "Tickets with reopens (potential FCR issues)",
            "query": "[.data.ticket_metrics[] | select(.reopens > 0) | {ticket_id, reopens}]",
        },
        "wait_times": {
            "description": "Requester and agent wait times",
            "query": "[.data.ticket_metrics[] | {ticket_id, requester_wait: .requester_wait_time_in_minutes.calendar, agent_wait: .agent_wait_time_in_minutes.calendar}]",
        },
    },
    # SLA policies
    "sla_policies": {
        "all_targets": {
            "description": "All SLA targets by policy and priority",
            "query": "[.data.sla_policies[] | {title, targets: [.policy_metrics[] | {priority, metric, target_mins: .target}]}]",
        },
        "frt_targets": {
            "description": "First reply time targets only",
            "query": "[.data.sla_policies[] | {title, frt_targets: [.policy_metrics[] | select(.metric == \"first_reply_time\") | {(.priority): .target}] | add}]",
        },
    },
    # Satisfaction ratings
    "satisfaction_ratings": {
        "summary": {
            "description": "Get ratings summary",
            "query": ".data.satisfaction_ratings | map({id, score, ticket_id, created_at, comment})",
        },
        "scores_only": {
            "description": "Get just scores",
            "query": "[.data.satisfaction_ratings[].score]",
        },
        "by_score": {
            "description": "Group by score",
            "query": ".data.satisfaction_ratings | group_by(.score) | map({score: .[0].score, count: length})",
        },
    },
    # Groups and tags
    "groups": {
        "list": {
            "description": "Get groups list",
            "query": ".data.groups | map({id, name, is_public})",
        },
    },
    "tags": {
        "list": {
            "description": "Get tags list",
            "query": ".data.tags | map({name, count})",
        },
    },
}


def get_queries_for_tool(tool_name: str) -> list[dict[str, str]]:
    """Get the list of predefined queries for a tool.

    Args:
        tool_name: Tool name (e.g., 'zendesk_get_ticket_details')

    Returns:
        List of query definitions with name, description, query
    """
    # Map tool names to query categories (supports both MCP and CLI naming)
    tool_to_category = {
        # MCP-style names
        "zendesk_get_ticket": "ticket",
        "zendesk_get_ticket_details": "ticket_details",
        "zendesk_search": "search",
        "zendesk_get_linked_incidents": "linked_incidents",
        "zendesk_get_user": "user",
        "zendesk_search_users": "users_search",
        "zendesk_get_organization": "organization",
        "zendesk_search_organizations": "organizations_search",
        "zendesk_list_views": "views",
        "zendesk_get_view_tickets": "view_tickets",
        "zendesk_get_ticket_metrics": "ticket_metrics",
        "zendesk_list_ticket_metrics": "ticket_metrics",
        "zendesk_get_satisfaction_ratings": "satisfaction_ratings",
        "zendesk_list_groups": "groups",
        "zendesk_list_tags": "tags",
        # CLI-style names (short names from filenames)
        "ticket": "ticket",
        "ticket_details": "ticket_details",
        "search": "search",
        "linked_incidents": "linked_incidents",
        "user": "user",
        "search_users": "users_search",
        "organization": "organization",
        "search_organizations": "organizations_search",
        "views": "views",
        "view_tickets": "view_tickets",
        "ticket_metrics": "ticket_metrics",
        "list_metrics": "list_metrics",
        "sla_policies": "sla_policies",
        "satisfaction_ratings": "satisfaction_ratings",
        "satisfaction_rating": "satisfaction_ratings",
        "groups": "groups",
        "tags": "tags",
    }

    category = tool_to_category.get(tool_name)
    if not category or category not in QUERIES:
        return []

    return [
        {"name": name, "description": q["description"], "query": q["query"]}
        for name, q in QUERIES[category].items()
    ]


def get_query(tool_name: str, query_name: str) -> str | None:
    """Get a specific named query.

    Args:
        tool_name: Tool name
        query_name: Query name

    Returns:
        jq query string or None if not found
    """
    queries = get_queries_for_tool(tool_name)
    for q in queries:
        if q["name"] == query_name:
            return q["query"]
    return None


def execute_jq(file_path: str, jq_query: str) -> tuple[bool, str]:
    """Execute a jq query on a file.

    Args:
        file_path: Path to the JSON file
        jq_query: jq query to execute

    Returns:
        Tuple of (success, result_or_error)
    """
    # Validate file exists
    if not Path(file_path).exists():
        return False, f"File not found: {file_path}"

    try:
        # Run jq subprocess
        result = subprocess.run(
            ["jq", jq_query, file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, f"jq error: {result.stderr.strip()}"

    except FileNotFoundError:
        return False, "jq is not installed. Please install jq to use query functionality."
    except subprocess.TimeoutExpired:
        return False, "jq query timed out after 30 seconds."
    except Exception as e:
        return False, f"Query execution failed: {e}"


def format_query_result(success: bool, result: str, max_length: int = 50000) -> str:
    """Format a query result for display.

    Args:
        success: Whether the query succeeded
        result: Query result or error message
        max_length: Maximum result length before truncation

    Returns:
        Formatted result string
    """
    if not success:
        return f"**Error:** {result}"

    # Try to pretty-print if it's JSON
    try:
        data = json.loads(result)
        formatted = json.dumps(data, indent=2)
    except json.JSONDecodeError:
        formatted = result

    if len(formatted) > max_length:
        formatted = formatted[:max_length] + f"\n\n... (truncated, {len(result) - max_length} more characters)"

    return f"```json\n{formatted}\n```"
