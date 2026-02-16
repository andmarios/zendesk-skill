"""Tests for the auth package."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_scopes_default():
    """Test that DEFAULT_SCOPES is a space-separated string."""
    from zendesk_skill.auth.scopes import DEFAULT_SCOPES

    assert isinstance(DEFAULT_SCOPES, str)
    assert "read" in DEFAULT_SCOPES
    assert "write" in DEFAULT_SCOPES


def test_auth_provider_protocol():
    """Test that AuthProvider is a runtime-checkable protocol."""
    from zendesk_skill.auth.provider import AuthProvider

    assert hasattr(AuthProvider, "subdomain")
    assert hasattr(AuthProvider, "get_auth_headers")
    assert hasattr(AuthProvider, "validate")


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


def test_auth_subcommands_include_oauth():
    """Test that OAuth auth subcommands exist."""
    from zendesk_skill.cli import auth_app

    command_names = [cmd.name for cmd in auth_app.registered_commands]
    assert "login-oauth" in command_names
    assert "logout-oauth" in command_names
