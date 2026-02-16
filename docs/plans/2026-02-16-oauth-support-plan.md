# OAuth Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add OAuth 2.0 (Authorization Code + PKCE) as an alternative auth method alongside existing API token auth, using a pluggable AuthProvider protocol.

**Architecture:** New `auth/` package with `AuthProvider` protocol, `TokenAuthProvider` (wraps existing Basic Auth), and `OAuthProvider` (loopback + manual paste flows). `ZendeskClient` delegates to the resolved provider. Factory function auto-detects which provider to use based on available credentials.

**Tech Stack:** Python 3.12+, httpx (existing), stdlib http.server/secrets/hashlib/webbrowser/urllib.parse

---

### Task 1: Create `auth/scopes.py` — OAuth Scope Constants

**Files:**
- Create: `src/zendesk_skill/auth/__init__.py` (empty for now)
- Create: `src/zendesk_skill/auth/scopes.py`
- Test: `tests/test_auth.py`

**Step 1: Write the failing test**

Create `tests/test_auth.py`:

```python
"""Tests for the auth package."""


def test_scopes_default():
    """Test that DEFAULT_SCOPES is a space-separated string."""
    from zendesk_skill.auth.scopes import DEFAULT_SCOPES

    assert isinstance(DEFAULT_SCOPES, str)
    assert "read" in DEFAULT_SCOPES
    assert "write" in DEFAULT_SCOPES
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_scopes_default -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zendesk_skill.auth'`

**Step 3: Create the auth package and scopes module**

Create `src/zendesk_skill/auth/__init__.py`:

```python
"""Authentication providers for Zendesk API."""
```

Create `src/zendesk_skill/auth/scopes.py`:

```python
"""Zendesk OAuth scope definitions."""

# Broad scopes for full CLI access.
# Zendesk supports granular scopes like "tickets:read", "users:write" etc.
# but for a personal CLI tool, broad scopes are simpler.
DEFAULT_SCOPES = "read write"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth.py::test_scopes_default -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/zendesk_skill/auth/__init__.py src/zendesk_skill/auth/scopes.py tests/test_auth.py
git commit -m "feat(auth): add auth package with OAuth scope constants"
```

---

### Task 2: Create `auth/provider.py` — AuthProvider Protocol

**Files:**
- Create: `src/zendesk_skill/auth/provider.py`
- Modify: `tests/test_auth.py`

**Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
def test_auth_provider_protocol():
    """Test that AuthProvider is a runtime-checkable protocol."""
    from zendesk_skill.auth.provider import AuthProvider

    assert hasattr(AuthProvider, "subdomain")
    assert hasattr(AuthProvider, "get_auth_headers")
    assert hasattr(AuthProvider, "validate")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_auth_provider_protocol -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write the provider module**

Create `src/zendesk_skill/auth/provider.py`:

```python
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
    from zendesk_skill.auth.oauth import OAuthProvider
    from zendesk_skill.auth.token_auth import TokenAuthProvider
    from zendesk_skill.client import ZendeskAuthError

    # Try OAuth first: check if token file exists and has a valid token
    try:
        provider = OAuthProvider()
        if provider.has_token():
            return provider
    except Exception:
        pass

    # Try API token auth
    try:
        return TokenAuthProvider()
    except Exception:
        pass

    raise ZendeskAuthError(
        "No Zendesk credentials found. Set up using:\n"
        "  OAuth:     zendesk auth login-oauth\n"
        "  API Token: zendesk auth login\n"
        "  Env vars:  ZENDESK_EMAIL, ZENDESK_TOKEN, ZENDESK_SUBDOMAIN"
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth.py::test_auth_provider_protocol -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/zendesk_skill/auth/provider.py tests/test_auth.py
git commit -m "feat(auth): add AuthProvider protocol and resolve factory"
```

---

### Task 3: Create `auth/token_auth.py` — TokenAuthProvider

This wraps the existing Basic Auth logic from `client.py`.

**Files:**
- Create: `src/zendesk_skill/auth/token_auth.py`
- Modify: `tests/test_auth.py`

**Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
import base64


def test_token_auth_provider_explicit_creds():
    """Test TokenAuthProvider with explicit credentials."""
    from zendesk_skill.auth.token_auth import TokenAuthProvider

    provider = TokenAuthProvider(
        email="test@example.com",
        token="abc123",
        subdomain="testco",
    )

    assert provider.subdomain == "testco"

    headers = provider.get_auth_headers()
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")

    # Verify the encoded value
    encoded = headers["Authorization"].replace("Basic ", "")
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "test@example.com/token:abc123"


def test_token_auth_provider_is_auth_provider():
    """Test that TokenAuthProvider satisfies the AuthProvider protocol."""
    from zendesk_skill.auth.provider import AuthProvider
    from zendesk_skill.auth.token_auth import TokenAuthProvider

    provider = TokenAuthProvider(
        email="test@example.com",
        token="abc123",
        subdomain="testco",
    )
    assert isinstance(provider, AuthProvider)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py::test_token_auth_provider_explicit_creds tests/test_auth.py::test_token_auth_provider_is_auth_provider -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write the TokenAuthProvider**

Create `src/zendesk_skill/auth/token_auth.py`:

```python
"""TokenAuthProvider — Basic Auth with email + API token."""

from __future__ import annotations

import base64

import httpx

from zendesk_skill.client import _get_credentials, ZendeskAPIError


class TokenAuthProvider:
    """Auth provider using Zendesk API token (Basic Auth).

    Wraps the existing email/token:token authentication pattern.
    Credentials loaded from env vars or config file if not explicitly provided.
    """

    def __init__(
        self,
        email: str | None = None,
        token: str | None = None,
        subdomain: str | None = None,
    ):
        if email and token and subdomain:
            self._email = email
            self._token = token
            self._subdomain = subdomain
        else:
            self._email, self._token, self._subdomain = _get_credentials()

    @property
    def subdomain(self) -> str:
        return self._subdomain

    def get_auth_headers(self) -> dict[str, str]:
        auth_string = f"{self._email}/token:{self._token}"
        encoded = base64.b64encode(auth_string.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    async def validate(self) -> dict:
        """Validate credentials by calling users/me.json."""
        url = f"https://{self._subdomain}.zendesk.com/api/v2/users/me.json"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={**self.get_auth_headers(), "Accept": "application/json"},
                timeout=30.0,
            )
            if response.status_code != 200:
                raise ZendeskAPIError(
                    f"Auth validation failed ({response.status_code})",
                    response.status_code,
                )
            user = response.json().get("user", {})
            return {
                "id": user.get("id"),
                "name": user.get("name"),
                "email": user.get("email"),
                "role": user.get("role"),
            }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -k "token_auth" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/zendesk_skill/auth/token_auth.py tests/test_auth.py
git commit -m "feat(auth): add TokenAuthProvider wrapping existing Basic Auth"
```

---

### Task 4: Create `auth/oauth.py` — OAuthProvider (PKCE + Token Storage)

This is the core OAuth module. We build it in two sub-steps: first the PKCE helpers and token storage, then the loopback flow.

**Files:**
- Create: `src/zendesk_skill/auth/oauth.py`
- Modify: `tests/test_auth.py`

**Step 1: Write the failing tests for PKCE + token storage**

Append to `tests/test_auth.py`:

```python
import json
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_pkce_code_verifier_length():
    """Test PKCE code verifier meets RFC 7636 requirements."""
    from zendesk_skill.auth.oauth import _generate_pkce_pair

    verifier, challenge = _generate_pkce_pair()
    # RFC 7636: verifier must be 43-128 chars, URL-safe
    assert 43 <= len(verifier) <= 128
    assert isinstance(challenge, str)
    assert len(challenge) > 0


def test_pkce_challenge_is_sha256_of_verifier():
    """Test that code_challenge is S256 of code_verifier."""
    import hashlib

    from zendesk_skill.auth.oauth import _generate_pkce_pair

    verifier, challenge = _generate_pkce_pair()

    # Manually compute expected challenge
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_oauth_token_save_and_load(tmp_path):
    """Test saving and loading OAuth tokens."""
    from zendesk_skill.auth.oauth import _save_oauth_token, _load_oauth_token

    token_path = tmp_path / "oauth_token.json"
    token_data = {
        "access_token": "test_access",
        "refresh_token": "test_refresh",
        "expires_at": 9999999999.0,
        "token_type": "Bearer",
        "scope": "read write",
    }

    with patch("zendesk_skill.auth.oauth.OAUTH_TOKEN_PATH", token_path):
        _save_oauth_token(token_data)

        # Check file permissions
        mode = token_path.stat().st_mode & 0o777
        assert mode == 0o600

        loaded = _load_oauth_token()
        assert loaded["access_token"] == "test_access"
        assert loaded["refresh_token"] == "test_refresh"


def test_oauth_token_load_missing(tmp_path):
    """Test loading when no token file exists."""
    from zendesk_skill.auth.oauth import _load_oauth_token

    token_path = tmp_path / "nonexistent.json"
    with patch("zendesk_skill.auth.oauth.OAUTH_TOKEN_PATH", token_path):
        result = _load_oauth_token()
        assert result is None


def test_oauth_provider_has_token_false(tmp_path):
    """Test has_token returns False when no token file exists."""
    from zendesk_skill.auth.oauth import OAuthProvider

    token_path = tmp_path / "nonexistent.json"
    with patch("zendesk_skill.auth.oauth.OAUTH_TOKEN_PATH", token_path):
        provider = OAuthProvider(subdomain="testco")
        assert provider.has_token() is False


def test_oauth_provider_has_token_true(tmp_path):
    """Test has_token returns True when valid token exists."""
    from zendesk_skill.auth.oauth import OAuthProvider, _save_oauth_token

    token_path = tmp_path / "oauth_token.json"
    with patch("zendesk_skill.auth.oauth.OAUTH_TOKEN_PATH", token_path):
        _save_oauth_token({
            "access_token": "test",
            "refresh_token": "test_refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
            "scope": "read write",
        })
        provider = OAuthProvider(subdomain="testco")
        assert provider.has_token() is True


def test_oauth_provider_get_auth_headers_valid_token(tmp_path):
    """Test get_auth_headers returns Bearer token when valid."""
    from zendesk_skill.auth.oauth import OAuthProvider, _save_oauth_token

    token_path = tmp_path / "oauth_token.json"
    with patch("zendesk_skill.auth.oauth.OAUTH_TOKEN_PATH", token_path):
        _save_oauth_token({
            "access_token": "my_access_token",
            "refresh_token": "my_refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
            "scope": "read write",
        })
        provider = OAuthProvider(subdomain="testco")
        headers = provider.get_auth_headers()
        assert headers["Authorization"] == "Bearer my_access_token"


def test_oauth_provider_is_auth_provider():
    """Test that OAuthProvider satisfies the AuthProvider protocol."""
    from zendesk_skill.auth.provider import AuthProvider
    from zendesk_skill.auth.oauth import OAuthProvider

    provider = OAuthProvider(subdomain="testco")
    assert isinstance(provider, AuthProvider)


def test_get_oauth_client_credentials_from_env():
    """Test loading OAuth client credentials from environment variables."""
    from zendesk_skill.auth.oauth import _get_oauth_client_credentials

    env = {
        "ZENDESK_OAUTH_CLIENT_ID": "env_client_id",
        "ZENDESK_OAUTH_CLIENT_SECRET": "env_client_secret",
    }
    with patch.dict("os.environ", env, clear=False):
        client_id, client_secret = _get_oauth_client_credentials("testco")
        assert client_id == "env_client_id"
        assert client_secret == "env_client_secret"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -k "oauth" -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

**Step 3: Write the OAuthProvider module**

Create `src/zendesk_skill/auth/oauth.py`:

```python
"""OAuthProvider — OAuth 2.0 Authorization Code + PKCE for Zendesk."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from zendesk_skill.client import ZendeskAuthError, ZendeskAPIError, _load_config_from_file

# Token storage path
CONFIG_DIR = Path.home() / ".claude" / ".zendesk-skill"
OAUTH_TOKEN_PATH = CONFIG_DIR / "oauth_token.json"

# Loopback server config
LOOPBACK_IP = "127.0.0.1"
PORT_RANGE = range(8080, 8100)


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256).

    Returns:
        Tuple of (code_verifier, code_challenge)
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _save_oauth_token(token_data: dict) -> None:
    """Save OAuth token to disk with secure permissions."""
    OAUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OAUTH_TOKEN_PATH, "w") as f:
        json.dump(token_data, f, indent=2)
    try:
        OAUTH_TOKEN_PATH.chmod(0o600)
    except OSError:
        pass


def _load_oauth_token() -> dict | None:
    """Load OAuth token from disk, or None if not found."""
    if not OAUTH_TOKEN_PATH.exists():
        return None
    try:
        with open(OAUTH_TOKEN_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _delete_oauth_token() -> bool:
    """Delete OAuth token file. Returns True if file was deleted."""
    if OAUTH_TOKEN_PATH.exists():
        OAUTH_TOKEN_PATH.unlink()
        return True
    return False


def _get_oauth_client_credentials(subdomain: str) -> tuple[str, str]:
    """Get OAuth client_id and client_secret from env vars or config file.

    Args:
        subdomain: Zendesk subdomain (needed for error message)

    Returns:
        Tuple of (client_id, client_secret)

    Raises:
        ZendeskAuthError: If credentials are not configured
    """
    client_id = os.environ.get("ZENDESK_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("ZENDESK_OAUTH_CLIENT_SECRET")

    if client_id and client_secret:
        return client_id, client_secret

    # Fall back to config file
    config = _load_config_from_file()
    client_id = client_id or config.get("oauth_client_id")
    client_secret = client_secret or config.get("oauth_client_secret")

    if not client_id or not client_secret:
        raise ZendeskAuthError(
            "OAuth client credentials not configured. Set up using:\n"
            "  Env vars: ZENDESK_OAUTH_CLIENT_ID, ZENDESK_OAUTH_CLIENT_SECRET\n"
            "  Or add oauth_client_id and oauth_client_secret to config.json\n\n"
            "Create an OAuth client in Zendesk Admin Center:\n"
            f"  https://{subdomain}.zendesk.com/admin/apps-integrations/apis/apis/oauth_clients"
        )

    return client_id, client_secret


def _find_available_port() -> int:
    """Find an available port in the loopback port range."""
    for port in PORT_RANGE:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((LOOPBACK_IP, port))
                return port
        except OSError:
            continue
    raise ZendeskAuthError(
        f"No available ports in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}. "
        "Close other applications using these ports, or use manual paste mode."
    )


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback."""

    authorization_code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self):
        """Handle the OAuth redirect callback."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
        elif "code" in params:
            _OAuthCallbackHandler.authorization_code = params["code"][0]
            _OAuthCallbackHandler.state = params.get("state", [None])[0]

        # Send response to browser
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        if _OAuthCallbackHandler.error:
            body = "<h1>Authorization Failed</h1><p>You can close this window.</p>"
        else:
            body = "<h1>Authorization Successful</h1><p>You can close this window and return to the CLI.</p>"

        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        """Suppress default HTTP logging."""
        pass


def _exchange_code_for_token(
    subdomain: str,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    """Exchange authorization code for access + refresh tokens.

    Returns:
        Token response dict with access_token, refresh_token, expires_in, etc.
    """
    token_url = f"https://{subdomain}.zendesk.com/oauth/tokens"

    with httpx.Client() as client:
        response = client.post(
            token_url,
            json={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

        if response.status_code != 200:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:200]
            raise ZendeskAuthError(f"Token exchange failed ({response.status_code}): {detail}")

        return response.json()


def _refresh_access_token(subdomain: str, refresh_token: str) -> dict:
    """Refresh an expired access token.

    Zendesk rotates both access and refresh tokens on each refresh.

    Returns:
        New token response dict.
    """
    token_url = f"https://{subdomain}.zendesk.com/oauth/tokens"

    # Get client credentials for the refresh request
    client_id, client_secret = _get_oauth_client_credentials(subdomain)

    with httpx.Client() as client:
        response = client.post(
            token_url,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

        if response.status_code != 200:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:200]
            raise ZendeskAuthError(
                f"Token refresh failed ({response.status_code}): {detail}\n"
                "Re-authenticate with: zendesk auth login-oauth"
            )

        return response.json()


class OAuthProvider:
    """Auth provider using OAuth 2.0 Authorization Code + PKCE.

    Supports two flows:
    - Loopback redirect: local HTTP server captures callback (desktop)
    - Manual paste: user pastes redirect URL back to CLI (headless)
    """

    def __init__(self, subdomain: str | None = None):
        if subdomain:
            self._subdomain = subdomain
        else:
            # Try to get subdomain from env or config
            self._subdomain = os.environ.get("ZENDESK_SUBDOMAIN")
            if not self._subdomain:
                config = _load_config_from_file()
                self._subdomain = config.get("subdomain", "")

        self._token_data: dict | None = None

    @property
    def subdomain(self) -> str:
        return self._subdomain

    def has_token(self) -> bool:
        """Check if a saved OAuth token exists on disk."""
        token = _load_oauth_token()
        return token is not None and "access_token" in token

    def get_auth_headers(self) -> dict[str, str]:
        """Return Bearer token headers, refreshing if expired."""
        if self._token_data is None:
            self._token_data = _load_oauth_token()

        if self._token_data is None:
            raise ZendeskAuthError(
                "No OAuth token found. Authenticate with: zendesk auth login-oauth"
            )

        # Check if token is expired
        expires_at = self._token_data.get("expires_at", 0)
        if time.time() >= expires_at - 60:  # 60s buffer
            self._refresh()

        return {"Authorization": f"Bearer {self._token_data['access_token']}"}

    async def validate(self) -> dict:
        """Validate OAuth credentials by calling users/me.json."""
        url = f"https://{self._subdomain}.zendesk.com/api/v2/users/me.json"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={**self.get_auth_headers(), "Accept": "application/json"},
                timeout=30.0,
            )
            if response.status_code != 200:
                raise ZendeskAPIError(
                    f"OAuth validation failed ({response.status_code})",
                    response.status_code,
                )
            user = response.json().get("user", {})
            return {
                "id": user.get("id"),
                "name": user.get("name"),
                "email": user.get("email"),
                "role": user.get("role"),
            }

    def _refresh(self) -> None:
        """Refresh the access token using the refresh token."""
        refresh_token = self._token_data.get("refresh_token")
        if not refresh_token:
            raise ZendeskAuthError(
                "No refresh token available. Re-authenticate with: zendesk auth login-oauth"
            )

        token_response = _refresh_access_token(self._subdomain, refresh_token)
        self._token_data = {
            "access_token": token_response["access_token"],
            "refresh_token": token_response.get("refresh_token", refresh_token),
            "expires_at": time.time() + token_response.get("expires_in", 7200),
            "token_type": token_response.get("token_type", "Bearer"),
            "scope": token_response.get("scope", "read write"),
        }
        _save_oauth_token(self._token_data)

    def run_auth_flow(self, manual: bool = False) -> dict:
        """Run the full OAuth authorization flow.

        Args:
            manual: If True, use manual paste mode instead of loopback.

        Returns:
            Dict with success status, user info hint, and token path.
        """
        client_id, client_secret = _get_oauth_client_credentials(self._subdomain)
        code_verifier, code_challenge = _generate_pkce_pair()
        state = secrets.token_urlsafe(32)

        if manual:
            return self._manual_flow(
                client_id, client_secret, code_verifier, code_challenge, state
            )
        else:
            return self._loopback_flow(
                client_id, client_secret, code_verifier, code_challenge, state
            )

    def _loopback_flow(
        self,
        client_id: str,
        client_secret: str,
        code_verifier: str,
        code_challenge: str,
        state: str,
    ) -> dict:
        """Run OAuth flow with loopback redirect server."""
        port = _find_available_port()
        redirect_uri = f"http://{LOOPBACK_IP}:{port}/callback"

        # Build authorization URL
        auth_params = {
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "scope": "read write",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = (
            f"https://{self._subdomain}.zendesk.com/oauth/authorizations/new?"
            + urlencode(auth_params)
        )

        # Reset handler state
        _OAuthCallbackHandler.authorization_code = None
        _OAuthCallbackHandler.state = None
        _OAuthCallbackHandler.error = None

        # Start local server
        server = HTTPServer((LOOPBACK_IP, port), _OAuthCallbackHandler)

        # Try to open browser
        try:
            browser = webbrowser.get()
            print(f"Opening browser for Zendesk authorization...", file=sys.stderr)
            webbrowser.open(auth_url)
        except webbrowser.Error:
            print(f"Open this URL in your browser:\n\n{auth_url}\n", file=sys.stderr)

        print(f"Waiting for authorization callback on port {port}...", file=sys.stderr)

        # Handle one request (the callback)
        server.handle_request()
        server.server_close()

        if _OAuthCallbackHandler.error:
            raise ZendeskAuthError(
                f"Authorization denied: {_OAuthCallbackHandler.error}"
            )

        if not _OAuthCallbackHandler.authorization_code:
            raise ZendeskAuthError("No authorization code received in callback.")

        if _OAuthCallbackHandler.state != state:
            raise ZendeskAuthError("State mismatch — possible CSRF attack.")

        # Exchange code for tokens
        return self._complete_auth(
            _OAuthCallbackHandler.authorization_code,
            client_id,
            client_secret,
            redirect_uri,
            code_verifier,
        )

    def _manual_flow(
        self,
        client_id: str,
        client_secret: str,
        code_verifier: str,
        code_challenge: str,
        state: str,
    ) -> dict:
        """Run OAuth flow with manual URL paste."""
        redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

        auth_params = {
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "scope": "read write",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = (
            f"https://{self._subdomain}.zendesk.com/oauth/authorizations/new?"
            + urlencode(auth_params)
        )

        print(f"\nOpen this URL in your browser:\n\n{auth_url}\n", file=sys.stderr)
        print("After authorizing, paste the authorization code below.", file=sys.stderr)

        code = input("Authorization code: ").strip()
        if not code:
            raise ZendeskAuthError("No authorization code provided.")

        return self._complete_auth(
            code, client_id, client_secret, redirect_uri, code_verifier
        )

    def _complete_auth(
        self,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict:
        """Exchange code for tokens and save."""
        token_response = _exchange_code_for_token(
            self._subdomain, code, client_id, client_secret, redirect_uri, code_verifier
        )

        self._token_data = {
            "access_token": token_response["access_token"],
            "refresh_token": token_response.get("refresh_token"),
            "expires_at": time.time() + token_response.get("expires_in", 7200),
            "token_type": token_response.get("token_type", "Bearer"),
            "scope": token_response.get("scope", "read write"),
        }
        _save_oauth_token(self._token_data)

        return {
            "success": True,
            "token_path": str(OAUTH_TOKEN_PATH),
            "scope": self._token_data["scope"],
        }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -k "oauth" -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/zendesk_skill/auth/oauth.py tests/test_auth.py
git commit -m "feat(auth): add OAuthProvider with PKCE, loopback, and manual paste flows"
```

---

### Task 5: Update `auth/__init__.py` — Export Public API

**Files:**
- Modify: `src/zendesk_skill/auth/__init__.py`

**Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
def test_auth_package_exports():
    """Test that auth package exports key symbols."""
    from zendesk_skill.auth import (
        AuthProvider,
        resolve_auth_provider,
        TokenAuthProvider,
        OAuthProvider,
        DEFAULT_SCOPES,
    )

    assert AuthProvider is not None
    assert resolve_auth_provider is not None
    assert TokenAuthProvider is not None
    assert OAuthProvider is not None
    assert DEFAULT_SCOPES is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_auth_package_exports -v`
Expected: FAIL with `ImportError`

**Step 3: Update the __init__.py**

Write `src/zendesk_skill/auth/__init__.py`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth.py::test_auth_package_exports -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/zendesk_skill/auth/__init__.py tests/test_auth.py
git commit -m "feat(auth): export public API from auth package"
```

---

### Task 6: Refactor `ZendeskClient` to Use AuthProvider

**Files:**
- Modify: `src/zendesk_skill/client.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_basic.py` (update existing tests if needed)

**Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
def test_zendesk_client_accepts_auth_provider():
    """Test that ZendeskClient can be constructed with an AuthProvider."""
    from zendesk_skill.auth.token_auth import TokenAuthProvider
    from zendesk_skill.client import ZendeskClient

    provider = TokenAuthProvider(
        email="test@example.com",
        token="abc123",
        subdomain="testco",
    )
    client = ZendeskClient(auth_provider=provider)

    assert client.base_url == "https://testco.zendesk.com/api/v2"
    headers = client._get_headers()
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")


def test_zendesk_client_backwards_compatible():
    """Test that ZendeskClient still works with explicit email/token/subdomain."""
    from zendesk_skill.client import ZendeskClient

    client = ZendeskClient(
        email="test@example.com",
        token="abc123",
        subdomain="testco",
    )
    assert client.base_url == "https://testco.zendesk.com/api/v2"
    headers = client._get_headers()
    assert headers["Authorization"].startswith("Basic ")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -k "zendesk_client" -v`
Expected: FAIL — `ZendeskClient` doesn't accept `auth_provider` yet

**Step 3: Refactor ZendeskClient**

Modify `src/zendesk_skill/client.py`:

The key changes to `ZendeskClient.__init__`:
- Add `auth_provider` parameter
- If `auth_provider` provided, use it
- If raw credentials provided, wrap in `TokenAuthProvider`
- If nothing provided, call `resolve_auth_provider()`
- Replace `self._auth_header` with delegation to `self._auth_provider.get_auth_headers()`

Changes to `_get_headers()`:
- Delegate to `self._auth_provider.get_auth_headers()`

Changes to `get_client()`:
- Use `resolve_auth_provider()` to create client

Full modified `ZendeskClient` class:

```python
class ZendeskClient:
    """Async HTTP client for Zendesk API."""

    def __init__(
        self,
        email: str | None = None,
        token: str | None = None,
        subdomain: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        auth_provider: Any | None = None,
    ):
        """Initialize the client.

        Args:
            email: Zendesk email (for Basic Auth backwards compatibility)
            token: Zendesk API token (for Basic Auth backwards compatibility)
            subdomain: Zendesk subdomain
            timeout: Request timeout in seconds
            auth_provider: An AuthProvider instance. If provided, email/token/subdomain are ignored.
        """
        if auth_provider is not None:
            self._auth_provider = auth_provider
        elif email and token and subdomain:
            from zendesk_skill.auth.token_auth import TokenAuthProvider
            self._auth_provider = TokenAuthProvider(
                email=email, token=token, subdomain=subdomain
            )
        else:
            from zendesk_skill.auth.provider import resolve_auth_provider
            self._auth_provider = resolve_auth_provider()

        self.timeout = timeout
        self.base_url = f"https://{self._auth_provider.subdomain}.zendesk.com/api/v2"

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        headers = self._auth_provider.get_auth_headers()
        headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        return headers
```

Also update `download_file` to use `self._auth_provider.get_auth_headers()` instead of `self._auth_header`:

```python
    async def download_file(self, url, output_path, timeout=None):
        ...
        headers=self._auth_provider.get_auth_headers(),
        ...
```

Remove the now-unused `_build_auth_header` import/call from client init (keep the function itself for backwards compatibility since tests reference it directly).

**Step 4: Run all tests to verify nothing is broken**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS (including existing `test_basic.py` tests)

**Step 5: Commit**

```bash
git add src/zendesk_skill/client.py tests/test_auth.py
git commit -m "refactor(client): delegate auth to AuthProvider, backwards compatible"
```

---

### Task 7: Add CLI Commands — `login-oauth` and `logout-oauth`

**Files:**
- Modify: `src/zendesk_skill/cli.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_basic.py` (update command counts)

**Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
def test_auth_subcommands_include_oauth():
    """Test that OAuth auth subcommands exist."""
    from zendesk_skill.cli import auth_app

    command_names = [cmd.name for cmd in auth_app.registered_commands]
    assert "login-oauth" in command_names
    assert "logout-oauth" in command_names
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_auth_subcommands_include_oauth -v`
Expected: FAIL — commands don't exist yet

**Step 3: Add the CLI commands**

Add to `src/zendesk_skill/cli.py` in the Auth Commands section (after the existing auth commands):

```python
@auth_app.command("login-oauth")
def auth_login_oauth_cmd(
    subdomain: Annotated[
        str | None,
        typer.Option("--subdomain", "-s", help="Zendesk subdomain (e.g., 'company' for company.zendesk.com)"),
    ] = None,
    client_id: Annotated[
        str | None,
        typer.Option("--client-id", help="OAuth client ID (or set ZENDESK_OAUTH_CLIENT_ID)"),
    ] = None,
    client_secret: Annotated[
        str | None,
        typer.Option("--client-secret", help="OAuth client secret (or set ZENDESK_OAUTH_CLIENT_SECRET)"),
    ] = None,
    manual: Annotated[
        bool,
        typer.Option("--manual", "-m", help="Use manual code paste instead of browser redirect"),
    ] = False,
) -> None:
    """Authenticate with Zendesk using OAuth 2.0.

    Opens a browser for authorization (or use --manual for headless environments).
    Requires an OAuth client registered in Zendesk Admin Center.
    """
    import os
    from zendesk_skill.auth.oauth import OAuthProvider
    from zendesk_skill.client import save_credentials, _load_config_from_file

    # Determine subdomain
    if not subdomain:
        subdomain = os.environ.get("ZENDESK_SUBDOMAIN")
    if not subdomain:
        config = _load_config_from_file()
        subdomain = config.get("subdomain")
    if not subdomain:
        subdomain = typer.prompt("Zendesk subdomain (e.g., 'company' for company.zendesk.com)")

    # Set client credentials as env vars if provided explicitly (so OAuthProvider finds them)
    if client_id:
        os.environ["ZENDESK_OAUTH_CLIENT_ID"] = client_id
    if client_secret:
        os.environ["ZENDESK_OAUTH_CLIENT_SECRET"] = client_secret

    try:
        provider = OAuthProvider(subdomain=subdomain)
        result = provider.run_auth_flow(manual=manual)

        output_json({
            "success": True,
            "message": "OAuth authentication successful.",
            "token_path": result["token_path"],
            "scope": result["scope"],
        })
    except Exception as e:
        output_error(str(e))


@auth_app.command("logout-oauth")
def auth_logout_oauth_cmd() -> None:
    """Remove saved OAuth token.

    Note: Does not revoke the token on Zendesk's side.
    """
    from zendesk_skill.auth.oauth import _delete_oauth_token, OAUTH_TOKEN_PATH

    deleted = _delete_oauth_token()

    output = {
        "deleted": deleted,
        "token_path": str(OAUTH_TOKEN_PATH),
    }
    if deleted:
        output["message"] = "OAuth token removed."
    else:
        output["message"] = "No OAuth token file found."

    output_json(output)
```

Also update `auth_status_cmd` in `cli.py` to report OAuth status. Add after the existing status logic:

```python
    # Check OAuth status
    from zendesk_skill.auth.oauth import _load_oauth_token, OAUTH_TOKEN_PATH
    oauth_token = _load_oauth_token()
    status["oauth"] = {
        "configured": oauth_token is not None,
        "token_path": str(OAUTH_TOKEN_PATH),
    }
```

**Step 4: Update test_basic.py command counts**

In `tests/test_basic.py`:
- Update `test_auth_subcommands_exist` to expect 8 commands (added login-oauth, logout-oauth)
- Update the assertion: `assert len(command_names) == 8`
- Add `"login-oauth"` and `"logout-oauth"` to the assertion list

**Step 5: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/zendesk_skill/cli.py tests/test_auth.py tests/test_basic.py
git commit -m "feat(cli): add login-oauth and logout-oauth commands"
```

---

### Task 8: Update `auth/__init__.py` Exports and Run Full Test Suite

**Files:**
- Verify: all files created/modified above
- Run: full test suite

**Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

**Step 2: Test CLI commands load correctly**

Run: `uv run zendesk auth --help`
Expected: Shows login, status, logout, login-oauth, logout-oauth, login-slack, status-slack, logout-slack

Run: `uv run zendesk auth login-oauth --help`
Expected: Shows --subdomain, --client-id, --client-secret, --manual options

**Step 3: Test import chain**

Run: `uv run python -c "from zendesk_skill.auth import AuthProvider, resolve_auth_provider, TokenAuthProvider, OAuthProvider; print('OK')"`
Expected: `OK`

**Step 4: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: complete OAuth 2.0 support with AuthProvider protocol"
```

---

## Summary of Files

| File | Action | Purpose |
|------|--------|---------|
| `src/zendesk_skill/auth/__init__.py` | Create | Package exports |
| `src/zendesk_skill/auth/scopes.py` | Create | OAuth scope constants |
| `src/zendesk_skill/auth/provider.py` | Create | AuthProvider protocol + factory |
| `src/zendesk_skill/auth/token_auth.py` | Create | TokenAuthProvider (Basic Auth) |
| `src/zendesk_skill/auth/oauth.py` | Create | OAuthProvider (PKCE + loopback + manual) |
| `src/zendesk_skill/client.py` | Modify | Accept AuthProvider, delegate headers |
| `src/zendesk_skill/cli.py` | Modify | Add login-oauth, logout-oauth commands |
| `tests/test_auth.py` | Create | All auth-related tests |
| `tests/test_basic.py` | Modify | Update command counts |
