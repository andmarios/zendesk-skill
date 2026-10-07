"""Attachment download paths must stay inside the attachments directory."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import zendesk_skill.operations as ops
from zendesk_skill import storage


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    """Redirect storage to tmp_path and record where download_file is asked to write."""
    base = tmp_path / "store"
    base.mkdir()
    monkeypatch.setattr(storage, "DEFAULT_STORAGE_DIR", base)
    monkeypatch.setattr(ops, "is_security_enabled", lambda: False)

    written = []

    async def fake_download(url, out_path, timeout=None):
        out_path.write_bytes(b"data")
        written.append(Path(out_path))
        return Path(out_path)

    client = MagicMock()
    client.download_file = fake_download
    monkeypatch.setattr(ops, "_get_client", lambda: client)
    return base, written


@pytest.mark.parametrize("name", [
    "../../evil.txt",
    "..%2F..%2Fevil.txt",          # decoded once by parse_qs
    "..%252F..%252Fevil.txt",      # double-encoded; decoded again by unquote
    "/etc/evil.txt",
    "..\\..\\evil.txt",
    "a/b/evil.txt",
])
@pytest.mark.asyncio
async def test_traversal_in_name_param_is_reduced_to_basename(downloads, name):
    base, written = downloads
    result = await ops.download_attachment(f"https://x.zendesk.com/a?name={name}", ticket_id="42")
    out = Path(result["file_path"])
    assert out.name == "evil.txt"
    assert out.parent == base / "42" / "attachments"
    assert not (base.parent / "evil.txt").exists()


@pytest.mark.parametrize("name", ["%20", ".", ".."])  # blank "name=" is dropped by parse_qs
@pytest.mark.asyncio
async def test_degenerate_names_fall_back_to_default(downloads, name):
    base, _ = downloads
    result = await ops.download_attachment(f"https://x.zendesk.com/a?name={name}")
    assert Path(result["file_path"]).parent == base / "attachments"
    assert Path(result["file_path"]).name == "attachment"


@pytest.mark.asyncio
async def test_path_fallback_is_sanitized(downloads):
    base, _ = downloads
    result = await ops.download_attachment("https://x.zendesk.com/files/..%2F..%2Fevil.txt")
    assert Path(result["file_path"]).parent == base / "attachments"


@pytest.mark.parametrize("ticket_id", ["../../x", "1/../../x", "abc", "-1", "0"])
@pytest.mark.asyncio
async def test_ticket_id_must_be_a_positive_integer(downloads, ticket_id):
    with pytest.raises(ValueError):
        await ops.download_attachment("https://x.zendesk.com/a?name=f.txt", ticket_id=ticket_id)


@pytest.mark.asyncio
async def test_symlinked_destination_is_refused(downloads, tmp_path):
    base, written = downloads
    adir = base / "7" / "attachments"
    adir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (adir / "f.txt").symlink_to(outside / "f.txt")

    with pytest.raises(ValueError):
        await ops.download_attachment("https://x.zendesk.com/a?name=f.txt", ticket_id="7")
    assert not written


@pytest.mark.asyncio
async def test_normal_names_and_duplicates_still_work(downloads):
    base, _ = downloads
    first = await ops.download_attachment("https://x.zendesk.com/a?name=report%20final.pdf", ticket_id="9")
    second = await ops.download_attachment("https://x.zendesk.com/a?name=report%20final.pdf", ticket_id="9")
    assert Path(first["file_path"]).name == "report final.pdf"
    assert Path(second["file_path"]).name == "report final_1.pdf"
