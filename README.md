# Zendesk CLI & MCP Server

A Claude Code skill for Zendesk Support integration. Provides both a CLI and MCP server for searching tickets, viewing details, analyzing metrics, managing users/organizations, updating tickets, and sending Slack reports.

## Features

- **CLI + MCP Server** - Use via command line or as an MCP server for AI assistants
- **30+ commands/tools** covering tickets, users, organizations, views, metrics, and more
- **Slack integration** - Send formatted support reports to Slack channels
- **Support metrics analysis** - Analyze response times, resolution rates, CSAT scores
- **Business hours tracking** - Track after-hours activity and on-call engagements
- **Local response storage** - API responses saved to temp directory with structure extraction
- **jq querying** - Named queries and custom jq for efficient data extraction
- **Skill file** - `SKILL.md` provides Claude with command documentation and workflows

## Installation

### 1. Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- jq (for query functionality)

### 2. Clone and Install

```bash
git clone <repo-url> ~/.claude/skills/zendesk-skill
cd ~/.claude/skills/zendesk-skill
uv sync
```

### 3. Set Up Authentication

See [Authentication Setup](#authentication-setup) below.

### 4. Verify Installation

```bash
cd ~/.claude/skills/zendesk-skill
uv run zendesk me
```

## Authentication Setup

You need three pieces of information from Zendesk:

| Value | Description | Example |
|-------|-------------|---------|
| **Email** | Your Zendesk agent email | `agent@company.com` |
| **API Token** | Generated API token (not your password) | `abCdEf123456...` |
| **Subdomain** | Your Zendesk subdomain | `mycompany` (from `mycompany.zendesk.com`) |

### Getting Your API Token

1. Log in to your Zendesk instance as an admin
2. Go to **Admin Center** (gear icon) → **Apps and integrations** → **APIs** → **Zendesk API**
3. In the **Settings** tab, ensure **Token Access** is enabled
4. Click **Add API token**
5. Give it a description (e.g., "Claude Code")
6. Click **Copy** to copy the token - **you won't be able to see it again!**
7. Click **Save**

### Finding Your Subdomain

Your subdomain is the first part of your Zendesk URL:
- `https://mycompany.zendesk.com` → subdomain is `mycompany`
- `https://support-acme.zendesk.com` → subdomain is `support-acme`

### Configure Credentials

#### Option A: Interactive Setup (recommended)

```bash
uv run zendesk auth
```

This will prompt for your credentials and save them securely.

#### Option B: Config File

Create `~/.claude/.zendesk-skill/config.json`:

```bash
mkdir -p ~/.claude/.zendesk-skill
cat > ~/.claude/.zendesk-skill/config.json << 'EOF'
{
  "email": "your-email@company.com",
  "token": "your-api-token",
  "subdomain": "yourcompany"
}
EOF
chmod 600 ~/.claude/.zendesk-skill/config.json
```

#### Option C: Environment Variables

```bash
export ZENDESK_EMAIL="your-email@company.com"
export ZENDESK_TOKEN="your-api-token"
export ZENDESK_SUBDOMAIN="yourcompany"
```

### Verify Authentication

```bash
uv run zendesk auth-status
uv run zendesk me
```

## Slack Integration

Send formatted support reports to Slack channels.

### Setup

```bash
# Interactive setup
uv run zendesk slack-auth

# Or manually configure
uv run zendesk slack-auth --webhook-url "https://hooks.slack.com/services/..." --channel "#support"
```

### Send Reports

```bash
# Generate and send a support metrics report
uv run python src/zendesk_skill/scripts/analyze_support_metrics.py | uv run zendesk slack-report
```

### Check Status

```bash
uv run zendesk slack-status
```

## Business Hours & On-Call Configuration

Track after-hours activity and on-call engagements with optional business hours configuration.

### Setup

Add to `~/.claude/.zendesk-skill/config.json`:

```json
{
  "email": "...",
  "token": "...",
  "subdomain": "...",
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
    "customers": ["customer.com"],
    "priorities": ["urgent"]
  }
}
```

**Notes:**
- `workdays`: 0=Monday, 6=Sunday
- `oncall.customers`: Empty array `[]` means ALL customers matching priority
- `oncall.start_hour`/`end_hour`: On-call hours (e.g., 19-9 means 7 PM to 9 AM)
- If business hours config is not present, these sections are omitted from reports

## MCP Server

> **Note:** The recommended usage is via the **CLI and skill file** (`SKILL.md`), which provides better integration with Claude Code. The MCP server is provided as an alternative for other AI assistants or custom integrations.

The package includes an MCP (Model Context Protocol) server that exposes all Zendesk operations as tools for AI assistants.

### Running the Server

```bash
# Start the MCP server
uv run zendesk-mcp
```

### Configuration for Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/zendesk-skill", "zendesk-mcp"]
    }
  }
}
```

### Available MCP Tools

The server exposes 26 tools including:

| Tool | Description |
|------|-------------|
| `zendesk_search` | Search tickets with Zendesk query syntax |
| `zendesk_get_ticket` | Get ticket by ID |
| `zendesk_get_ticket_details` | Get ticket with all comments |
| `zendesk_update_ticket` | Update ticket properties |
| `zendesk_create_ticket` | Create new ticket |
| `zendesk_add_private_note` | Add internal note |
| `zendesk_add_public_note` | Add public comment |
| `zendesk_get_user` | Get user details |
| `zendesk_search_users` | Search users |
| `zendesk_get_organization` | Get organization details |
| `zendesk_list_views` | List available views |
| `zendesk_get_satisfaction_ratings` | Get CSAT ratings |
| `zendesk_auth_status` | Check authentication status |

## Usage

Once installed in `~/.claude/skills/`, Claude Code will automatically use the Zendesk skill when you ask about tickets, support metrics, or Zendesk data.

### Example Prompts

- "Search for open urgent tickets"
- "Get details for ticket 12345"
- "Show me CSAT ratings from last week"
- "Find tickets from user@example.com"
- "What's the ticket count in the Support queue?"
- "Send a support metrics report to Slack"
- "Analyze our support performance for the last 2 weeks"

### Workflow Pattern

The skill saves API responses locally and uses jq for efficient extraction:

```bash
# 1. Fetch ticket details
uv run zendesk ticket-details 12345
# -> Output: file_path: "/tmp/zendesk-skill/ticket_details_xxx.json"

# 2. Extract just comment bodies
uv run zendesk query /tmp/zendesk-skill/ticket_details_xxx.json -q comments_slim

# 3. List attachments if needed
uv run zendesk query /tmp/zendesk-skill/ticket_details_xxx.json -q attachments

# 4. Download an attachment (organized by ticket)
uv run zendesk attachment --ticket 12345 "https://..."
```

## Commands

### Ticket Operations
- `zendesk search` - Search with Zendesk query syntax
- `zendesk ticket` - Get ticket by ID
- `zendesk ticket-details` - Get ticket with all comments
- `zendesk linked-incidents` - Get linked incidents
- `zendesk attachment` - Download attachment (supports `--ticket` for organization)

### Write Operations
- `zendesk update-ticket` - Update ticket properties
- `zendesk create-ticket` - Create new ticket
- `zendesk add-note` - Add internal note
- `zendesk add-comment` - Add public comment

### Metrics & Analytics
- `zendesk ticket-metrics` - Get ticket metrics
- `zendesk list-metrics` - List metrics
- `zendesk satisfaction-ratings` - List CSAT ratings
- `zendesk satisfaction-rating` - Get single rating

### Views
- `zendesk views` - List available views
- `zendesk view-count` - Get view ticket count
- `zendesk view-tickets` - Get tickets from view

### Users & Organizations
- `zendesk user` - Get user by ID
- `zendesk search-users` - Search users
- `zendesk org` - Get organization by ID
- `zendesk search-orgs` - Search organizations

### Configuration
- `zendesk groups` - List support groups
- `zendesk tags` - List popular tags
- `zendesk sla-policies` - List SLA policies
- `zendesk me` - Test authentication

### Authentication
- `zendesk auth` - Interactive credential setup
- `zendesk auth-status` - Check auth configuration
- `zendesk auth-delete` - Remove saved credentials

### Slack Integration
- `zendesk slack-auth` - Configure Slack webhook
- `zendesk slack-status` - Check Slack configuration
- `zendesk slack-delete` - Remove Slack configuration
- `zendesk slack-report` - Send report to Slack (reads JSON from stdin)

### Utility
- `zendesk query` - Query saved files with jq

## Support Metrics Analysis

Generate comprehensive support metrics reports:

```bash
# Default: last 2 weeks
uv run python src/zendesk_skill/scripts/analyze_support_metrics.py

# Custom date range
uv run python src/zendesk_skill/scripts/analyze_support_metrics.py \
  --start 2026-01-01 --end 2026-01-15

# Pipe to Slack
uv run python src/zendesk_skill/scripts/analyze_support_metrics.py | \
  uv run zendesk slack-report
```

The report includes:
- **Ticket volume** - Total, by priority, by status
- **Response times** - First response, full resolution
- **CSAT scores** - Average rating, response rate
- **Business hours activity** (if configured) - After-hours tickets, messages, replies
- **On-call engagements** (if configured) - Urgent tickets during on-call hours

## File Storage

API responses and attachments are stored in the system temp directory:
- **Linux/macOS**: `/tmp/zendesk-skill/`
- **Windows**: `%TEMP%\zendesk-skill\`

Files can be organized by ticket using the `--ticket` option:
```bash
# Saves to: <temp>/zendesk-skill/12345/attachments/file.pdf
uv run zendesk attachment --ticket 12345 "https://..."
```

## Development

```bash
# Run tests
uv run pytest

# Test CLI import
uv run python -c "from zendesk_skill.cli import app; print('OK')"

# Show all commands
uv run zendesk --help
```

## License

MIT - See [LICENSE](LICENSE) for details.
