import json
from pathlib import Path

from lts import journal


def test_a_record_round_trips(tmp_path: Path):
    log = tmp_path / "health.jsonl"
    journal.append(log, {"event": "session_start", "checks": {"config": "ok"}})
    got = journal.tail(log, 5)
    assert len(got) == 1
    assert got[0]["event"] == "session_start"
    assert got[0]["checks"] == {"config": "ok"}


def test_append_stamps_at_when_absent_and_keeps_it_when_given(tmp_path: Path):
    log = tmp_path / "health.jsonl"
    journal.append(log, {"event": "a"})
    journal.append(log, {"event": "b", "at": "2020-01-01T00:00:00.000Z"})
    stamped, given = journal.tail(log, 2)
    assert stamped["at"].endswith("Z") and stamped["at"] > "2026-01-01"
    assert given["at"] == "2020-01-01T00:00:00.000Z"


def test_tail_returns_the_last_n_oldest_first(tmp_path: Path):
    log = tmp_path / "health.jsonl"
    for i in range(6):
        journal.append(log, {"event": str(i)})
    assert [r["event"] for r in journal.tail(log, 3)] == ["3", "4", "5"]


def test_tail_of_a_missing_file_is_empty(tmp_path: Path):
    assert journal.tail(tmp_path / "nope.jsonl", 5) == []


def test_an_unparseable_line_is_skipped_not_fatal(tmp_path: Path):
    """A truncated write must cost one record, not the whole history."""
    log = tmp_path / "health.jsonl"
    journal.append(log, {"event": "good-1"})
    with log.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "trunca\n')
    journal.append(log, {"event": "good-2"})
    assert [r["event"] for r in journal.tail(log, 5)] == ["good-1", "good-2"]


def test_the_log_is_bounded_by_construction(tmp_path: Path):
    """It is appended to on every compaction forever, so it must never grow without limit."""
    log = tmp_path / "health.jsonl"
    padding = "x" * 400
    for i in range(400):
        journal.append(log, {"event": str(i), "pad": padding})
    kept = journal.tail(log, 10_000)
    assert len(kept) <= journal._KEEP
    assert kept[-1]["event"] == "399"          # the newest survives
    assert log.stat().st_size < journal._MAX_BYTES * 2


def test_append_never_raises_on_an_unwritable_path(tmp_path: Path):
    """A journal that can break a hook would be a new way for memory to die."""
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("", encoding="utf-8")
    journal.append(blocked / "health.jsonl", {"event": "x"})   # must not raise
