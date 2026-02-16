# OAuth Support for Zendesk MCP

**Date**: 2026-02-16
**Status**: Approved

## Summary

Add OAuth 2.0 (Authorization Code + PKCE) support alongside existing API token auth. Mirrors the `AuthProvider` protocol pattern from `../google-workspace`.

## Requirements

- OAuth coexists with Basic Auth (API token) — auto-detect based on available credentials
- OAuth client credentials from both env vars and config file (env takes priority)
- Loopback redirect flow with auto-fallback to manual code paste for headless environments
- Pluggable AuthProvider protocol for easy relay server addition later
- Token refresh with rotation (Zendesk invalidates old tokens on refresh)

## Architecture

### New `auth/` Package

```
src/zendesk_skill/auth/
├── __init__.py          # Exports: AuthProvider, resolve_auth_provider
├── provider.py          # AuthProvider Protocol + resolve_auth_provider() factory
├── token_auth.py        # TokenAuthProvider — wraps existing Basic Auth logic
├── oauth.py             # OAuthProvider — authorization code + PKCE, loopback + manual paste
└── scopes.py            # Zendesk OAuth scope constants
```

### AuthProvider Protocol

```python
from typing import Protocol

class AuthProvider(Protocol):
    @property
    def subdomain(self) -> str: ...

    def get_auth_headers(self) -> dict[str, str]:
        """Return Authorization header dict."""
        ...

    async def validate(self) -> dict:
        """Validate credentials by calling users/me.json. Returns user info."""
        ...
```

### Provider Resolution

`resolve_auth_provider()` checks in order:
1. Valid OAuth token on disk → `OAuthProvider` (Bearer token)
2. API token credentials available (env/config) → `TokenAuthProvider` (Basic auth)
3. Raises `ZendeskAuthError` with guidance

### OAuth Flow

**Endpoints:**
- Authorize: `https://{subdomain}.zendesk.com/oauth/authorizations/new`
- Token: `https://{subdomain}.zendesk.com/oauth/tokens`

**Loopback flow (desktop):**
1. User runs `zendesk auth login-oauth`
2. Load OAuth client credentials (env vars or config)
3. Generate PKCE code_verifier + code_challenge (SHA256)
4. Start local HTTP server on `127.0.0.1` port 8080-8099
5. Open browser to authorize URL with redirect_uri=loopback
6. Capture callback with authorization code
7. Exchange code + code_verifier at token endpoint
8. Save tokens to `oauth_token.json`

**Manual paste fallback (headless):**
- Print authorization URL
- User completes in browser, pastes redirect URL back to CLI
- Extract code from URL, proceed with token exchange

**Token refresh:**
- `get_auth_headers()` checks `expires_at` before returning
- If expired, POST refresh_token to `/oauth/tokens` with `grant_type=refresh_token`
- Save new token pair (Zendesk rotates both access + refresh tokens)
- If refresh fails, raise error directing user to re-authenticate

### Scopes

```python
DEFAULT_SCOPES = "read write"
```

Broad scopes for personal CLI tool. Granular scopes deferred to relay server work.

### Token Storage

```
~/.claude/.zendesk-skill/
├── config.json             # Extended with: oauth_client_id, oauth_client_secret
└── oauth_token.json        # NEW: access_token, refresh_token, expires_at, token_type, scope
```

File permissions: `0o600`.

### Client Refactoring

`ZendeskClient.__init__` accepts an optional `auth_provider: AuthProvider` parameter:
- If `auth_provider` provided → use it directly
- If raw email/token/subdomain provided → wrap in `TokenAuthProvider`
- If nothing provided → call `resolve_auth_provider()` to auto-detect

`_get_headers()` delegates to `auth_provider.get_auth_headers()`.

Singleton `get_client()` calls `resolve_auth_provider()`.

### CLI Changes

New commands under existing `auth` group:
- `zendesk auth login-oauth` — Run OAuth flow (requires subdomain + client credentials)
- `zendesk auth logout-oauth` — Delete OAuth token file

Enhanced:
- `zendesk auth status` — Show both OAuth and token auth status

Existing `auth login` / `auth logout` unchanged.

### Dependencies

No new dependencies. Uses:
- `httpx` (existing) for OAuth HTTP requests
- `http.server` (stdlib) for loopback callback
- `secrets` + `hashlib` (stdlib) for PKCE
- `webbrowser` (stdlib) for browser opening
- `urllib.parse` (stdlib) for URL construction

## Non-Goals

- Multi-account support (defer)
- OAuth relay server integration (next phase)
- Granular scope selection (defer to relay)
- Device flow (not needed — manual paste covers headless)
