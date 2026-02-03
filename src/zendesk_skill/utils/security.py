"""Security utilities for Zendesk skill - wrapper around prompt-security-utils.

Security wrapping is enabled by default. To disable, add to
~/.claude/.zendesk-skill/config.json:

    {"security_enabled": false}

To allowlist specific tickets:
    {"allowlisted_tickets": ["12345", "67890"]}
"""

import json
from pathlib import Path
from typing import Any

from prompt_security import (
    SecurityConfig,
    detect_suspicious_content,
    load_config,
    output_external_content,
    screen_content,
    wrap_field,
    wrap_fields,
    wrap_untrusted_content,
)

# Zendesk config path
ZENDESK_CONFIG_PATH = Path.home() / ".claude" / ".zendesk-skill" / "config.json"

__all__ = [
    "is_security_enabled",
    "wrap_untrusted_content",
    "wrap_field",
    "wrap_field_simple",
    "wrap_fields",
    "output_external_content",
    "detect_suspicious_content",
    "screen_content",
    "load_config",
    "SecurityConfig",
]


def _load_zendesk_config() -> dict[str, Any]:
    """Load zendesk skill config."""
    if ZENDESK_CONFIG_PATH.exists():
        try:
            with open(ZENDESK_CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def is_security_enabled() -> bool:
    """Check if security wrapping is enabled.

    Security is enabled by default. To disable, add to
    ~/.claude/.zendesk-skill/config.json:
        {"security_enabled": false}

    Returns:
        True if security should be applied, False otherwise
    """
    config = _load_zendesk_config()
    return config.get("security_enabled", True)  # Default: enabled


def is_allowlisted(source_type: str, source_id: str) -> bool:
    """Check if a source is in the zendesk allowlist.

    Args:
        source_type: Type of source ("ticket", "comment", etc.)
        source_id: Unique identifier for the source

    Returns:
        True if allowlisted, False otherwise
    """
    config = _load_zendesk_config()
    if source_type in ("ticket", "zendesk", "comment"):
        return source_id in config.get("allowlisted_tickets", [])
    return False


def wrap_field_simple(
    content: str | None,
    source_type: str,
    source_id: str,
) -> dict[str, Any] | str | None:
    """Wrap a field with security markers, returning simplified output for MCP tools.

    If security is disabled in zendesk config, returns content unchanged.
    If source is allowlisted, returns content unchanged.

    Args:
        content: The content to wrap (returns None if None)
        source_type: Type of source ("ticket", "comment", etc.)
        source_id: Unique identifier for the source

    Returns:
        Wrapped content dict if security enabled, otherwise original content
    """
    if content is None:
        return None

    # Check if security is enabled in zendesk config
    if not is_security_enabled():
        return content

    # Check zendesk allowlist
    if is_allowlisted(source_type, source_id):
        return content  # Return unwrapped

    security_config = load_config()

    return wrap_untrusted_content(
        content,
        source_type,
        source_id,
        start_marker=security_config.content_start_marker,
        end_marker=security_config.content_end_marker,
    )
