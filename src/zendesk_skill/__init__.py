"""Zendesk MCP Server - A Python MCP server for Zendesk API integration."""

from importlib.metadata import version as _pkg_version

__version__ = _pkg_version("zd-cli")

from zendesk_skill.client import ZendeskAPIError, ZendeskAuthError, ZendeskClient
