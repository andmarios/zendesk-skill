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
