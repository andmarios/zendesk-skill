"""Zendesk MCP Server - Thin wrapper around operations module."""

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from zendesk_skill import operations
from zendesk_skill.client import ZendeskAuthError, ZendeskAPIError
from zendesk_skill.queries import execute_jq, get_query
from zendesk_skill.storage import load_response

# Initialize the MCP server
mcp = FastMCP("zendesk_skill")


# =============================================================================
# Pydantic Input Models
# =============================================================================

# Shared model config
_MODEL_CONFIG = ConfigDict(str_strip_whitespace=True)


class OutputOnlyInput(BaseModel):
    """Base input with only output path."""
    model_config = _MODEL_CONFIG
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class TicketIdInput(BaseModel):
    """Input for single ticket operations."""
    model_config = _MODEL_CONFIG
    ticket_id: str = Field(..., description="The ID of the ticket", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class ViewIdInput(BaseModel):
    """Input for view operations."""
    model_config = _MODEL_CONFIG
    view_id: str = Field(..., description="View ID", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class UserIdInput(BaseModel):
    """Input for user operations."""
    model_config = _MODEL_CONFIG
    user_id: str = Field(..., description="User ID", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class OrgIdInput(BaseModel):
    """Input for organization operations."""
    model_config = _MODEL_CONFIG
    organization_id: str = Field(..., description="Organization ID", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class RatingIdInput(BaseModel):
    """Input for single rating."""
    model_config = _MODEL_CONFIG
    rating_id: str = Field(..., description="Rating ID", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class SearchQueryInput(BaseModel):
    """Input for simple search operations (users, orgs)."""
    model_config = _MODEL_CONFIG
    query: str = Field(..., description="Search query", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class SearchInput(BaseModel):
    """Input for ticket search with pagination."""
    model_config = _MODEL_CONFIG
    query: str = Field(..., description="Search query", min_length=1)
    page: int = Field(default=1, ge=1, description="Page number")
    per_page: int = Field(default=25, ge=1, le=100, description="Results per page")
    sort_by: Optional[str] = Field(default=None, description="Sort field")
    sort_order: str = Field(default="desc", description="Sort order")
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class PaginatedInput(BaseModel):
    """Input for paginated listing operations."""
    model_config = _MODEL_CONFIG
    page: int = Field(default=1, ge=1, description="Page number")
    per_page: int = Field(default=25, ge=1, le=100, description="Results per page")
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class ViewTicketsInput(BaseModel):
    """Input for view tickets with pagination."""
    model_config = _MODEL_CONFIG
    view_id: str = Field(..., description="View ID", min_length=1)
    page: int = Field(default=1, ge=1, description="Page number")
    per_page: int = Field(default=25, ge=1, le=100, description="Results per page")
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class AttachmentInput(BaseModel):
    """Input for attachment download."""
    model_config = _MODEL_CONFIG
    content_url: str = Field(..., description="The attachment content URL")
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class TicketUpdateInput(BaseModel):
    """Input for ticket updates."""
    model_config = _MODEL_CONFIG
    ticket_id: str = Field(..., description="The ticket ID", min_length=1)
    status: Optional[str] = Field(default=None, description="New status")
    priority: Optional[str] = Field(default=None, description="New priority")
    assignee_id: Optional[str] = Field(default=None, description="Assignee ID")
    subject: Optional[str] = Field(default=None, description="New subject")
    tags: Optional[list[str]] = Field(default=None, description="Tags to set")
    type: Optional[str] = Field(default=None, description="Ticket type")
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class TicketCreateInput(BaseModel):
    """Input for ticket creation."""
    model_config = _MODEL_CONFIG
    subject: str = Field(..., description="Ticket subject", min_length=1)
    description: str = Field(..., description="Ticket description", min_length=1)
    status: Optional[str] = Field(default=None, description="Status")
    priority: Optional[str] = Field(default=None, description="Priority")
    tags: Optional[list[str]] = Field(default=None, description="Tags")
    type: Optional[str] = Field(default=None, description="Ticket type")
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class NoteInput(BaseModel):
    """Input for adding notes to tickets."""
    model_config = _MODEL_CONFIG
    ticket_id: str = Field(..., description="Ticket ID", min_length=1)
    note: str = Field(..., description="Note content", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class CommentInput(BaseModel):
    """Input for adding comments to tickets."""
    model_config = _MODEL_CONFIG
    ticket_id: str = Field(..., description="Ticket ID", min_length=1)
    comment: str = Field(..., description="Comment content", min_length=1)
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class QueryStoredInput(BaseModel):
    """Input for querying stored files."""
    model_config = _MODEL_CONFIG
    file_path: str = Field(..., description="Path to stored JSON file")
    query: Optional[str] = Field(default=None, description="Named query")
    custom_jq: Optional[str] = Field(default=None, description="Custom jq expression")


class SatisfactionRatingsInput(BaseModel):
    """Input for satisfaction ratings query."""
    model_config = _MODEL_CONFIG
    score: Optional[str] = Field(default=None, description="Filter by score")
    start_time: Optional[str] = Field(default=None, description="Start time")
    end_time: Optional[str] = Field(default=None, description="End time")
    page: int = Field(default=1, ge=1, description="Page number")
    per_page: int = Field(default=25, ge=1, le=100, description="Results per page")
    output_path: Optional[str] = Field(default=None, description="Custom output path")


class AuthStatusInput(BaseModel):
    """Input for auth status check."""
    model_config = _MODEL_CONFIG
    validate_credentials: bool = Field(
        default=True,
        description="Whether to validate credentials by making an API call"
    )


# =============================================================================
# Helper Functions
# =============================================================================


def _format_result(result: dict) -> str:
    """Format operation result as JSON string."""
    return json.dumps(result, indent=2, default=str)


def _handle_error(e: Exception) -> str:
    """Format errors consistently."""
    if isinstance(e, ZendeskAuthError):
        return f"**Authentication Error:** {e}"
    elif isinstance(e, ZendeskAPIError):
        return f"**API Error:** {e}"
    else:
        return f"**Error:** {type(e).__name__}: {e}"


# =============================================================================
# Ticket Tools
# =============================================================================


@mcp.tool(name="zendesk_get_ticket")
async def zendesk_get_ticket(params: TicketIdInput) -> str:
    """Get a Zendesk ticket by ID."""
    try:
        result = await operations.get_ticket(params.ticket_id, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_search")
async def zendesk_search(params: SearchInput) -> str:
    """Search for Zendesk tickets based on a query with pagination support."""
    try:
        result = await operations.search_tickets(
            params.query, params.page, params.per_page,
            params.sort_by, params.sort_order, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_ticket_details")
async def zendesk_get_ticket_details(params: TicketIdInput) -> str:
    """Get detailed information about a Zendesk ticket including comments."""
    try:
        result = await operations.get_ticket_details(params.ticket_id, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_linked_incidents")
async def zendesk_get_linked_incidents(params: TicketIdInput) -> str:
    """Fetch all incident tickets linked to a particular ticket."""
    try:
        result = await operations.get_linked_incidents(params.ticket_id, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_attachment")
async def zendesk_get_attachment(params: AttachmentInput) -> str:
    """Download an attachment from Zendesk and save it locally."""
    try:
        result = await operations.download_attachment(params.content_url, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Write Operations
# =============================================================================


@mcp.tool(name="zendesk_update_ticket")
async def zendesk_update_ticket(params: TicketUpdateInput) -> str:
    """Update a Zendesk ticket's properties."""
    try:
        result = await operations.update_ticket(
            params.ticket_id, params.status, params.priority,
            params.assignee_id, params.subject, params.tags,
            params.type, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_create_ticket")
async def zendesk_create_ticket(params: TicketCreateInput) -> str:
    """Create a new Zendesk ticket."""
    try:
        result = await operations.create_ticket(
            params.subject, params.description, params.priority,
            params.status, params.tags, params.type, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_add_private_note")
async def zendesk_add_private_note(params: NoteInput) -> str:
    """Add a private internal note to a Zendesk ticket."""
    try:
        result = await operations.add_private_note(
            params.ticket_id, params.note, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_add_public_note")
async def zendesk_add_public_note(params: CommentInput) -> str:
    """Add a public comment to a Zendesk ticket."""
    try:
        result = await operations.add_public_comment(
            params.ticket_id, params.comment, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Query Tool
# =============================================================================


@mcp.tool(name="zendesk_query_stored")
async def zendesk_query_stored(params: QueryStoredInput) -> str:
    """Query a stored Zendesk response file using jq."""
    try:
        stored = load_response(params.file_path)
        tool_name = stored.get("metadata", {}).get("tool", "")

        if params.custom_jq:
            jq_query = params.custom_jq
        elif params.query:
            named = get_query(tool_name, params.query)
            jq_query = named if named else params.query
        else:
            return "**Error:** Either query or custom_jq must be provided"

        success, result = execute_jq(params.file_path, jq_query)
        if success:
            return result
        else:
            return f"**Error:** {result}"

    except FileNotFoundError:
        return f"**Error:** File not found: {params.file_path}"
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Metrics Tools
# =============================================================================


@mcp.tool(name="zendesk_get_ticket_metrics")
async def zendesk_get_ticket_metrics(params: TicketIdInput) -> str:
    """Get metrics for a ticket (reply time, resolution time, etc.)."""
    try:
        result = await operations.get_ticket_metrics(params.ticket_id, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_list_ticket_metrics")
async def zendesk_list_ticket_metrics(params: PaginatedInput) -> str:
    """List metrics for multiple tickets."""
    try:
        result = await operations.list_ticket_metrics(
            params.page, params.per_page, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_satisfaction_ratings")
async def zendesk_get_satisfaction_ratings(params: SatisfactionRatingsInput) -> str:
    """List CSAT ratings with optional filters."""
    try:
        result = await operations.get_satisfaction_ratings(
            params.score, params.start_time, params.end_time,
            params.page, params.per_page, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_satisfaction_rating")
async def zendesk_get_satisfaction_rating(params: RatingIdInput) -> str:
    """Get a single satisfaction rating by ID."""
    try:
        result = await operations.get_satisfaction_rating(
            params.rating_id, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Views Tools
# =============================================================================


@mcp.tool(name="zendesk_list_views")
async def zendesk_list_views(params: OutputOnlyInput) -> str:
    """List available Zendesk views."""
    try:
        result = await operations.list_views(output_path=params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_view_count")
async def zendesk_get_view_count(params: ViewIdInput) -> str:
    """Get the ticket count for a view."""
    try:
        result = await operations.get_view_count(params.view_id, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_view_tickets")
async def zendesk_get_view_tickets(params: ViewTicketsInput) -> str:
    """Get tickets from a specific view."""
    try:
        result = await operations.get_view_tickets(
            params.view_id, params.page, params.per_page, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Users & Organizations Tools
# =============================================================================


@mcp.tool(name="zendesk_get_user")
async def zendesk_get_user(params: UserIdInput) -> str:
    """Get a user by ID."""
    try:
        result = await operations.get_user(params.user_id, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_search_users")
async def zendesk_search_users(params: SearchQueryInput) -> str:
    """Search users by name or email."""
    try:
        result = await operations.search_users(params.query, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_organization")
async def zendesk_get_organization(params: OrgIdInput) -> str:
    """Get an organization by ID."""
    try:
        result = await operations.get_organization(
            params.organization_id, params.output_path
        )
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_search_organizations")
async def zendesk_search_organizations(params: SearchQueryInput) -> str:
    """Search organizations by name."""
    try:
        result = await operations.search_organizations(params.query, params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Config Tools
# =============================================================================


@mcp.tool(name="zendesk_list_groups")
async def zendesk_list_groups(params: OutputOnlyInput) -> str:
    """List support groups."""
    try:
        result = await operations.list_groups(params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_list_tags")
async def zendesk_list_tags(params: OutputOnlyInput) -> str:
    """List popular tags in the account."""
    try:
        result = await operations.list_tags(params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_list_sla_policies")
async def zendesk_list_sla_policies(params: OutputOnlyInput) -> str:
    """List SLA policies."""
    try:
        result = await operations.list_sla_policies(params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="zendesk_get_current_user")
async def zendesk_get_current_user(params: OutputOnlyInput) -> str:
    """Get the authenticated user (me). Useful for testing authentication."""
    try:
        result = await operations.get_current_user(params.output_path)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Auth Tools
# =============================================================================


@mcp.tool(name="zendesk_auth_status")
async def zendesk_auth_status(params: AuthStatusInput) -> str:
    """Check Zendesk authentication status.

    Returns current auth configuration source (env vars, config file, or none),
    validates credentials if requested, and provides setup guidance if not configured.
    """
    try:
        result = await operations.check_auth_status(validate=params.validate_credentials)
        return _format_result(result)
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Server Entry Point
# =============================================================================


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
