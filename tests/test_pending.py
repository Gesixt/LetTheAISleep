from pathlib import Path
from lts import pending


def test_dump_creates_incrementing_files(tmp_path: Path):
    p1 = pending.dump_snapshot(tmp_path, "sess1", "raw transcript A")
    p2 = pending.dump_snapshot(tmp_path, "sess1", "raw transcript B")
    assert p1.name == "sess1-1.md"
    assert p2.name == "sess1-2.md"
    assert p1.read_text(encoding="utf-8") == "raw transcript A"


def test_has_pending(tmp_path: Path):
    assert pending.has_pending(tmp_path) is False
    pending.dump_snapshot(tmp_path, "s", "x")
    assert pending.has_pending(tmp_path) is True


def test_list_and_clear(tmp_path: Path):
    pending.dump_snapshot(tmp_path, "s", "a")
    pending.dump_snapshot(tmp_path, "s", "b")
    assert len(pending.list_snapshots(tmp_path)) == 2
    pending.clear_all(tmp_path)
    assert pending.has_pending(tmp_path) is False
