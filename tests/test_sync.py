import os
import time

import pytest

from app import sync


@pytest.mark.parametrize("spec,cron", [
    ({"type": "manual"}, ""),
    ({"type": "event"}, ""),
    ({"type": "minutes", "every": 15}, "*/15 * * * *"),
    ({"type": "minutes", "every": 1}, "* * * * *"),
    ({"type": "hourly", "minute": 5}, "5 * * * *"),
    ({"type": "daily", "time": "02:30"}, "30 2 * * *"),
    ({"type": "weekly", "weekday": 1, "time": "23:00"}, "0 23 * * mon"),
    ({"type": "monthly", "day": 15, "time": "04:05"}, "5 4 15 * *"),
    ({"type": "cron", "cron": "0  */6 * * *"}, "0 */6 * * *"),
])
def test_build_cron(spec, cron):
    assert sync.build_cron(spec) == cron
    if cron:
        assert sync.build_cron(sync.parse_cron(cron)) == cron


@pytest.mark.parametrize("spec", [
    {"type": "minutes", "every": 0}, {"type": "daily", "time": "25:00"}, {"type": "cron", "cron": "bad"},
    {"type": "monthly", "day": 32, "time": "01:00"}, {"type": "nope"},
])
def test_build_cron_rejects_invalid(spec):
    with pytest.raises(sync.SyncError):
        sync.build_cron(spec)


def test_describe_and_next_runs():
    assert sync.describe("30 2 * * *") == "Every day at 02:30"
    assert sync.describe("0 23 * * mon") == "Every Monday at 23:00"
    assert sync.describe("", event_driven=True).startswith("When files change")
    assert len(sync.next_runs("*/5 * * * *")) == 3


def test_copy_tree_is_idempotent_and_atomic(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "a").mkdir(parents=True)
    (src / "a" / "one.txt").write_text("1")
    (src / "two.txt").write_text("2")
    assert sync.copy_tree(str(src), str(dst))["copied"] == 2
    again = sync.copy_tree(str(src), str(dst))
    assert again["copied"] == 0 and again["skipped"] == 2
    assert not list(dst.rglob("*" + sync.TMP_SUFFIX))
    time.sleep(0.01)
    (src / "two.txt").write_text("22")
    os.utime(src / "two.txt", (time.time() + 10, time.time() + 10))
    assert sync.copy_tree(str(src), str(dst))["copied"] == 1
    assert (dst / "two.txt").read_text() == "22"


def test_sync_mode_deletes_extras(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "keep.txt").write_text("k")
    dst.mkdir()
    (dst / "old.txt").write_text("o")
    assert sync.copy_tree(str(src), str(dst))["deleted"] == 0
    assert sync.copy_tree(str(src), str(dst), delete_extra=True)["deleted"] == 1
    assert sorted(p.name for p in dst.iterdir()) == ["keep.txt"]


def test_job_validation_rejects_paths_outside_roots():
    with pytest.raises(sync.SyncError):
        sync.save_job({"name": "bad", "source": "/etc", "destination": "/tmp/x", "schedule_spec": {"type": "manual"}})
