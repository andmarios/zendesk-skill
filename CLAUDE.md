# Zendesk CLI - Developer Guide

A Claude Code skill that provides a CLI for Zendesk Support integration.

## Project Structure

```
zendesk-skill/
├── pyproject.toml           # uv project config (entry point: zendesk)
├── SKILL.md                 # Claude Code skill file
├── CLAUDE.md                # This file
├── README.md                # User documentation
├── reference/               # Additional documentation
│   └── search-syntax.md     # Zendesk search query reference
├── src/
│   └── zendesk_skill/
│       ├── __init__.py      # Package init with version
│       ├── cli.py           # Typer CLI with commands
│       ├── client.py        # Zendesk API client (httpx-based)
│       ├── formatting.py    # Markdown-to-HTML for write operations
│       ├── storage.py       # Response storage + structure extraction
│       ├── queries.py       # jq query definitions
│       └── auth/            # Pluggable auth backends
│           ├── __init__.py  # Package exports
│           ├── provider.py  # AuthProvider protocol + resolve factory
│           ├── token_auth.py # Basic Auth (email + API token)
│           ├── oauth.py     # OAuth 2.0 Authorization Code + PKCE
│           └── scopes.py    # OAuth scope constants
└── tests/
    ├── test_basic.py        # Basic + formatting tests
    └── test_auth.py         # Auth package tests
```

## Architecture

### CLI (cli.py)
- Uses Typer for command-line interface
- 25 commands organized by category
- Outputs JSON to stdout
- Saves full responses to `/tmp/zendesk-skill/`

### Auth (auth/)
- `AuthProvider` protocol: pluggable auth backends
- `resolve_auth_provider()` factory: OAuth token → API token → error
- `TokenAuthProvider`: Basic Auth with email + API token
- `OAuthProvider`: OAuth 2.0 Authorization Code + PKCE, auto-refresh
- Lazy imports in provider.py to avoid circular deps with client.py

### API Client (client.py)
- httpx-based async HTTP client
- Delegates auth to `AuthProvider` (accepts `auth_provider` param)
- Backwards compatible: still accepts explicit email/token/subdomain
- Proper error handling with actionable messages

### Storage (storage.py)
- Saves all API responses to `/tmp/zendesk-skill/`
- Auto-extracts response structure for metadata
- File naming: `{command}_{md5_8}_{timestamp}.json`

### Queries (queries.py)
- Named jq queries per command type
- Executes jq via subprocess (safe)
- Supports both named and custom queries

## Command Categories

1. **Ticket Commands** (5): search, ticket, ticket-details, linked-incidents, attachment
2. **Write Operations** (4): update-ticket, create-ticket, add-note, add-comment
3. **Metrics & Analytics** (4): ticket-metrics, list-metrics, satisfaction-ratings, satisfaction-rating
4. **Views** (3): views, view-count, view-tickets
5. **Users & Orgs** (4): user, search-users, org, search-orgs
6. **Config** (4): groups, tags, sla-policies, me
7. **Query** (1): query (for jq queries on stored files)

## Development

### Running Commands

```bash
# Show all commands
uv run zendesk --help

# Run specific command
uv run zendesk me
uv run zendesk search "status:open"
uv run zendesk ticket-details 12345

# Get command help
uv run zendesk search --help
```

### Testing

```bash
# Run all tests
uv run pytest -v

# Test CLI import
uv run python -c "from zendesk_skill.cli import app; print('OK')"
```

### Adding a New Command

1. Add command function in `cli.py` with `@app.command()` decorator
2. Use Typer's `Annotated` type hints for parameters
3. Call `get_client()` to get the Zendesk client
4. Use `run_async()` to call async client methods
5. Save response with `save_response()`
6. Output summary JSON with `output_json()`
7. Add named queries in `queries.py` if appropriate
8. Update SKILL.md with command documentation

### Command Pattern

```python
@app.command("command-name")
def command_name(
    arg: Annotated[str, typer.Argument(help="Description")],
    option: Annotated[Optional[str], typer.Option("--option", "-o", help="Description")] = None,
    output_path: Annotated[Optional[str], typer.Option("--output", help="Custom output path")] = None,
) -> None:
    """Command description."""
    client = get_client()

    try:
        result = run_async(client.get("endpoint"))
        file_path = save_response("command_name", {"arg": arg}, result, output_path)
        output_json({
            "summary_field": result.get("field"),
            "file_path": str(file_path),
        })
    except ZendeskClientError as e:
        output_error(str(e))
```

## Dependencies

- `typer>=0.9.0` - CLI framework
- `rich>=13.0.0` - Pretty terminal output
- `httpx>=0.27.0` - Async HTTP client
- `pydantic>=2.0.0` - Input validation
- `mistune>=3.0.0` - Markdown-to-HTML conversion for write operations

## External Requirements

- `jq` must be installed for `zendesk query` command to work
- Zendesk credentials: OAuth token or API token (email + token + subdomain)

## Auth Configuration

Auth provider is resolved automatically (first match wins):
1. **OAuth token** on disk (`~/.claude/.zendesk-skill/oauth_token.json`) → `OAuthProvider`
2. **API token** (env vars or config file) → `TokenAuthProvider`

API token credentials load from:
1. Environment variables: `ZENDESK_EMAIL`, `ZENDESK_TOKEN`, `ZENDESK_SUBDOMAIN`
2. Config file: `~/.claude/.zendesk-skill/config.json`

OAuth client credentials load from:
1. Environment variables: `ZENDESK_OAUTH_CLIENT_ID`, `ZENDESK_OAUTH_CLIENT_SECRET`
2. Config file: `oauth_client_id`, `oauth_client_secret` in config.json (auto-saved on login)

## Response Format

All commands output JSON to stdout:
```json
{
  "summary_field": "value",
  "file_path": "/tmp/zendesk-skill/command_xxx.json"
}
```

The `file_path` points to the full saved response for follow-up queries.

## MCP Server Backup

The original MCP server implementation is preserved in `backup-mcp/server.py` in case it's needed in the future. The CLI approach was chosen for easier integration with Claude Code skills (no settings.json configuration required).
