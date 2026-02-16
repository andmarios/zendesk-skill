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
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from zendesk_skill.auth.scopes import DEFAULT_SCOPES
from zendesk_skill.client import ZendeskAuthError, ZendeskAPIError, _load_config_from_file, _save_config

# Token storage path
CONFIG_DIR = Path.home() / ".claude" / ".zendesk-skill"
OAUTH_TOKEN_PATH = CONFIG_DIR / "oauth_token.json"

# Loopback server config
LOOPBACK_IP = "127.0.0.1"
PORT_RANGE = range(8080, 8090)


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


def _get_oauth_client_credentials(
    subdomain: str,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> tuple[str, str]:
    """Get OAuth client_id and client_secret.

    Resolution order: explicit args -> env vars -> config file.

    Args:
        subdomain: Zendesk subdomain (needed for error message)
        client_id: Explicit client ID (takes priority)
        client_secret: Explicit client secret (takes priority)

    Returns:
        Tuple of (client_id, client_secret)

    Raises:
        ZendeskAuthError: If credentials are not configured
    """
    client_id = client_id or os.environ.get("ZENDESK_OAUTH_CLIENT_ID")
    client_secret = client_secret or os.environ.get("ZENDESK_OAUTH_CLIENT_SECRET")

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


def get_oauth_status() -> dict:
    """Get OAuth token status (public API for CLI/status commands).

    Returns:
        Dict with 'configured' bool and 'token_path' string.
    """
    token = _load_oauth_token()
    return {
        "configured": token is not None and "access_token" in token,
        "token_path": str(OAUTH_TOKEN_PATH),
    }


def delete_oauth_token() -> bool:
    """Delete OAuth token file (public API). Returns True if file was deleted."""
    return _delete_oauth_token()


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
    """HTTP handler that captures the OAuth callback.

    Stores result on the server instance (self.server.oauth_result)
    instead of class variables to avoid shared mutable state.
    """

    def do_GET(self):
        """Handle the OAuth redirect callback."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        result = self.server.oauth_result

        if "error" in params:
            result["error"] = params["error"][0]
        elif "code" in params:
            result["code"] = params["code"][0]
            result["state"] = params.get("state", [None])[0]

        # Send response to browser
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        if result.get("error"):
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
            self._subdomain = os.environ.get("ZENDESK_SUBDOMAIN") or ""
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
            "scope": token_response.get("scope", DEFAULT_SCOPES),
        }
        _save_oauth_token(self._token_data)

    def run_auth_flow(
        self,
        manual: bool = False,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> dict:
        """Run the full OAuth authorization flow.

        Args:
            manual: If True, use manual paste mode instead of loopback.
            client_id: Explicit OAuth client ID (skips env/config lookup).
            client_secret: Explicit OAuth client secret (skips env/config lookup).

        Returns:
            Dict with success status, user info hint, and token path.
        """
        client_id, client_secret = _get_oauth_client_credentials(
            self._subdomain, client_id, client_secret
        )
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
            "scope": DEFAULT_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        auth_url = (
            f"https://{self._subdomain}.zendesk.com/oauth/authorizations/new?"
            + urlencode(auth_params)
        )

        # Start local server with result storage on the instance
        server = HTTPServer((LOOPBACK_IP, port), _OAuthCallbackHandler)
        server.oauth_result = {}
        server.timeout = 300  # 5 minute timeout

        # Try to open browser
        try:
            webbrowser.get()
            print("Opening browser for Zendesk authorization...", file=sys.stderr)
            webbrowser.open(auth_url)
        except webbrowser.Error:
            print(f"Open this URL in your browser:\n\n{auth_url}\n", file=sys.stderr)

        print(f"Waiting for authorization callback on port {port}...", file=sys.stderr)

        # Handle one request (the callback)
        server.handle_request()
        server.server_close()

        result = server.oauth_result

        if result.get("error"):
            raise ZendeskAuthError(
                f"Authorization denied: {result['error']}"
            )

        if not result.get("code"):
            raise ZendeskAuthError(
                "No authorization code received. The request may have timed out."
            )

        if result.get("state") != state:
            raise ZendeskAuthError("State mismatch — possible CSRF attack.")

        # Exchange code for tokens
        return self._complete_auth(
            result["code"],
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
            "scope": DEFAULT_SCOPES,
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
        """Exchange code for tokens, save tokens and client credentials."""
        token_response = _exchange_code_for_token(
            self._subdomain, code, client_id, client_secret, redirect_uri, code_verifier
        )

        self._token_data = {
            "access_token": token_response["access_token"],
            "refresh_token": token_response.get("refresh_token"),
            "expires_at": time.time() + token_response.get("expires_in", 7200),
            "token_type": token_response.get("token_type", "Bearer"),
            "scope": token_response.get("scope", DEFAULT_SCOPES),
        }
        _save_oauth_token(self._token_data)

        # Persist client credentials so token refresh works without env vars
        config = _load_config_from_file()
        config["oauth_client_id"] = client_id
        config["oauth_client_secret"] = client_secret
        _save_config(config)

        return {
            "success": True,
            "token_path": str(OAUTH_TOKEN_PATH),
            "scope": self._token_data["scope"],
        }
