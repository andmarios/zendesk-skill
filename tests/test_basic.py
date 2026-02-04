"""Basic tests for zendesk-skill CLI and MCP server."""

import json
import tempfile
from pathlib import Path

import pytest


def test_import_cli():
    """Test that the CLI module can be imported."""
    from zendesk_skill.cli import app

    assert app.info.name == "zendesk"


def test_cli_has_commands():
    """Test that CLI has expected number of commands."""
    from zendesk_skill.cli import app

    # Get registered commands
    commands = list(app.registered_commands)
    assert len(commands) == 27


def test_cli_command_names():
    """Test that expected commands are registered."""
    from zendesk_skill.cli import app

    command_names = [cmd.name for cmd in app.registered_commands]

    expected_commands = [
        "search",
        "ticket",
        "ticket-details",
        "linked-incidents",
        "attachment",
        "update-ticket",
        "create-ticket",
        "add-note",
        "add-comment",
        "ticket-metrics",
        "list-metrics",
        "satisfaction-ratings",
        "satisfaction-rating",
        "views",
        "view-count",
        "view-tickets",
        "user",
        "search-users",
        "org",
        "search-orgs",
        "groups",
        "tags",
        "sla-policies",
        "me",
        "query",
    ]

    for cmd in expected_commands:
        assert cmd in command_names, f"Missing command: {cmd}"


def test_import_server():
    """Test that the MCP server module can be imported."""
    from zendesk_skill.server import mcp

    assert mcp.name == "zendesk_skill"


def test_import_operations():
    """Test that the operations module can be imported."""
    from zendesk_skill import operations

    # Check that key operations are exported
    assert hasattr(operations, "search_tickets")
    assert hasattr(operations, "get_ticket")
    assert hasattr(operations, "get_ticket_details")
    assert hasattr(operations, "get_current_user")


def test_storage_save_response():
    """Test response storage functionality."""
    from zendesk_skill.storage import save_response, load_response

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        output_path = f.name

    try:
        test_data = {"ticket": {"id": 123, "subject": "Test"}}

        file_path, stored = save_response(
            "test_tool",
            {"ticket_id": "123"},
            test_data,
            output_path=output_path,
        )

        assert file_path == output_path
        assert stored["data"] == test_data
        assert stored["metadata"]["tool"] == "test_tool"

        # Test loading
        loaded = load_response(file_path)
        assert loaded["data"] == test_data
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_structure_extraction():
    """Test structure extraction from responses."""
    from zendesk_skill.storage import _extract_structure

    data = {
        "ticket": {
            "id": 123,
            "subject": "Test ticket",
            "status": "open",
            "tags": ["billing", "urgent"],
            "custom_fields": [{"id": 1, "value": "test"}],
        }
    }

    structure = _extract_structure(data)

    assert "ticket" in structure
    assert "ticket.id" in structure
    assert "ticket.subject" in structure
    assert "ticket.tags" in structure
    assert structure["ticket.id"] == "integer"
    assert structure["ticket.subject"] == "string"
    assert "array" in structure["ticket.tags"]


def test_queries_for_tool():
    """Test getting queries for a tool."""
    from zendesk_skill.queries import get_queries_for_tool

    # Test with CLI-style name
    queries = get_queries_for_tool("ticket_details")

    assert len(queries) > 0
    query_names = [q["name"] for q in queries]
    assert "comments_slim" in query_names
    assert "attachments" in query_names


def test_get_named_query():
    """Test getting a specific named query."""
    from zendesk_skill.queries import get_query

    # Test with CLI-style name
    query = get_query("ticket_details", "comments_slim")
    assert query is not None
    assert ".data.comments" in query


def test_client_auth_header():
    """Test auth header building."""
    from zendesk_skill.client import _build_auth_header

    header = _build_auth_header("test@example.com", "abc123")
    assert header.startswith("Basic ")

    # Decode and verify format
    import base64

    encoded = header.replace("Basic ", "")
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "test@example.com/token:abc123"


def test_client_singleton():
    """Test client singleton pattern."""
    from zendesk_skill.client import reset_client, CONFIG_PATH

    # Reset to clear any existing singleton
    reset_client()

    # Without credentials, getting client should fail
    import os

    # Save current env vars
    saved = {
        "ZENDESK_EMAIL": os.environ.get("ZENDESK_EMAIL"),
        "ZENDESK_TOKEN": os.environ.get("ZENDESK_TOKEN"),
        "ZENDESK_SUBDOMAIN": os.environ.get("ZENDESK_SUBDOMAIN"),
    }

    # Save config file if it exists
    saved_config = None
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            saved_config = f.read()

    try:
        # Clear env vars
        for key in saved:
            if key in os.environ:
                del os.environ[key]

        # Temporarily remove config file
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()

        from zendesk_skill.client import ZendeskAuthError, ZendeskClient

        # Should raise auth error without credentials
        with pytest.raises(ZendeskAuthError):
            ZendeskClient()
    finally:
        # Restore env vars
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value

        # Restore config file
        if saved_config is not None:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write(saved_config)
            CONFIG_PATH.chmod(0o600)

        reset_client()


def test_output_helpers():
    """Test CLI output helper functions."""
    import io
    import sys
    from zendesk_skill.cli import output_json

    # Capture stdout
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    try:
        output_json({"test": "value", "count": 42})
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    parsed = json.loads(output)
    assert parsed["test"] == "value"
    assert parsed["count"] == 42


# =============================================================================
# Auth Tests
# =============================================================================


def test_auth_subcommands_exist():
    """Test that auth subcommands exist."""
    from zendesk_skill.cli import auth_app

    command_names = [cmd.name for cmd in auth_app.registered_commands]

    # Zendesk auth commands
    assert "login" in command_names
    assert "status" in command_names
    assert "logout" in command_names

    # Slack auth commands
    assert "login-slack" in command_names
    assert "status-slack" in command_names
    assert "logout-slack" in command_names

    assert len(command_names) == 6


def test_get_auth_status():
    """Test get_auth_status function."""
    import os
    from zendesk_skill.client import get_auth_status, CONFIG_PATH

    # Save current env vars
    saved = {
        "ZENDESK_EMAIL": os.environ.get("ZENDESK_EMAIL"),
        "ZENDESK_TOKEN": os.environ.get("ZENDESK_TOKEN"),
        "ZENDESK_SUBDOMAIN": os.environ.get("ZENDESK_SUBDOMAIN"),
    }

    try:
        # Clear env vars
        for key in saved:
            if key in os.environ:
                del os.environ[key]

        status = get_auth_status()

        assert "configured" in status
        assert "source" in status
        assert "config_path" in status
        assert "env_vars_set" in status
        assert "has_config_file" in status
        assert status["config_path"] == str(CONFIG_PATH)

    finally:
        # Restore env vars
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_save_and_delete_credentials():
    """Test save_credentials and delete_credentials functions."""
    from zendesk_skill.client import save_credentials, delete_credentials, CONFIG_PATH
    import os

    # Save original config if it exists
    original_config = None
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            original_config = f.read()

    try:
        # Test save
        path = save_credentials("test@example.com", "test_token", "test_subdomain")
        assert path == CONFIG_PATH
        assert CONFIG_PATH.exists()

        # Verify file permissions (owner read/write only)
        mode = CONFIG_PATH.stat().st_mode & 0o777
        assert mode == 0o600

        # Verify content
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        assert config["email"] == "test@example.com"
        assert config["token"] == "test_token"
        assert config["subdomain"] == "test_subdomain"

        # Test delete
        deleted = delete_credentials()
        assert deleted is True
        assert not CONFIG_PATH.exists()

        # Test delete when file doesn't exist
        deleted_again = delete_credentials()
        assert deleted_again is False

    finally:
        # Restore original config if it existed
        if original_config is not None:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write(original_config)
            CONFIG_PATH.chmod(0o600)


def test_auth_operations_exist():
    """Test that auth operations are exported."""
    from zendesk_skill import operations

    assert hasattr(operations, "check_auth_status")
    assert hasattr(operations, "auth_login")
    assert hasattr(operations, "auth_logout")


def test_mcp_auth_tool_exists():
    """Test that MCP auth status tool exists."""
    from zendesk_skill.server import mcp

    # Get tool names
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "zendesk_auth_status" in tool_names


# =============================================================================
# Formatting Tests
# =============================================================================


def test_markdown_to_html_bold():
    """Test bold Markdown conversion."""
    from zendesk_skill.formatting import markdown_to_html

    result = markdown_to_html("**bold text**")
    assert "<strong>bold text</strong>" in result


def test_markdown_to_html_italic():
    """Test italic Markdown conversion."""
    from zendesk_skill.formatting import markdown_to_html

    result = markdown_to_html("*italic text*")
    assert "<em>italic text</em>" in result


def test_markdown_to_html_headers():
    """Test header Markdown conversion."""
    from zendesk_skill.formatting import markdown_to_html

    result = markdown_to_html("# Header 1\n## Header 2")
    assert "<h1>" in result
    assert "<h2>" in result


def test_markdown_to_html_list():
    """Test list Markdown conversion."""
    from zendesk_skill.formatting import markdown_to_html

    result = markdown_to_html("- item 1\n- item 2\n- item 3")
    assert "<ul>" in result
    assert "<li>" in result


def test_markdown_to_html_code_block():
    """Test code block Markdown conversion."""
    from zendesk_skill.formatting import markdown_to_html

    result = markdown_to_html("```\ncode here\n```")
    assert "<code>" in result


def test_markdown_to_html_links():
    """Test link Markdown conversion."""
    from zendesk_skill.formatting import markdown_to_html

    result = markdown_to_html("[link text](https://example.com)")
    assert '<a href="https://example.com">' in result
    assert "link text" in result


def test_markdown_to_html_passthrough():
    """Test that content starting with an HTML tag is passed through unchanged."""
    from zendesk_skill.formatting import markdown_to_html

    html_content = "<p>Already <strong>HTML</strong></p>"
    result = markdown_to_html(html_content)
    assert result == html_content


def test_markdown_to_html_no_false_passthrough():
    """Test that Markdown mentioning HTML tags is NOT treated as HTML."""
    from zendesk_skill.formatting import markdown_to_html

    # Markdown that mentions HTML tags in code spans or text
    md = "Use `<strong>` for **bold** and `<em>` for *italic*"
    result = markdown_to_html(md)
    # Should be converted, not passed through
    assert "<strong>" in result  # from **bold**
    assert "<em>" in result  # from *italic*


def test_markdown_to_html_empty():
    """Test empty content returns empty string."""
    from zendesk_skill.formatting import markdown_to_html

    assert markdown_to_html("") == ""


def test_plain_text_to_html():
    """Test plain text wrapping with HTML escaping."""
    from zendesk_skill.formatting import plain_text_to_html

    result = plain_text_to_html("Hello <world> & friends")
    assert "<p>" in result
    assert "&lt;world&gt;" in result
    assert "&amp;" in result


def test_plain_text_to_html_newlines():
    """Test plain text newline handling."""
    from zendesk_skill.formatting import plain_text_to_html

    result = plain_text_to_html("line 1\nline 2")
    assert "<br>" in result


def test_plain_text_to_html_paragraphs():
    """Test plain text paragraph separation."""
    from zendesk_skill.formatting import plain_text_to_html

    result = plain_text_to_html("para 1\n\npara 2")
    assert result.count("<p>") == 2


def test_plain_text_to_html_empty():
    """Test empty plain text returns empty string."""
    from zendesk_skill.formatting import plain_text_to_html

    assert plain_text_to_html("") == ""


def test_format_for_zendesk_returns_html_body():
    """Test that format_for_zendesk returns dict with html_body key."""
    from zendesk_skill.formatting import format_for_zendesk

    result = format_for_zendesk("**bold**")
    assert "html_body" in result
    assert "body" not in result
    assert "<strong>bold</strong>" in result["html_body"]


def test_format_for_zendesk_plain_text():
    """Test format_for_zendesk with plain_text=True."""
    from zendesk_skill.formatting import format_for_zendesk

    result = format_for_zendesk("Hello <world>", plain_text=True)
    assert "html_body" in result
    assert "&lt;world&gt;" in result["html_body"]


def test_format_for_zendesk_size_limit():
    """Test that format_for_zendesk raises ValueError for oversized content."""
    from zendesk_skill.formatting import format_for_zendesk

    # Create content that exceeds 64KB after conversion
    huge_content = "x" * 70000
    with pytest.raises(ValueError, match="64KB limit"):
        format_for_zendesk(huge_content)
