"""Zendesk CLI - Thin wrapper around operations module."""

import asyncio
import json
import sys
from functools import wraps
from pathlib import Path
from typing import Annotated, Callable

import typer

from zendesk_skill import __version__
from zendesk_skill import operations
from zendesk_skill.client import ZendeskClientError
from zendesk_skill.queries import execute_jq, get_queries_for_tool, get_query

# Main app
app = typer.Typer(
    name="zendesk",
    help="Zendesk CLI - Search tickets, manage support, and analyze metrics.",
    no_args_is_help=True,
    add_completion=False,
)

# Auth subcommand group
auth_app = typer.Typer(
    help="Authentication management - configure and test Zendesk credentials.",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")


def output_json(data: dict) -> None:
    """Output JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


def output_error(message: str, exit_code: int = 1) -> None:
    """Output error and exit."""
    print(json.dumps({"error": message}), file=sys.stderr)
    raise typer.Exit(exit_code)


def run_async(coro):
    """Run async coroutine synchronously."""
    return asyncio.run(coro)


def zendesk_command(func: Callable) -> Callable:
    """Decorator to handle common error patterns for Zendesk CLI commands."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (ZendeskClientError, ValueError) as e:
            output_error(str(e))
    return wrapper


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        output_json({"version": __version__})
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version", "-v",
            help="Show version and exit.",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """Zendesk CLI - Manage tickets, users, and support metrics."""


# =============================================================================
# Auth Commands
# =============================================================================


@auth_app.command("login")
def auth_login_cmd(
    email: Annotated[
        str | None,
        typer.Option("--email", "-e", help="Zendesk email address"),
    ] = None,
    token: Annotated[
        str | None,
        typer.Option("--token", "-t", help="Zendesk API token"),
    ] = None,
    subdomain: Annotated[
        str | None,
        typer.Option("--subdomain", "-s", help="Zendesk subdomain (e.g., 'company' for company.zendesk.com)"),
    ] = None,
) -> None:
    """Configure Zendesk authentication.

    Interactive mode (no options): prompts for credentials.
    Non-interactive mode (all options): validates and saves credentials.
    """
    # Determine mode
    all_provided = all([email, token, subdomain])
    none_provided = not any([email, token, subdomain])

    if not all_provided and not none_provided:
        output_error("Either provide all credentials (--email, --token, --subdomain) or none for interactive mode.")

    # Interactive mode
    if none_provided:
        typer.echo("Configure Zendesk authentication\n")
        email = typer.prompt("Email")
        token = typer.prompt("API Token", hide_input=True)
        subdomain = typer.prompt("Subdomain (e.g., 'company' for company.zendesk.com)")

    # Validate and save
    typer.echo("\nValidating credentials...")
    result = run_async(operations.auth_login(email, token, subdomain))

    if result["success"]:
        user = result["user"]
        output_json({
            "success": True,
            "message": f"Authenticated as {user['name']} ({user['email']})",
            "user": user,
            "config_path": result["config_path"],
        })
    else:
        output_error(f"Authentication failed: {result['error']}")


@auth_app.command("status")
def auth_status_cmd() -> None:
    """Check current authentication status.

    Shows credential source (env vars, config file, or none) and validates them.
    """
    result = run_async(operations.check_auth_status(validate=True))

    status = {
        "configured": result["configured"],
        "source": result["source"],
        "config_path": result["config_path"],
        "env_vars_set": result["env_vars_set"],
        "has_config_file": result["has_config_file"],
    }

    if result["user"]:
        status["user"] = result["user"]
        status["authenticated"] = True
    elif result["error"]:
        status["authenticated"] = False
        status["error"] = result["error"]
    elif result["guidance"]:
        status["authenticated"] = False
        status["guidance"] = result["guidance"]

    output_json(status)


@auth_app.command("logout")
def auth_logout_cmd() -> None:
    """Remove saved credentials from config file.

    Note: Does not affect environment variables if set.
    """
    result = operations.auth_logout()

    output = {
        "deleted": result["deleted"],
        "config_path": result["config_path"],
    }

    if result["deleted"]:
        output["message"] = "Credentials removed from config file."
    else:
        output["message"] = "No config file found to delete."

    if result["warning"]:
        output["warning"] = result["warning"]

    output_json(output)


@auth_app.command("login-slack")
def auth_login_slack_cmd(
    webhook_url: Annotated[
        str | None,
        typer.Option("--webhook", "-w", help="Slack incoming webhook URL"),
    ] = None,
    channel: Annotated[
        str | None,
        typer.Option("--channel", "-c", help="Default Slack channel (e.g., #channel-name)"),
    ] = None,
) -> None:
    """Configure Slack webhook for sending reports.

    Interactive mode (no options): prompts for configuration.
    Non-interactive mode (all options): validates and saves configuration.
    """
    all_provided = all([webhook_url, channel])
    none_provided = not any([webhook_url, channel])

    if not all_provided and not none_provided:
        output_error("Either provide all options (--webhook, --channel) or none for interactive mode.")

    if none_provided:
        typer.echo("Configure Slack integration\n")
        webhook_url = typer.prompt("Webhook URL")
        channel = typer.prompt("Default channel (e.g., #general)")

    typer.echo("\nValidating Slack webhook...")
    result = run_async(operations.slack_login(webhook_url, channel))

    if result["success"]:
        output_json({
            "success": True,
            "message": f"Slack configured for channel {result['channel']}",
            "channel": result["channel"],
            "config_path": result["config_path"],
        })
    else:
        output_error(f"Slack configuration failed: {result['error']}")


@auth_app.command("status-slack")
def auth_status_slack_cmd() -> None:
    """Check Slack configuration status."""
    result = operations.check_slack_status()
    output_json(result)


@auth_app.command("logout-slack")
def auth_logout_slack_cmd() -> None:
    """Remove Slack configuration from config file."""
    result = operations.slack_logout()

    output = {
        "deleted": result["deleted"],
        "config_path": result["config_path"],
    }

    if result["deleted"]:
        output["message"] = "Slack configuration removed."
    else:
        output["message"] = "No Slack configuration found."

    if result["warning"]:
        output["warning"] = result["warning"]

    output_json(output)


# =============================================================================
# Ticket Commands
# =============================================================================


@app.command("search")
@zendesk_command
def search_cmd(
    query: Annotated[str, typer.Argument(help="Zendesk search query")],
    page: Annotated[int, typer.Option("--page", "-p", help="Page number")] = 1,
    per_page: Annotated[int, typer.Option("--per-page", "-n", help="Results per page")] = 25,
    sort_by: Annotated[str | None, typer.Option("--sort", "-s", help="Sort field")] = None,
    sort_order: Annotated[str, typer.Option("--order", "-o", help="Sort order")] = "desc",
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Search Zendesk tickets using query syntax."""
    result = run_async(operations.search_tickets(
        query, page, per_page, sort_by, sort_order, output_path
    ))
    output_json(result)


@app.command("ticket")
@zendesk_command
def ticket_cmd(
    ticket_id: Annotated[str, typer.Argument(help="Ticket ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get a ticket by ID."""
    result = run_async(operations.get_ticket(ticket_id, output_path))
    output_json(result)


@app.command("ticket-details")
@zendesk_command
def ticket_details_cmd(
    ticket_id: Annotated[str, typer.Argument(help="Ticket ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get ticket with all comments and metadata."""
    result = run_async(operations.get_ticket_details(ticket_id, output_path))
    output_json(result)


@app.command("linked-incidents")
@zendesk_command
def linked_incidents_cmd(
    ticket_id: Annotated[str, typer.Argument(help="Ticket ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get incidents linked to a ticket."""
    result = run_async(operations.get_linked_incidents(ticket_id, output_path))
    output_json(result)


@app.command("attachment")
@zendesk_command
def attachment_cmd(
    content_url: Annotated[str, typer.Argument(help="Attachment content URL")],
    ticket_id: Annotated[str | None, typer.Option("--ticket", "-t", help="Ticket ID (organizes downloads by ticket)")] = None,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path (overrides --ticket)")] = None,
) -> None:
    """Download an attachment."""
    result = run_async(operations.download_attachment(content_url, ticket_id, output_path))
    output_json(result)


# =============================================================================
# Write Operations
# =============================================================================


@app.command("update-ticket")
@zendesk_command
def update_ticket_cmd(
    ticket_id: Annotated[str, typer.Argument(help="Ticket ID")],
    status: Annotated[str | None, typer.Option("--status", "-s", help="New status")] = None,
    priority: Annotated[str | None, typer.Option("--priority", "-p", help="New priority")] = None,
    assignee_id: Annotated[str | None, typer.Option("--assignee", "-a", help="Assignee ID")] = None,
    subject: Annotated[str | None, typer.Option("--subject", help="New subject")] = None,
    tags: Annotated[str | None, typer.Option("--tags", "-t", help="Tags (comma-separated)")] = None,
    ticket_type: Annotated[str | None, typer.Option("--type", help="Ticket type")] = None,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Update a ticket's properties."""
    tags_list = [t.strip() for t in tags.split(",")] if tags else None
    result = run_async(operations.update_ticket(
        ticket_id, status, priority, assignee_id, subject, tags_list, ticket_type, output_path
    ))
    output_json(result)


@app.command("create-ticket")
@zendesk_command
def create_ticket_cmd(
    subject: Annotated[str, typer.Argument(help="Ticket subject")],
    description: Annotated[str, typer.Argument(help="Ticket description (Markdown supported)")],
    priority: Annotated[str | None, typer.Option("--priority", "-p", help="Priority")] = None,
    status: Annotated[str | None, typer.Option("--status", "-s", help="Status")] = None,
    tags: Annotated[str | None, typer.Option("--tags", "-t", help="Tags (comma-separated)")] = None,
    ticket_type: Annotated[str | None, typer.Option("--type", help="Ticket type")] = None,
    plain_text: Annotated[bool, typer.Option("--plain-text", help="Send as plain text instead of Markdown")] = False,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Create a new ticket. Description supports Markdown formatting by default."""
    tags_list = [t.strip() for t in tags.split(",")] if tags else None
    result = run_async(operations.create_ticket(
        subject, description, priority, status, tags_list, ticket_type, output_path,
        plain_text=plain_text,
    ))
    output_json(result)


@app.command("add-note")
@zendesk_command
def add_note_cmd(
    ticket_id: Annotated[str, typer.Argument(help="Ticket ID")],
    note: Annotated[str, typer.Argument(help="Note content (Markdown supported)")],
    plain_text: Annotated[bool, typer.Option("--plain-text", help="Send as plain text instead of Markdown")] = False,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Add a private internal note to a ticket. Supports Markdown formatting by default."""
    result = run_async(operations.add_private_note(
        ticket_id, note, output_path, plain_text=plain_text,
    ))
    output_json(result)


@app.command("add-comment")
@zendesk_command
def add_comment_cmd(
    ticket_id: Annotated[str, typer.Argument(help="Ticket ID")],
    comment: Annotated[str, typer.Argument(help="Comment content (Markdown supported)")],
    plain_text: Annotated[bool, typer.Option("--plain-text", help="Send as plain text instead of Markdown")] = False,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Add a public comment to a ticket. Supports Markdown formatting by default."""
    result = run_async(operations.add_public_comment(
        ticket_id, comment, output_path, plain_text=plain_text,
    ))
    output_json(result)


# =============================================================================
# Metrics & Analytics
# =============================================================================


@app.command("ticket-metrics")
@zendesk_command
def ticket_metrics_cmd(
    ticket_id: Annotated[str, typer.Argument(help="Ticket ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get metrics for a ticket."""
    result = run_async(operations.get_ticket_metrics(ticket_id, output_path))
    output_json(result)


@app.command("list-metrics")
@zendesk_command
def list_metrics_cmd(
    page: Annotated[int, typer.Option("--page", "-p", help="Page number")] = 1,
    per_page: Annotated[int, typer.Option("--per-page", "-n", help="Results per page")] = 25,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """List ticket metrics."""
    result = run_async(operations.list_ticket_metrics(page, per_page, output_path))
    output_json(result)


@app.command("satisfaction-ratings")
@zendesk_command
def satisfaction_ratings_cmd(
    score: Annotated[str | None, typer.Option("--score", "-s", help="Filter by score")] = None,
    start_time: Annotated[str | None, typer.Option("--start", help="Start time")] = None,
    end_time: Annotated[str | None, typer.Option("--end", help="End time")] = None,
    page: Annotated[int, typer.Option("--page", "-p", help="Page number")] = 1,
    per_page: Annotated[int, typer.Option("--per-page", "-n", help="Results per page")] = 25,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """List CSAT satisfaction ratings."""
    result = run_async(operations.get_satisfaction_ratings(
        score, start_time, end_time, page, per_page, output_path
    ))
    output_json(result)


@app.command("satisfaction-rating")
@zendesk_command
def satisfaction_rating_cmd(
    rating_id: Annotated[str, typer.Argument(help="Rating ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get a single satisfaction rating."""
    result = run_async(operations.get_satisfaction_rating(rating_id, output_path))
    output_json(result)


# =============================================================================
# Views
# =============================================================================


@app.command("views")
@zendesk_command
def views_cmd(
    active: Annotated[bool | None, typer.Option("--active/--all", help="Filter active views")] = None,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """List available views."""
    result = run_async(operations.list_views(active, output_path))
    output_json(result)


@app.command("view-count")
@zendesk_command
def view_count_cmd(
    view_id: Annotated[str, typer.Argument(help="View ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get ticket count for a view."""
    result = run_async(operations.get_view_count(view_id, output_path))
    output_json(result)


@app.command("view-tickets")
@zendesk_command
def view_tickets_cmd(
    view_id: Annotated[str, typer.Argument(help="View ID")],
    page: Annotated[int, typer.Option("--page", "-p", help="Page number")] = 1,
    per_page: Annotated[int, typer.Option("--per-page", "-n", help="Results per page")] = 25,
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get tickets from a view."""
    result = run_async(operations.get_view_tickets(view_id, page, per_page, output_path))
    output_json(result)


# =============================================================================
# Users & Organizations
# =============================================================================


@app.command("user")
@zendesk_command
def user_cmd(
    user_id: Annotated[str, typer.Argument(help="User ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get a user by ID."""
    result = run_async(operations.get_user(user_id, output_path))
    output_json(result)


@app.command("search-users")
@zendesk_command
def search_users_cmd(
    query: Annotated[str, typer.Argument(help="Search query")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Search users by name or email."""
    result = run_async(operations.search_users(query, output_path))
    output_json(result)


@app.command("org")
@zendesk_command
def org_cmd(
    org_id: Annotated[str, typer.Argument(help="Organization ID")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get an organization by ID."""
    result = run_async(operations.get_organization(org_id, output_path))
    output_json(result)


@app.command("search-orgs")
@zendesk_command
def search_orgs_cmd(
    query: Annotated[str, typer.Argument(help="Search query")],
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Search organizations."""
    result = run_async(operations.search_organizations(query, output_path))
    output_json(result)


# =============================================================================
# Configuration
# =============================================================================


@app.command("groups")
@zendesk_command
def groups_cmd(
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """List support groups."""
    result = run_async(operations.list_groups(output_path))
    output_json(result)


@app.command("tags")
@zendesk_command
def tags_cmd(
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """List popular tags."""
    result = run_async(operations.list_tags(output_path))
    output_json(result)


@app.command("sla-policies")
@zendesk_command
def sla_policies_cmd(
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """List SLA policies."""
    result = run_async(operations.list_sla_policies(output_path))
    output_json(result)


@app.command("me")
@zendesk_command
def me_cmd(
    output_path: Annotated[str | None, typer.Option("--output", help="Output path")] = None,
) -> None:
    """Get current authenticated user (test auth)."""
    result = run_async(operations.get_current_user(output_path))
    output_json(result)


# =============================================================================
# Query Stored Files
# =============================================================================


@app.command("query")
def query_cmd(
    file_path: Annotated[str, typer.Argument(help="Path to stored JSON file")],
    query: Annotated[str | None, typer.Option("--query", "-q", help="Named query")] = None,
    custom_jq: Annotated[str | None, typer.Option("--jq", help="Custom jq expression")] = None,
    list_queries: Annotated[bool, typer.Option("--list", "-l", help="List available queries")] = False,
) -> None:
    """Query a stored JSON file using jq."""
    path = Path(file_path)
    if not path.exists():
        output_error(f"File not found: {file_path}")

    # Detect tool from filename
    # Filename format: {tool}_{hash}_{timestamp}.json
    # Tool names can be compound (e.g., ticket_details, ticket_metrics, sla_policies)
    filename = path.stem

    # Known compound tool names (order matters - check longer matches first)
    compound_tools = [
        "ticket_details", "ticket_metrics", "linked_incidents",
        "list_metrics", "sla_policies", "satisfaction_ratings",
        "satisfaction_rating", "search_users", "search_organizations",
        "view_tickets", "view_count", "update_ticket", "create_ticket",
        "add_note", "add_comment",
    ]

    tool_name = None
    for tool in compound_tools:
        if filename.startswith(tool + "_") or filename == tool:
            tool_name = tool
            break

    # Fall back to first segment for simple tool names
    if not tool_name:
        tool_name = filename.split("_")[0] if "_" in filename else filename

    if list_queries:
        queries = get_queries_for_tool(tool_name)
        output_json({
            "tool": tool_name,
            "available_queries": [
                {"name": q["name"], "description": q["description"]}
                for q in queries
            ]
        })
        return

    # Determine jq expression
    jq_expr = custom_jq
    if not jq_expr and query:
        named = get_query(tool_name, query)
        if named:
            jq_expr = named
        else:
            jq_expr = query

    if not jq_expr:
        jq_expr = ".data"

    success, result = execute_jq(str(path), jq_expr)
    if not success:
        output_error(f"Query failed: {result}")

    try:
        parsed = json.loads(result)
        output_json({"result": parsed})
    except json.JSONDecodeError:
        print(result)


# =============================================================================
# Slack Integration
# =============================================================================


@app.command("slack-report")
@zendesk_command
def slack_report_cmd(
    analysis_file: Annotated[
        str | None,
        typer.Argument(help="Path to support_analysis.json file"),
    ] = None,
    channel: Annotated[
        str | None,
        typer.Option("--channel", "-c", help="Override Slack channel"),
    ] = None,
) -> None:
    """Send support metrics report to Slack.

    Uses the analysis file generated by the analyze script, or searches
    for the most recent support_analysis.json in the temp directory.
    """
    import tempfile

    # Find analysis file
    if analysis_file:
        path = Path(analysis_file)
    else:
        # Search for most recent analysis file
        base_dir = Path(tempfile.gettempdir()) / "zendesk-skill"
        candidates = list(base_dir.glob("**/support_analysis.json"))
        if not candidates:
            output_error(
                "No analysis file found. Run the analysis first or provide a file path.\n"
                "Example: python src/zendesk_skill/scripts/analyze_support_metrics.py"
            )
        path = max(candidates, key=lambda f: f.stat().st_mtime)

    if not path.exists():
        output_error(f"File not found: {path}")

    # Load analysis data
    with open(path) as f:
        report_data = json.load(f)

    # Send to Slack
    result = run_async(operations.send_slack_report(report_data, channel=channel))

    if result.get("success"):
        output_json({
            "success": True,
            "message": result["message"],
            "channel": result["channel"],
            "source_file": str(path),
        })
    else:
        output_error(result.get("error", "Failed to send report"))


@app.command("markdown-report")
@zendesk_command
def markdown_report_cmd(
    analysis_file: Annotated[
        str | None,
        typer.Argument(help="Path to support_analysis.json file"),
    ] = None,
    output_file: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output file path (prints to stdout if not specified)"),
    ] = None,
) -> None:
    """Generate a detailed markdown support metrics report.

    Uses the analysis file generated by the analyze script, or searches
    for the most recent support_analysis.json in the temp directory.

    Output is written to stdout by default, or to a file if --output is specified.
    """
    import tempfile

    # Find analysis file
    if analysis_file:
        path = Path(analysis_file)
    else:
        # Search for most recent analysis file
        base_dir = Path(tempfile.gettempdir()) / "zendesk-skill"
        candidates = list(base_dir.glob("**/support_analysis.json"))
        if not candidates:
            output_error(
                "No analysis file found. Run the analysis first or provide a file path.\n"
                "Example: uv run python src/zendesk_skill/scripts/analyze_support_metrics.py"
            )
        path = max(candidates, key=lambda f: f.stat().st_mtime)

    if not path.exists():
        output_error(f"File not found: {path}")

    # Load analysis data
    with open(path) as f:
        report_data = json.load(f)

    # Generate markdown report
    markdown = operations.generate_markdown_report(report_data)

    if output_file:
        output_path = Path(output_file)
        output_path.write_text(markdown)
        output_json({
            "success": True,
            "message": f"Report written to {output_path}",
            "output_file": str(output_path),
            "source_file": str(path),
        })
    else:
        # Print markdown directly to stdout
        print(markdown)


def main_cli() -> None:
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main_cli()
