import os
import uuid
from pathlib import Path

import pytest

from app import fstab, mounts

TEST_BASE = Path(os.environ["FSTAB_PATH"]).parent


def test_validate_path_runs_all_permission_tests(tmp_path):
    status = mounts.validate_path(str(tmp_path), require_mount=False)
    assert status["mounted"] and status["readable"] and status["writable"]
    assert status["rename_ok"] and status["delete_ok"] and status["error"] is None
    assert os.listdir(tmp_path) == []  # probe files cleaned up
    assert mounts.healthy(status)


def test_delete_removes_the_file_rename_created(tmp_path, monkeypatch):
    """rename_test and delete_test must share one probe so delete removes what rename created."""
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": "0123456789abcdef0123456789abcdef"})())
    renamed = tmp_path / ".drive-manager-check-0123456789.renamed"
    renamed.write_text("stale probe from a previous run", encoding="utf-8")
    status = mounts.validate_path(str(tmp_path), require_mount=False)
    assert status["rename_ok"] and status["delete_ok"] and status["error"] is None
    assert not renamed.exists()
    assert os.listdir(tmp_path) == []  # probe files cleaned up


def test_existing_directory_is_not_healthy_when_not_mounted(tmp_path):
    status = mounts.validate_path(str(tmp_path))
    assert status["mounted"] is False and status["error"] == "not mounted"
    assert not mounts.healthy(status)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses permissions")
def test_read_only_directory_fails_write(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        status = mounts.validate_path(str(ro), require_mount=False)
        assert status["readable"] and not status["writable"] and not mounts.healthy(status)
        assert mounts.healthy(status, need_write=False)
    finally:
        ro.chmod(0o755)


def test_ownership_report(tmp_path):
    info = mounts.ownership(str(tmp_path))
    assert info["owner_uid"] == os.getuid() and info["uid_match"] and info["gid_match"]


def test_ensure_available_plain_directory(tmp_path):
    assert mounts.ensure_available(str(tmp_path), need_write=True)["ok"]
    missing = mounts.ensure_available(str(tmp_path / "missing"), need_write=False)
    assert not missing["ok"] and missing["attempts"] == 0


def test_mount_point_must_be_under_mnt():
    with pytest.raises(mounts.MountError):
        mounts.add_mount({"type": "nfs", "name": "x", "host": "10.0.0.1", "export": "/srv", "mount_point": "/etc"})


def test_nfs_command_and_fstab_line_use_safe_defaults():
    m = {"id": "abc", "type": "nfs", "host": "10.0.0.2", "export": "/srv/share", "mount_point": "/mnt/nas", "options": ""}
    cmd = mounts.build_mount_command(m)
    assert cmd[:4] == ["mount", "-t", "nfs4", "-o"] and "x-systemd.automount" in cmd[4] and "nofail" in cmd[4]
    smb = {"id": "s1", "type": "smb", "host": "nas", "share": "media", "mount_point": "/mnt/media", "options": ""}
    device, mp, fstype, opts = mounts.fstab_line(smb)
    assert fstype == "cifs" and "uid=1000" in opts and "credentials=" in opts and "password" not in opts


def test_fstab_parse():
    entries = fstab.parse("# comment\nUUID=1 / ext4 defaults 0 1\nnas:/srv /mnt/My\\040Share nfs4 nofail,x-systemd.automount 0 0\n")
    assert len(entries) == 2
    assert entries[1]["mountpoint"] == "/mnt/My Share" and entries[1]["automount"] and entries[1]["nofail"]
    assert not entries[0]["nofail"]


def test_fstab_persist_and_remove_roundtrip():
    path = TEST_BASE / "fstab"
    res = fstab.ensure_line("nas:/srv", "/mnt/nas", "nfs4", "nofail")
    assert res["ok"], res
    assert "/mnt/nas nfs4" in path.read_text()
    fstab.ensure_line("nas:/srv2", "/mnt/nas", "nfs4", "nofail")  # idempotent replace
    assert path.read_text().count("/mnt/nas") == 1
    with pytest.raises(PermissionError):
        fstab.remove_entry("/mnt/nas", "remove")
    fstab.remove_entry("/mnt/nas", "REMOVE")
    assert "/mnt/nas" not in path.read_text()
    assert (TEST_BASE / "fstab.drive-manager.bak").exists()
