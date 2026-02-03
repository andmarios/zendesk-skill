"""Zendesk API client with authentication and request handling."""

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx

# Config file location
CONFIG_PATH = Path.home() / ".claude" / ".zendesk-skill" / "config.json"

# Default timeout for API requests
DEFAULT_TIMEOUT = 30.0

# Maximum redirects for attachment downloads
MAX_REDIRECTS = 5


class ZendeskClientError(Exception):
    """Base exception for Zendesk client errors."""


class ZendeskAuthError(ZendeskClientError):
    """Authentication error."""


class ZendeskAPIError(ZendeskClientError):
    """API request error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _load_config_from_file() -> dict[str, str]:
    """Load configuration from config file."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _get_credentials() -> tuple[str, str, str]:
    """Get credentials from environment variables or config file.

    Returns:
        Tuple of (email, token, subdomain)

    Raises:
        ZendeskAuthError: If required credentials are missing
    """
    # Try environment variables first
    email = os.environ.get("ZENDESK_EMAIL")
    token = os.environ.get("ZENDESK_TOKEN")
    subdomain = os.environ.get("ZENDESK_SUBDOMAIN")

    # Fall back to config file
    if not all([email, token, subdomain]):
        config = _load_config_from_file()
        email = email or config.get("email")
        token = token or config.get("token")
        subdomain = subdomain or config.get("subdomain")

    # Validate
    missing = []
    if not email:
        missing.append("email (ZENDESK_EMAIL)")
    if not token:
        missing.append("token (ZENDESK_TOKEN)")
    if not subdomain:
        missing.append("subdomain (ZENDESK_SUBDOMAIN)")

    if missing:
        raise ZendeskAuthError(
            f"Missing Zendesk credentials: {', '.join(missing)}. "
            f"Set environment variables or create config at {CONFIG_PATH}"
        )

    return email, token, subdomain


def _build_auth_header(email: str, token: str) -> str:
    """Build Basic auth header for Zendesk API.

    Zendesk uses email/token auth: {email}/token:{token}
    """
    auth_string = f"{email}/token:{token}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    return f"Basic {encoded}"


def _save_config(config: dict) -> Path:
    """Save config dict to file, preserving permissions.

    Args:
        config: Config dictionary to save

    Returns:
        Path to the config file
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    # Secure permissions (Unix only, no-op on Windows)
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass  # Windows doesn't support Unix permissions
    return CONFIG_PATH


def save_credentials(email: str, token: str, subdomain: str) -> Path:
    """Save Zendesk credentials to config file.

    Args:
        email: Zendesk email
        token: Zendesk API token
        subdomain: Zendesk subdomain

    Returns:
        Path to the config file
    """
    # Load existing config to preserve other settings (e.g., Slack)
    config = _load_config_from_file()
    config.update({"email": email, "token": token, "subdomain": subdomain})
    return _save_config(config)


def save_slack_config(webhook_url: str, channel: str) -> Path:
    """Save Slack webhook configuration to config file.

    Args:
        webhook_url: Slack incoming webhook URL
        channel: Default Slack channel (e.g., #channel-name)

    Returns:
        Path to the config file
    """
    # Load existing config to preserve Zendesk credentials
    config = _load_config_from_file()
    config["slack_webhook_url"] = webhook_url
    config["slack_channel"] = channel
    return _save_config(config)


def get_slack_config() -> tuple[str, str] | None:
    """Get Slack configuration from environment or config file.

    Returns:
        Tuple of (webhook_url, channel) or None if not configured
    """
    # Try environment variables first
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    channel = os.environ.get("SLACK_CHANNEL")

    # Fall back to config file
    if not webhook_url or not channel:
        config = _load_config_from_file()
        webhook_url = webhook_url or config.get("slack_webhook_url")
        channel = channel or config.get("slack_channel")

    if webhook_url and channel:
        return webhook_url, channel
    return None


def get_slack_status() -> dict:
    """Get Slack configuration status.

    Returns:
        Dict with configured status and details
    """
    env_webhook = os.environ.get("SLACK_WEBHOOK_URL")
    env_channel = os.environ.get("SLACK_CHANNEL")
    env_vars_set = []
    if env_webhook:
        env_vars_set.append("SLACK_WEBHOOK_URL")
    if env_channel:
        env_vars_set.append("SLACK_CHANNEL")

    config = _load_config_from_file()
    config_webhook = config.get("slack_webhook_url")
    config_channel = config.get("slack_channel")

    # Determine source
    source = None
    configured = False
    if env_webhook and env_channel:
        source = "env"
        configured = True
    elif config_webhook and config_channel:
        source = "config"
        configured = True

    return {
        "configured": configured,
        "source": source,
        "channel": config_channel or env_channel,
        "env_vars_set": env_vars_set,
        "has_config": bool(config_webhook and config_channel),
    }


def delete_slack_config() -> bool:
    """Remove Slack configuration from config file.

    Returns:
        True if Slack config was removed, False if it didn't exist
    """
    config = _load_config_from_file()
    had_slack = "slack_webhook_url" in config or "slack_channel" in config
    config.pop("slack_webhook_url", None)
    config.pop("slack_channel", None)
    if had_slack:
        _save_config(config)
    return had_slack


def delete_credentials() -> bool:
    """Delete config file if it exists.

    Returns:
        True if config file was deleted, False if it didn't exist
    """
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
        return True
    return False


def get_business_hours_config() -> dict | None:
    """Get business hours configuration from config file.

    Returns:
        Dict with business_hours and oncall config, or None if not configured.

    Example config:
        {
            "business_hours": {
                "timezone": "Europe/Berlin",
                "start_hour": 9,
                "end_hour": 18,
                "workdays": [0, 1, 2, 3, 4]  # Monday=0, Sunday=6
            },
            "oncall": {
                "enabled": true,
                "start_hour": 19,
                "end_hour": 9,
                "customers": ["acme.com"],  # Empty = all customers
                "priorities": ["urgent"]
            }
        }
    """
    config = _load_config_from_file()
    business_hours = config.get("business_hours")
    oncall = config.get("oncall")

    if not business_hours:
        return None

    return {
        "business_hours": business_hours,
        "oncall": oncall,
    }


def save_business_hours_config(
    timezone: str = "Europe/Berlin",
    start_hour: int = 9,
    end_hour: int = 18,
    workdays: list[int] | None = None,
    oncall_enabled: bool = False,
    oncall_start_hour: int = 19,
    oncall_end_hour: int = 9,
    oncall_customers: list[str] | None = None,
    oncall_priorities: list[str] | None = None,
) -> Path:
    """Save business hours configuration to config file.

    Args:
        timezone: Timezone name (e.g., "Europe/Berlin")
        start_hour: Business hours start (0-23)
        end_hour: Business hours end (0-23)
        workdays: List of workdays (0=Monday, 6=Sunday). Default: Mon-Fri
        oncall_enabled: Whether on-call tracking is enabled
        oncall_start_hour: On-call period start hour
        oncall_end_hour: On-call period end hour
        oncall_customers: Customer domains for on-call (empty = all)
        oncall_priorities: Ticket priorities for on-call

    Returns:
        Path to the config file
    """
    config = _load_config_from_file()

    config["business_hours"] = {
        "timezone": timezone,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "workdays": workdays if workdays is not None else [0, 1, 2, 3, 4],
    }

    config["oncall"] = {
        "enabled": oncall_enabled,
        "start_hour": oncall_start_hour,
        "end_hour": oncall_end_hour,
        "customers": oncall_customers or [],
        "priorities": oncall_priorities or ["urgent"],
    }

    return _save_config(config)


def get_auth_status() -> dict:
    """Get current authentication configuration status.

    Returns:
        Dict with:
            - configured: bool - whether credentials are available
            - source: str | None - "env", "config", or None
            - config_path: str - path to config file
            - env_vars_set: list - which env vars are set
            - has_config_file: bool - whether config file exists
    """
    # Check environment variables
    env_email = os.environ.get("ZENDESK_EMAIL")
    env_token = os.environ.get("ZENDESK_TOKEN")
    env_subdomain = os.environ.get("ZENDESK_SUBDOMAIN")
    env_vars_set = []
    if env_email:
        env_vars_set.append("ZENDESK_EMAIL")
    if env_token:
        env_vars_set.append("ZENDESK_TOKEN")
    if env_subdomain:
        env_vars_set.append("ZENDESK_SUBDOMAIN")

    env_complete = all([env_email, env_token, env_subdomain])

    # Check config file
    has_config_file = CONFIG_PATH.exists()
    config_complete = False
    if has_config_file:
        config = _load_config_from_file()
        config_complete = all([
            config.get("email"),
            config.get("token"),
            config.get("subdomain"),
        ])

    # Determine source (env takes precedence)
    source = None
    configured = False
    if env_complete:
        source = "env"
        configured = True
    elif config_complete:
        source = "config"
        configured = True

    return {
        "configured": configured,
        "source": source,
        "config_path": str(CONFIG_PATH),
        "env_vars_set": env_vars_set,
        "has_config_file": has_config_file,
    }


class ZendeskClient:
    """Async HTTP client for Zendesk API."""

    def __init__(
        self,
        email: str | None = None,
        token: str | None = None,
        subdomain: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Initialize the client.

        If credentials are not provided, they will be loaded from
        environment variables or config file.
        """
        if email and token and subdomain:
            self.email = email
            self.token = token
            self.subdomain = subdomain
        else:
            self.email, self.token, self.subdomain = _get_credentials()

        self.timeout = timeout
        self.base_url = f"https://{self.subdomain}.zendesk.com/api/v2"
        self._auth_header = _build_auth_header(self.email, self.token)

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Make an API request to Zendesk.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (without base URL)
            params: Query parameters
            json_data: JSON body data
            timeout: Request timeout override

        Returns:
            Parsed JSON response

        Raises:
            ZendeskAPIError: On API errors
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    params=params,
                    json=json_data,
                    timeout=timeout or self.timeout,
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                error_msg = self._format_http_error(e)
                raise ZendeskAPIError(error_msg, e.response.status_code) from e
            except httpx.TimeoutException as e:
                raise ZendeskAPIError(
                    "Request timed out. The Zendesk API may be slow or unavailable."
                ) from e
            except httpx.RequestError as e:
                raise ZendeskAPIError(f"Request failed: {e}") from e

    def _format_http_error(self, error: httpx.HTTPStatusError) -> str:
        """Format HTTP error into user-friendly message."""
        status = error.response.status_code

        # Try to extract error details from response
        try:
            data = error.response.json()
            if "error" in data:
                detail = data.get("description", data["error"])
            elif "errors" in data:
                detail = "; ".join(str(e) for e in data["errors"])
            else:
                detail = str(data)
        except Exception:
            detail = error.response.text[:200] if error.response.text else ""

        if status == 401:
            return "Authentication failed. Check your Zendesk email and API token."
        elif status == 403:
            return f"Permission denied. You don't have access to this resource. {detail}"
        elif status == 404:
            return f"Resource not found. {detail}"
        elif status == 422:
            return f"Invalid request: {detail}"
        elif status == 429:
            return "Rate limit exceeded. Please wait before making more requests."
        elif status >= 500:
            return f"Zendesk server error ({status}). Try again later. {detail}"
        else:
            return f"API error ({status}): {detail}"

    async def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Make a GET request."""
        return await self.request("GET", endpoint, params=params, timeout=timeout)

    async def post(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Make a POST request."""
        return await self.request("POST", endpoint, json_data=json_data, timeout=timeout)

    async def put(
        self,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Make a PUT request."""
        return await self.request("PUT", endpoint, json_data=json_data, timeout=timeout)

    async def delete(
        self,
        endpoint: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Make a DELETE request."""
        return await self.request("DELETE", endpoint, timeout=timeout)

    async def download_file(
        self,
        url: str,
        output_path: Path,
        timeout: float | None = None,
    ) -> Path:
        """Download a file (e.g., attachment) following redirects.

        Args:
            url: URL to download
            output_path: Where to save the file
            timeout: Request timeout override

        Returns:
            Path to downloaded file

        Raises:
            ZendeskAPIError: On download errors
        """
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        ) as client:
            try:
                response = await client.get(
                    url,
                    headers={"Authorization": self._auth_header},
                    timeout=timeout or self.timeout,
                )
                response.raise_for_status()

                # Ensure parent directory exists
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Write file
                with open(output_path, "wb") as f:
                    f.write(response.content)

                return output_path

            except httpx.HTTPStatusError as e:
                error_msg = self._format_http_error(e)
                raise ZendeskAPIError(error_msg, e.response.status_code) from e
            except httpx.TimeoutException as e:
                raise ZendeskAPIError("Download timed out.") from e
            except httpx.RequestError as e:
                raise ZendeskAPIError(f"Download failed: {e}") from e


# Module-level singleton for convenience
_client: ZendeskClient | None = None


def get_client() -> ZendeskClient:
    """Get or create the default Zendesk client singleton."""
    global _client
    if _client is None:
        _client = ZendeskClient()
    return _client


def reset_client() -> None:
    """Reset the client singleton (useful for testing)."""
    global _client
    _client = None
