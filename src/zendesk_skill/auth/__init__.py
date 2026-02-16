"""Authentication providers for Zendesk API."""

from zendesk_skill.auth.oauth import OAuthProvider
from zendesk_skill.auth.provider import AuthProvider, resolve_auth_provider
from zendesk_skill.auth.scopes import DEFAULT_SCOPES
from zendesk_skill.auth.token_auth import TokenAuthProvider

__all__ = [
    "AuthProvider",
    "DEFAULT_SCOPES",
    "OAuthProvider",
    "TokenAuthProvider",
    "resolve_auth_provider",
]
