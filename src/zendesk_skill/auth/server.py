"""Server auth provider -- delegates OAuth to an oauth-token-relay server."""

from __future__ import annotations

import hashlib
import os
import secrets
import base64
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from zendesk_skill.auth.scopes import DEFAULT_SCOPES
from zendesk_skill.auth.oauth import OAUTH_TOKEN_PATH
from zendesk_skill.client import CONFIG_DIR, ZendeskAuthError, ZendeskAPIError, _load_config_from_file, _get_encryption_key
from zendesk_skill.crypto import save_encrypted, load_encrypted, delete_encrypted

# Storage paths
SERVER_TOKEN_PATH = CONFIG_DIR / "server_token.json"


class ServerAuthProvider:
    """Auth provider that delegates OAuth to an oauth-token-relay server.

    The server handles:
    - OAuth 2.1 authentication of the CLI user (PKCE or device flow)
    - Provider resolution (which Zendesk OAuth app to use)
    - Token exchange with Zendesk (using server-held client_secret)
    - Token refresh via the server

    The CLI holds:
    - A server JWT (for authenticating to the relay server)
    - Zendesk API tokens (obtained through the relay, stored locally)
    """

    def __init__(
        self,
        server_url: str,
        config: dict | None = None,
    ):
        self.server_url = server_url.rstrip("/")
        self._config = config or _load_config_from_file()

        # Resolve subdomain: config -> env -> error
        subdomain = self._config.get("subdomain") or os.environ.get("ZENDESK_SUBDOMAIN")
        if not subdomain:
            raise ZendeskAuthError(
                "Zendesk subdomain is required for server auth mode.\n"
                "Set it via config (subdomain) or ZENDESK_SUBDOMAIN env var."
            )
        self._subdomain = subdomain

        # Optional: explicit relay provider name (auto-discovers if not set)
        self._server_provider: str | None = self._config.get("server_provider")

        self._token_data: dict | None = None
        self._server_token: dict[str, Any] | None = None

    # -- AuthProvider protocol methods -----------------------------------------

    @property
    def subdomain(self) -> str:
        """Zendesk subdomain (e.g., 'company' for company.zendesk.com)."""
        return self._subdomain

    def get_auth_headers(self) -> dict[str, str]:
        """Return Bearer token headers, refreshing via server if expired.

        Loads cached token from disk, checks expiry with 60s buffer,
        and refreshes through the relay server when needed.
        """
        if self._token_data is None:
            self._token_data = self._load_zendesk_token()

        if self._token_data is None:
            # No token at all -- run the full server auth flow
            self._run_server_auth_flow()

        # Check expiry (60s buffer)
        expires_at = self._token_data.get("expires_at", 0)  # type: ignore[union-attr]
        if time.time() >= expires_at - 60:
            refresh_token = self._token_data.get("refresh_token")  # type: ignore[union-attr]
            if refresh_token:
                try:
                    self._refresh_via_server(refresh_token)
                except Exception:
                    # Refresh failed -- try full re-auth
                    self._token_data = None
                    self._run_server_auth_flow()
            else:
                # No refresh token -- full re-auth
                self._token_data = None
                self._run_server_auth_flow()

        return {"Authorization": f"Bearer {self._token_data['access_token']}"}  # type: ignore[index]

    async def validate(self) -> dict:
        """Validate credentials by calling users/me.json.

        Returns:
            Dict with user info: id, name, email, role
        """
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

    def has_token(self) -> bool:
        """Check if a saved OAuth token exists on disk."""
        token = self._load_zendesk_token()
        return token is not None and "access_token" in token

    def delete_token(self) -> bool:
        """Delete the Zendesk API token file."""
        self._token_data = None
        return delete_encrypted(OAUTH_TOKEN_PATH)

    # -- Server authentication (CLI user -> relay server) ----------------------

    def server_login(self, device_flow: bool = False) -> None:
        """Authenticate the CLI user to the relay server, then obtain a Zendesk token.

        Two-step flow:
        1. Authenticate CLI user to the relay (PKCE or device flow)
        2. Initiate Zendesk OAuth via the relay to get an API token

        Both steps happen in one go so the user is fully authenticated afterward.
        """
        if device_flow:
            self._server_device_flow()
        else:
            self._server_pkce_flow()

        # Now chain the Zendesk OAuth so the user is fully authenticated
        if not self.has_token():
            self._run_server_auth_flow()

    def server_logout(self) -> None:
        """Revoke server token and delete local server_token.json."""
        server_token = self._load_server_token()
        if server_token:
            # Best-effort revoke on server
            try:
                self._server_request(
                    "POST",
                    "/oauth/revoke",
                    json_data={"token": server_token.get("refresh_token", "")},
                )
            except Exception:
                pass  # Server might be unreachable; still clean up locally

        delete_encrypted(SERVER_TOKEN_PATH)
        self._server_token = None

    def server_status(self) -> dict[str, Any]:
        """Check server connection and authentication status."""
        result: dict[str, Any] = {"server_url": self.server_url}

        # Check server health
        try:
            resp = httpx.get(f"{self.server_url}/health", timeout=10)
            health = resp.json()
            result["server_reachable"] = True
            result["server_status"] = health.get("status", "unknown")
            result["providers"] = health.get("providers", [])
        except Exception as e:
            result["server_reachable"] = False
            result["server_error"] = str(e)
            return result

        # Check local server token
        server_token = self._load_server_token()
        if server_token:
            result["authenticated"] = True
            result["server_token_path"] = str(SERVER_TOKEN_PATH)
        else:
            result["authenticated"] = False
            result["hint"] = "Run 'zd-cli auth server-login' to authenticate."

        return result

    # -- Private: server PKCE flow ---------------------------------------------

    def _server_pkce_flow(self) -> None:
        """OAuth 2.1 PKCE authorization code flow against the relay server."""
        # Generate PKCE verifier + challenge
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()

        state = secrets.token_urlsafe(32)

        # Build authorize URL
        redirect_uri = f"{self.server_url}/oauth/cli-callback"
        params = {
            "response_type": "code",
            "client_id": "cli",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "redirect_uri": redirect_uri,
        }
        auth_url = f"{self.server_url}/oauth/authorize?{urlencode(params)}"

        print("\n" + "=" * 60, file=sys.stderr)
        print("OAuth Token Relay Server Authentication Required", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        # Try to open browser
        try:
            can_open = webbrowser.get() is not None
        except webbrowser.Error:
            can_open = False

        if can_open:
            print(f"\nOpening browser to:\n{auth_url}\n", file=sys.stderr)
            webbrowser.open(auth_url)
        else:
            print(f"\nOpen this URL in your browser:\n{auth_url}\n", file=sys.stderr)

        # Poll for authorization code (server stores it when browser callback arrives)
        print("Waiting for authorization...\n", file=sys.stderr)

        code = None
        deadline = time.monotonic() + 600  # 10 minute timeout
        while time.monotonic() < deadline:
            time.sleep(2)

            try:
                poll_resp = httpx.get(
                    f"{self.server_url}/oauth/cli-poll",
                    params={"state": state},
                    timeout=10,
                )
            except httpx.RequestError:
                continue  # Network hiccup, retry

            if poll_resp.status_code == 200:
                code = poll_resp.json().get("code")
                break
            elif poll_resp.status_code == 202:
                continue  # Still pending
            else:
                raise ZendeskAuthError(
                    f"Server authentication failed: "
                    f"Poll error: {poll_resp.status_code} {poll_resp.text}"
                )

        if not code:
            raise ZendeskAuthError("Authorization timed out. Please try again.")

        # Exchange code for tokens
        resp = httpx.post(
            f"{self.server_url}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": "cli",
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            raise ZendeskAuthError(
                f"Server authentication failed: "
                f"Status {resp.status_code}: {resp.text}"
            )

        token_data = resp.json()
        self._save_server_token(token_data)
        print("\nServer authentication successful!\n", file=sys.stderr)

    # -- Private: server device flow -------------------------------------------

    def _server_device_flow(self) -> None:
        """OAuth 2.1 device authorization flow (for headless/SSH)."""
        # Initiate device flow
        resp = httpx.post(f"{self.server_url}/oauth/device", timeout=30)

        if resp.status_code != 200:
            raise ZendeskAuthError(
                f"Device flow initiation failed: "
                f"Status {resp.status_code}: {resp.text}"
            )

        device_data = resp.json()
        user_code = device_data["user_code"]
        verification_uri = device_data["verification_uri"]
        device_code = device_data["device_code"]
        interval = device_data.get("interval", 5)
        expires_in = device_data.get("expires_in", 300)

        print("\n" + "=" * 60, file=sys.stderr)
        print("Server Authentication (Device Flow)", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"\nGo to: {verification_uri}", file=sys.stderr)
        print(f"Enter code: {user_code}\n", file=sys.stderr)
        print("Waiting for authorization...", file=sys.stderr)

        # Poll for completion
        deadline = time.monotonic() + expires_in
        while time.monotonic() < deadline:
            time.sleep(interval)

            resp = httpx.post(
                f"{self.server_url}/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                },
                timeout=30,
            )

            if resp.status_code == 200:
                token_data = resp.json()
                self._save_server_token(token_data)
                print("\nServer authentication successful!\n", file=sys.stderr)
                return

            error = resp.json().get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            else:
                raise ZendeskAuthError(
                    f"Device flow failed: "
                    f"Error: {error} -- {resp.json().get('error_description', '')}"
                )

        raise ZendeskAuthError("Device flow timed out. Please try again.")

    # -- Private: Zendesk token relay ------------------------------------------

    def _run_server_auth_flow(self) -> None:
        """Initiate upstream Zendesk OAuth via the server relay.

        The CLI sends Zendesk scopes -- the server passes them through
        to the upstream Zendesk OAuth provider as-is.
        """
        server_token = self._ensure_server_token()
        scopes = DEFAULT_SCOPES.split()

        # Discover relay provider from server health endpoint
        provider = self._discover_relay_provider()

        # Start the flow
        resp = self._server_request(
            "POST",
            "/auth/tokens/start",
            json_data={"scopes": scopes, "provider": provider},
            bearer_token=server_token["access_token"],
        )

        if resp.status_code != 200:
            raise ZendeskAuthError(
                f"Failed to start token relay: "
                f"Status {resp.status_code}: {resp.text}"
            )

        start_data = resp.json()
        auth_url = start_data["auth_url"]
        session_id = start_data["session_id"]

        print("\n" + "=" * 60, file=sys.stderr)
        print("Zendesk OAuth Authorization Required for zd-cli", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        try:
            can_open = webbrowser.get() is not None
        except webbrowser.Error:
            can_open = False

        if can_open:
            print(f"\nOpening browser to:\n{auth_url}\n", file=sys.stderr)
            webbrowser.open(auth_url)
        else:
            print(f"\nOpen this URL in your browser:\n{auth_url}\n", file=sys.stderr)

        print("Waiting for authorization...\n", file=sys.stderr)

        # Poll for completion (server holds tokens for ~5min)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            time.sleep(3)

            resp = self._server_request(
                "POST",
                "/auth/tokens/complete",
                json_data={"session_id": session_id},
                bearer_token=server_token["access_token"],
            )

            if resp.status_code == 200:
                token_data = resp.json()
                self._save_zendesk_token(token_data)
                print("Authorization successful! Token saved.\n", file=sys.stderr)
                return
            elif resp.status_code == 202:
                # Still pending
                continue
            else:
                raise ZendeskAuthError(
                    f"Token relay failed: "
                    f"Status {resp.status_code}: {resp.text}"
                )

        raise ZendeskAuthError("Authorization timed out. Please try again.")

    def _refresh_via_server(self, refresh_token: str) -> None:
        """Refresh Zendesk API token via the server relay."""
        server_token = self._ensure_server_token()

        resp = self._server_request(
            "POST",
            "/auth/tokens/refresh",
            json_data={
                "refresh_token": refresh_token,
                "provider": self._discover_relay_provider(),
            },
            bearer_token=server_token["access_token"],
        )

        if resp.status_code != 200:
            raise ZendeskAuthError(
                f"Token refresh via server failed: "
                f"Status {resp.status_code}: {resp.text}"
            )

        token_data = resp.json()
        self._save_zendesk_token(token_data, refresh_token=refresh_token)

    # -- Private: helpers ------------------------------------------------------

    def _discover_relay_provider(self) -> str:
        """Resolve which relay provider to use.

        Priority:
        1. Explicit config (server_provider in config)
        2. Auto-discover from /health if exactly one provider exists
        3. Error with available options
        """
        # Check explicit config first
        if self._server_provider:
            return self._server_provider

        # Auto-discover from server
        try:
            resp = httpx.get(f"{self.server_url}/health", timeout=10)
            health = resp.json()
            providers = health.get("providers", [])
        except Exception as e:
            raise ZendeskAuthError(
                f"Cannot discover relay providers: Server health check failed: {e}"
            )

        if not providers:
            raise ZendeskAuthError("No relay providers configured on the server.")

        if len(providers) == 1:
            return providers[0]

        raise ZendeskAuthError(
            f"Multiple relay providers available: {', '.join(providers)}. "
            "Set one with: zd-cli auth set-mode --mode server --url <url> --provider <name>"
        )

    def _server_request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        bearer_token: str | None = None,
    ) -> httpx.Response:
        """Make an authenticated request to the relay server.

        Automatically refreshes the server JWT on 401 and retries once.
        """
        headers: dict[str, str] = {}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        resp = httpx.request(
            method,
            f"{self.server_url}{path}",
            json=json_data,
            headers=headers,
            timeout=30,
        )

        if resp.status_code == 401 and bearer_token:
            new_token = self._refresh_server_token()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                resp = httpx.request(
                    method,
                    f"{self.server_url}{path}",
                    json=json_data,
                    headers=headers,
                    timeout=30,
                )

        return resp

    def _refresh_server_token(self) -> str | None:
        """Refresh the server JWT using the refresh_token.

        Returns the new access_token, or None if refresh failed.
        """
        server_token = self._load_server_token()
        if not server_token or not server_token.get("refresh_token"):
            return None

        try:
            resp = httpx.post(
                f"{self.server_url}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": server_token["refresh_token"],
                    "client_id": "cli",
                },
                timeout=30,
            )
        except httpx.RequestError:
            return None

        if resp.status_code != 200:
            return None

        new_token_data = resp.json()
        # Preserve the refresh_token if the server didn't issue a new one
        if "refresh_token" not in new_token_data:
            new_token_data["refresh_token"] = server_token["refresh_token"]
        self._save_server_token(new_token_data)
        return new_token_data.get("access_token")

    def _load_server_token(self) -> dict[str, Any] | None:
        """Load the server JWT from disk."""
        if self._server_token:
            return self._server_token

        data = load_encrypted(SERVER_TOKEN_PATH, _get_encryption_key())
        if data:
            self._server_token = data
        return data

    def _save_server_token(self, token_data: dict[str, Any]) -> None:
        """Save server JWT to disk."""
        save_encrypted(SERVER_TOKEN_PATH, token_data, _get_encryption_key())
        self._server_token = token_data

    def _ensure_server_token(self, auto_login: bool = True) -> dict[str, Any]:
        """Load server token, auto-triggering login if needed.

        When auto_login is True (the default), missing server tokens trigger
        the PKCE login flow automatically so the user never needs to run
        'zd-cli auth server-login' as a separate step.
        """
        token = self._load_server_token()
        if token:
            return token

        if not auto_login:
            raise ZendeskAuthError(
                "Not authenticated to the server. "
                "Run 'zd-cli auth server-login' first."
            )

        # Auto-trigger server login -- one fewer manual step for the user
        print("No server token found -- starting server authentication...\n", file=sys.stderr)
        self.server_login()

        token = self._load_server_token()
        if not token:
            raise ZendeskAuthError(
                "Server login completed but no token was saved. "
                "Try again with 'zd-cli auth server-login'."
            )
        return token

    # -- Private: Zendesk token I/O --------------------------------------------

    def _load_zendesk_token(self) -> dict | None:
        """Load Zendesk API token from disk (oauth_token.json format)."""
        data = load_encrypted(OAUTH_TOKEN_PATH, _get_encryption_key())
        if not data or not data.get("access_token"):
            return None

        self._token_data = data
        return data

    def _save_zendesk_token(
        self,
        token_data: dict[str, Any],
        refresh_token: str | None = None,
    ) -> None:
        """Save Zendesk API tokens in oauth_token.json format.

        Format matches OAuthProvider so either provider can read the token:
        {
            "access_token": "...",
            "refresh_token": "...",
            "token_type": "bearer",
            "scope": "read write",
            "expires_at": 1234567890.0
        }
        """
        resolved_refresh = token_data.get("refresh_token", refresh_token)
        expires_in = token_data.get("expires_in", 7200)

        self._token_data = {
            "access_token": token_data["access_token"],
            "refresh_token": resolved_refresh,
            "token_type": token_data.get("token_type", "bearer"),
            "scope": token_data.get("scope", DEFAULT_SCOPES),
            "expires_at": time.time() + expires_in,
        }

        save_encrypted(OAUTH_TOKEN_PATH, self._token_data, _get_encryption_key())
