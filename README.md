# Zendesk CLI, MCP Server & Claude Code Skill

A Claude Code skill for Zendesk Support integration. The primary interface is a **skill file** (`SKILL.md`) that gives Claude full command knowledge — backed by a CLI (`zd-cli`) that Claude runs to interact with Zendesk. An MCP server is also included for other AI assistants.

> This project was built almost entirely by Claude Code through iterative conversation — designing, implementing, testing against a live Zendesk instance, and fixing issues together over multiple sessions. It's a tool I use often and am sharing in case others find it useful.

## Features

- **Claude Code skill** — `SKILL.md` gives Claude full command documentation, workflows, and the save-first/query-later pattern
- **CLI (`zd-cli`)** — 28 commands covering tickets, users, organizations, views, metrics, and more
- **MCP server** — alternative interface for other AI assistants
- **Slack integration** — send formatted support reports to Slack channels
- **Support metrics analysis** — response times, resolution rates, CSAT, after-hours and on-call tracking
- **Local response storage** — API responses saved to temp directory, queryable with jq without re-fetching
- **Markdown formatting** — write operations convert Markdown to HTML for proper rendering in Agent Workspace
- **Encrypted credentials** — API tokens and OAuth tokens encrypted at rest

## Quick Start

```bash
# Run directly (no install needed)
uvx --from zendesk-skill zd-cli --help

# Set up credentials (see Authentication below)
uvx --from zendesk-skill zd-cli auth login

# Verify it works
uvx --from zendesk-skill zd-cli me
```

## Authentication

Three methods are supported. API token is the simplest; OAuth adds auto-refresh. A relay server mode exists for shared team deployments but is not covered here.

### Method 1: API Token

1. In Zendesk: **Admin Center → Apps and integrations → APIs → Zendesk API → Add API token**
2. Copy the token (shown only once)

```bash
# Interactive setup (prompts for email, token, subdomain)
zd-cli auth login

# Or non-interactive
zd-cli auth login --email you@company.com --token YOUR_TOKEN --subdomain yourcompany

# Or via environment variables
export ZENDESK_EMAIL="you@company.com"
export ZENDESK_TOKEN="your-api-token"
export ZENDESK_SUBDOMAIN="yourcompany"
```

### Method 2: OAuth 2.0 (with auto-refresh)

1. In Zendesk: **Admin Center → Apps and integrations → APIs → OAuth Clients → Add OAuth client**
2. Add redirect URIs for ports 8080–8089:
   ```
   http://127.0.0.1:8080/callback
   http://127.0.0.1:8081/callback
   ...
   http://127.0.0.1:8089/callback
   ```
3. Note the **Client ID** and **Client Secret**

```bash
# Opens a browser for authorization
zd-cli auth login-oauth --subdomain yourcompany --client-id YOUR_ID --client-secret YOUR_SECRET

# Headless/SSH: browser redirects to localhost (shows connection refused — that's expected),
# paste the full redirect URL back into the prompt
zd-cli auth login-oauth --subdomain yourcompany --client-id YOUR_ID --client-secret YOUR_SECRET --manual
```

```bash
# Check auth status
zd-cli auth status

# Remove credentials
zd-cli auth logout          # API token
zd-cli auth logout-oauth    # OAuth token
```

## Installation

### Via uvx (no install, recommended)

```bash
uvx --from zendesk-skill zd-cli --help
```

`uvx` runs the tool in an isolated environment on demand — nothing is permanently installed.

### Via uv tool install

```bash
uv tool install zendesk-skill
zd-cli --help
```

### Development / Claude Code skill

```bash
git clone https://github.com/andmarios/zendesk-skill zendesk-skill
cd zendesk-skill
uv sync
uv run zd-cli --help
```

Point Claude Code at `SKILL.md` to give Claude full command documentation and workflows.

**Prerequisites:** Python 3.12+, [uv](https://github.com/astral-sh/uv), and `jq` (for the `query` command).

## Commands

### Tickets

| Command | Description |
|---------|-------------|
| `search "status:open priority:urgent"` | Search tickets |
| `ticket 12345` | Get ticket by ID |
| `ticket-details 12345` | Ticket + all comments |
| `linked-incidents 12345` | Incidents linked to a problem ticket |
| `attachment --ticket 12345 <url>` | Download an attachment |

### Write Operations

All write commands convert Markdown to HTML by default (use `--plain-text` to skip).

| Command | Description |
|---------|-------------|
| `update-ticket 12345 --status pending --tags "waiting-customer"` | Update ticket |
| `create-ticket "Subject" "**Bold** description"` | Create ticket |
| `add-note 12345 "Internal **note**"` | Add internal note |
| `add-comment 12345 "Public reply"` | Add public comment |

### Metrics & Analytics

| Command | Description |
|---------|-------------|
| `ticket-metrics 12345` | Reply/resolution times for a ticket |
| `list-metrics` | Metrics across tickets |
| `satisfaction-ratings --score bad` | CSAT ratings |

### Views

| Command | Description |
|---------|-------------|
| `views` | List available views |
| `view-count 123` | Ticket count in a view |
| `view-tickets 123` | Tickets from a view |

### Users & Organizations

| Command | Description |
|---------|-------------|
| `user 12345` | Get user by ID |
| `search-users "john@example.com"` | Search users |
| `org 67890` | Get organization by ID |
| `search-orgs "Acme"` | Search organizations |

### Configuration & Info

| Command | Description |
|---------|-------------|
| `me` | Current user (tests auth) |
| `groups` | List support groups |
| `tags` | Popular tags |
| `sla-policies` | SLA policies |

### Query

```bash
# Query a saved response file with jq
zd-cli query <file> -q comments_slim       # named query
zd-cli query <file> --jq '.data.ticket'    # custom jq
zd-cli query <file> --list                 # show available named queries
```

All commands save their full API response to `/tmp/zd-cli-$UID/` and print the path. Use `zd-cli query` to extract data from saved files without re-fetching.

## Search Query Syntax

```bash
# Status & priority
zd-cli search "status:open priority:urgent"
zd-cli search "status:pending assignee:me"

# Time filters
zd-cli search "created>2024-01-01 status:open"
zd-cli search "updated<1week status:open"

# By person/org
zd-cli search "requester:user@example.com status:open"
zd-cli search "organization:acme type:incident"

# Tags
zd-cli search "tags:billing tags:escalated"
```

## Slack Integration

```bash
# Configure
zd-cli auth login-slack --webhook "https://hooks.slack.com/services/..." --channel "#support"

# Send a report
zd-cli slack-report

# Check / remove
zd-cli auth status-slack
zd-cli auth logout-slack
```

## Support Metrics Analysis

```bash
# Default: last 2 weeks
uv run python src/zendesk_skill/scripts/analyze_support_metrics.py

# Custom range with accurate reply counts from the Ticket Metrics API
uv run python src/zendesk_skill/scripts/analyze_support_metrics.py \
  --start 2026-01-01 --end 2026-01-15 --fetch-metrics

# Pipe to Slack
uv run python src/zendesk_skill/scripts/analyze_support_metrics.py | zd-cli slack-report

# Generate markdown report
zd-cli markdown-report -o report.md
```

### Business Hours & On-Call Tracking

Configure in `~/.config/zd-cli/config.json` to add after-hours and on-call sections to your reports:

```json
{
  "business_hours": {
    "timezone": "Europe/Berlin",
    "start_hour": 9,
    "end_hour": 18,
    "workdays": [0, 1, 2, 3, 4]
  },
  "oncall": {
    "enabled": true,
    "start_hour": 19,
    "end_hour": 9,
    "customers": ["bigclient.com"],
    "priorities": ["urgent"]
  }
}
```

When configured, reports include after-hours ticket counts, messages, replies, and on-call engagements (tickets matching the configured priority + customer domain during on-call hours). FRT for on-call tickets is calculated in calendar time; all others use business hours.

## MCP Server

The MCP server exposes the same functionality for AI assistants that support the Model Context Protocol.

```bash
uv run zendesk-mcp
```

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "uvx",
      "args": ["--from", "zendesk-skill", "zendesk-mcp"]
    }
  }
}
```

## Development

```bash
uv run pytest -v
uv run zd-cli --help
```

## License

MIT
