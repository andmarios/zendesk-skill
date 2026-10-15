"""Tests for owner-only storage."""

import os
import stat
from pathlib import Path

import pytest

from zendesk_skill import storage


@pytest.fixture
def base(tmp_path, monkeypatch):
    d = tmp_path / f"zd-cli-{os.getuid()}"
    monkeypatch.setattr(storage, "DEFAULT_STORAGE_DIR", d)
    return d


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_dirs_and_files_are_owner_only(base):
    path, _ = storage.save_response("ticket", {"ticket_id": "1"}, {"ticket": {}}, ticket_id="1")
    assert mode(base) == 0o700
    assert mode(base / "1") == 0o700
    assert mode(Path(path)) == 0o600


def test_existing_loose_permissions_are_tightened(base):
    base.mkdir(mode=0o755)
    os.chmod(base, 0o755)
    old = base / "old.json"
    old.write_text("{}")
    os.chmod(old, 0o644)

    storage.ensure_private_dir(base / "5")
    storage.write_private_text(old, "{}")

    assert mode(base) == 0o700 and mode(base / "5") == 0o700
    assert mode(old) == 0o600


def test_refuses_symlinked_base(base, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    base.symlink_to(target)
    with pytest.raises(storage.StorageError):
        storage.ensure_private_dir(base / "1")


def test_refuses_foreign_owner(base, monkeypatch):
    base.mkdir()
    monkeypatch.setattr(os, "getuid", lambda: os.stat(base).st_uid + 1)
    with pytest.raises(storage.StorageError):
        storage.ensure_private_dir(base / "1")


def test_dotdot_cannot_chmod_outside_the_store(base, tmp_path):
    """Regression: a path that enters the store and climbs back out must not be chmod-ed."""
    victim = tmp_path / "victim"
    victim.mkdir()
    os.chmod(victim, 0o755)
    os.chmod(tmp_path, 0o755)

    storage.ensure_private_dir(base / ".." / "victim" / "sub" / "attachments")

    assert mode(victim) == 0o755
    assert mode(tmp_path) == 0o755


def test_dotdot_inside_the_store_is_still_private(base):
    assert storage.ensure_private_dir(base / "a" / ".." / "b") == base / "b"
    assert mode(base / "b") == 0o700


def test_output_path_with_dotdot_inside_the_store_is_written_where_it_was_secured(base):
    """Regression: the raw path climbs out of a missing directory, which the kernel can't resolve."""
    path, _ = storage.save_response("ticket", {}, {"ticket": {}}, output_path=str(base / "nope" / ".." / "out.json"))

    assert Path(path) == base / "out.json"
    assert mode(Path(path)) == 0o600


def test_symlink_component_escaping_the_store_is_refused(base, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    base.mkdir(mode=0o700)
    (base / "link").symlink_to(outside)

    with pytest.raises(storage.StorageError):
        storage.ensure_private_dir(base / "link" / "sub")

    assert mode(outside) == 0o755
    assert not (outside / "sub").exists()


def test_output_path_with_dotdot_does_not_touch_other_dirs(base, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    os.chmod(victim, 0o755)

    storage.save_response("ticket", {}, {"ticket": {}}, output_path=str(base / ".." / "victim" / "out.json"))

    assert mode(victim) == 0o755
    assert (victim / "out.json").exists()


def test_storage_error_is_an_oserror():
    assert issubclass(storage.StorageError, OSError)


def test_cannot_secure_directory_raises_instead_of_failing_silently(base, monkeypatch):
    def deny(*a, **k):
        raise PermissionError("nope")

    monkeypatch.setattr(os, "chmod", deny)
    with pytest.raises(storage.StorageError, match="Cannot create a private directory"):
        storage.ensure_private_dir(base / "1")


def test_failure_to_tighten_file_mode_is_reported_on_stderr(tmp_path, monkeypatch, capsys):
    def deny(*a, **k):
        raise OSError("operation not supported")

    monkeypatch.setattr(os, "fchmod", deny)
    target = tmp_path / "out.json"
    storage.write_private_text(target, "{}")

    assert target.read_text() == "{}"  # still written
    assert "could not restrict permissions" in capsys.readouterr().err


def test_unwritable_destination_raises(tmp_path):
    with pytest.raises(OSError):
        storage.write_private_text(tmp_path / "missing" / "out.json", "{}")


def _cli_with(monkeypatch, exc):
    from typer.testing import CliRunner

    from zendesk_skill import cli, operations

    async def boom(*a, **k):
        raise exc

    monkeypatch.setattr(operations, "get_ticket", boom)
    return CliRunner().invoke(cli.app, ["ticket", "1"])


@pytest.mark.parametrize("exc", [
    storage.StorageError("Refusing to use /tmp/zd-cli-1: not a directory owned by the current user"),
    PermissionError("[Errno 13] Permission denied: '/x'"),
])
def test_cli_reports_filesystem_errors_instead_of_a_traceback(monkeypatch, exc):
    out = _cli_with(monkeypatch, exc)
    assert out.exit_code == 1
    assert "File system error" in out.output
    assert "Traceback" not in out.output
