"""Credentials must only be sent to the account's own Zendesk host."""

import httpx
import pytest

from zendesk_skill.client import ZendeskClient, ZendeskClientError


class Auth:
    subdomain = "acme"

    def get_auth_headers(self):
        return {"Authorization": "Bearer SECRET"}


@pytest.fixture
def client():
    return ZendeskClient(auth_provider=Auth())


@pytest.fixture
def requests_seen(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"file")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://evil.example.com/a?name=f.txt",
    "https://acme.zendesk.com.evil.com/a",        # suffix trick
    "https://acme.zendesk.com@evil.com/a",        # userinfo trick
    "https://evil.com/acme.zendesk.com",          # host in path
    "https://other.zendesk.com/attachments/x",    # a different account
    "http://acme.zendesk.com/attachments/x",      # no TLS
    "https://acme.zendesk.com:8443/attachments/x",
    "ftp://acme.zendesk.com/x",
    "file:///etc/passwd",
    "//acme.zendesk.com/x",
    "not a url",
])
async def test_other_hosts_are_refused_before_any_request(client, requests_seen, tmp_path, url):
    with pytest.raises(ZendeskClientError):
        await client.download_file(url, tmp_path / "out")
    assert requests_seen == []
    assert not (tmp_path / "out").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://acme.zendesk.com/attachments/token/abc/?name=f.txt",
    "https://ACME.zendesk.com/attachments/token/abc/",
    "https://acme.zendesk.com:443/attachments/token/abc/",
])
async def test_own_zendesk_host_still_works(client, requests_seen, tmp_path, url):
    out = await client.download_file(url, tmp_path / "out")
    assert out.read_bytes() == b"file"
    assert requests_seen[0].headers["Authorization"] == "Bearer SECRET"
