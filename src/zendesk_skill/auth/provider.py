"""AuthProvider protocol and factory for pluggable auth backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol for authentication providers.

    TokenAuthProvider: Basic Auth with email + API token.
    OAuthProvider: OAuth 2.0 Authorization Code + PKCE.
    """

    @property
    def subdomain(self) -> str:
        """Zendesk subdomain (e.g., 'company' for company.zendesk.com)."""
        ...

    def get_auth_headers(self) -> dict[str, str]:
        """Return headers dict including Authorization."""
        ...

    async def validate(self) -> dict:
        """Validate credentials by calling users/me.json.

        Returns:
            Dict with user info: id, name, email, role
        """
        ...


def resolve_auth_provider() -> AuthProvider:
    """Factory: return the appropriate auth provider based on available credentials.

    Resolution order:
        1. Valid OAuth token on disk -> OAuthProvider
        2. API token credentials (env vars or config) -> TokenAuthProvider
        3. Raises ZendeskAuthError with guidance
    """
    import json

    from zendesk_skill.auth.oauth import OAuthProvider
    from zendesk_skill.auth.token_auth import TokenAuthProvider
    from zendesk_skill.client import ZendeskAuthError

    # Try OAuth first: check if token file exists and has a valid token
    try:
        provider = OAuthProvider()
        if provider.has_token():
            return provider
    except (ZendeskAuthError, OSError, json.JSONDecodeError):
        pass

    # Try API token auth
    try:
        return TokenAuthProvider()
    except (ZendeskAuthError, OSError, json.JSONDecodeError):
        pass

    raise ZendeskAuthError(
        "No Zendesk credentials found. Set up using:\n"
        "  OAuth:     zendesk auth login-oauth\n"
        "  API Token: zendesk auth login\n"
        "  Env vars:  ZENDESK_EMAIL, ZENDESK_TOKEN, ZENDESK_SUBDOMAIN"
    )
