# Zendesk Search Query Syntax Reference

This document covers the full query syntax for `zendesk_search`.

## Basic Structure

Queries consist of field:value pairs and text searches combined with spaces (AND logic).

```
field:value field2:value2 "text search"
```

## Field Reference

### Ticket Status

| Query | Description |
|-------|-------------|
| `status:new` | New tickets |
| `status:open` | Open tickets |
| `status:pending` | Pending tickets |
| `status:hold` | On hold tickets |
| `status:solved` | Solved tickets |
| `status:closed` | Closed tickets |
| `status<solved` | Not yet solved (new, open, pending, hold) |
| `status>new` | Beyond new (open, pending, hold, solved, closed) |

### Ticket Type

| Query | Description |
|-------|-------------|
| `type:incident` | Incident tickets |
| `type:problem` | Problem tickets |
| `type:question` | Question tickets |
| `type:task` | Task tickets |

### Priority

| Query | Description |
|-------|-------------|
| `priority:urgent` | Urgent priority |
| `priority:high` | High priority |
| `priority:normal` | Normal priority |
| `priority:low` | Low priority |
| `priority>normal` | High or urgent |
| `priority<high` | Normal or low |

### Assignment

| Query | Description |
|-------|-------------|
| `assignee:me` | Assigned to current user |
| `assignee:none` | Unassigned tickets |
| `assignee:john@example.com` | Assigned to specific agent |
| `assignee_id:12345` | Assigned to agent by ID |
| `group:Support` | Assigned to group by name |
| `group_id:123` | Assigned to group by ID |

### Requester / Submitter

| Query | Description |
|-------|-------------|
| `requester:user@example.com` | From specific requester |
| `requester_id:12345` | From requester by ID |
| `requester:*@bigclient.com` | Wildcard domain match |
| `submitter:agent@company.com` | Submitted by specific user |
| `submitter:requester` | Submitted by the requester themselves |

### Organization

| Query | Description |
|-------|-------------|
| `organization:Acme` | From organization by name |
| `organization_id:123` | From organization by ID |

### Tags

| Query | Description |
|-------|-------------|
| `tags:billing` | Has specific tag |
| `tags:urgent tags:vip` | Has both tags (AND) |
| `-tags:spam` | Does NOT have tag |

### Channels

| Query | Description |
|-------|-------------|
| `via:web` | Created via web form |
| `via:email` | Created via email |
| `via:chat` | Created via chat |
| `via:api` | Created via API |
| `via:twitter` | Created via Twitter |
| `via:facebook` | Created via Facebook |

### Dates and Times

#### Absolute Dates
| Query | Description |
|-------|-------------|
| `created:2024-01-15` | Created on specific date |
| `created>2024-01-01` | Created after date |
| `created<2024-01-31` | Created before date |
| `created>=2024-01-01` | Created on or after |
| `updated>2024-01-01` | Updated after date |
| `solved>2024-01-01` | Solved after date |

#### Relative Dates
| Query | Description |
|-------|-------------|
| `created>1hour` | Created in last hour |
| `created>1day` | Created in last day |
| `created>1week` | Created in last week |
| `created>1month` | Created in last month |
| `created>1year` | Created in last year |
| `updated<2weeks` | Not updated in 2 weeks |

### Text Search

| Query | Description |
|-------|-------------|
| `password reset` | Search in all text fields |
| `subject:password` | Search in subject only |
| `description:error` | Search in description only |
| `"exact phrase"` | Match exact phrase |
| `password*` | Wildcard prefix match |

### Custom Fields

| Query | Description |
|-------|-------------|
| `custom_field_123456:value` | Match custom field value |
| `fieldname:value` | Match by field name (if unique) |

### Ticket Attributes

| Query | Description |
|-------|-------------|
| `has_attachment:true` | Has attachments |
| `has_attachment:false` | No attachments |
| `has_incidents:true` | Problem with linked incidents |

### SLA

| Query | Description |
|-------|-------------|
| `sla_policy_id:123` | Has specific SLA policy |
| `sla:breached` | SLA breached |
| `sla:paused` | SLA paused |

## Operators

### Comparison Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `:` | Equals | `status:open` |
| `>` | Greater than | `priority>normal` |
| `<` | Less than | `created<2024-01-01` |
| `>=` | Greater or equal | `created>=2024-01-01` |
| `<=` | Less or equal | `priority<=high` |

### Logical Operators

| Operator | Description | Example |
|----------|-------------|---------|
| (space) | AND | `status:open priority:urgent` |
| `-` | NOT (prefix) | `-tags:spam` |

Note: OR is not directly supported. Use multiple searches or wider filters.

### Wildcards

| Pattern | Description | Example |
|---------|-------------|---------|
| `*` | Match any characters | `requester:*@gmail.com` |
| `?` | Match single character | `tag:issue-?` |

## Complex Query Examples

### High Priority Open Tickets
```
status:open priority:urgent
```

### Unassigned Tickets from VIP Org
```
assignee:none organization:VIP-Client
```

### Tickets Updated This Week
```
updated>1week status<solved
```

### Problems with Incidents
```
type:problem has_incidents:true status:open
```

### Escalated Tickets Awaiting Response
```
tags:escalated status:pending updated<2days
```

### Tickets from Email Without Attachments
```
via:email has_attachment:false created>1week
```

### Support Team Backlog
```
group:Support status:open -tags:waiting-customer
```

### Customer Complaints
```
tags:complaint priority>normal created>1month
```

### Agent's Open Workload
```
assignee:me status:open
```

### Tickets Mentioning Specific Issue
```
"connection timeout" status<solved
```

## Sorting

Sorting is controlled by the `sort_by` and `sort_order` parameters:

| sort_by | Description |
|---------|-------------|
| `created_at` | Creation date |
| `updated_at` | Last update date |
| `priority` | Priority level |
| `status` | Status |
| `ticket_type` | Ticket type |

| sort_order | Description |
|------------|-------------|
| `asc` | Ascending (oldest first) |
| `desc` | Descending (newest first, default) |

## Pagination

- `per_page`: 1-100 (default 25)
- `page`: Page number (starts at 1)
- Maximum results: 1000 (page 10 with per_page=100)

## Tips

1. **Combine filters** to narrow results quickly
2. **Use relative dates** for recurring queries
3. **Check pagination** - results may span multiple pages
4. **Wildcards in email** help find all tickets from a domain
5. **Negative filters** (`-tags:`) exclude unwanted results
6. **Status comparisons** (`status<solved`) are cleaner than listing statuses
