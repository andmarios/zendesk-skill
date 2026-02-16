"""Zendesk OAuth scope definitions."""

# Broad scopes for full CLI access.
# Zendesk supports granular scopes like "tickets:read", "users:write" etc.
# but for a personal CLI tool, broad scopes are simpler.
DEFAULT_SCOPES = "read write"
