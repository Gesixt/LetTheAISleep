import json
from pathlib import Path
from unittest import mock

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
    """A whole invalid line costs itself, not the history.

    This is the state where the line *is* terminated — something appended a complete line that is
    not JSON. `test_a_truncated_write_costs_only_itself` covers the other one, an unterminated
    fragment, which is what an interrupted `append` actually leaves behind; both are real, and
    only the terminated one was pinned.
    """
    log = tmp_path / "health.jsonl"
    journal.append(log, {"event": "good-1"})
    with log.open("a", encoding="utf-8") as fh:
        fh.write('not json at all\n')
    journal.append(log, {"event": "good-2"})
    assert [r["event"] for r in journal.tail(log, 5)] == ["good-1", "good-2"]


def test_a_truncated_write_costs_only_itself(tmp_path: Path):
    """A write killed mid-record leaves no trailing newline — and must not eat the next record.

    The fragment is written *without* `\n`, because that byte is exactly what a truncation
    removes. With a `\n` this test passed against an `append` that continued the unfinished line:
    the fragment and the following record fused into one invalid JSON line and `_records` dropped
    both, so `good-2` disappeared while the docstring claimed the opposite.
    """
    log = tmp_path / "health.jsonl"
    journal.append(log, {"event": "good-1"})
    with log.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "trunca')          # no newline: the write did not finish
    journal.append(log, {"event": "good-2"})
    journal.append(log, {"event": "good-3"})
    assert [r["event"] for r in journal.tail(log, 5)] == ["good-1", "good-2", "good-3"]
    # The fragment is still on a line of its own, where it can cost nothing but itself.
    assert '{"event": "trunca\n' in log.read_text(encoding="utf-8")


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


def test_a_failed_trim_costs_the_trim_and_not_the_history(tmp_path: Path):
    """The trim replaces the file in one step, so a failure leaves the old one intact.

    The rewrite used to be `path.write_text`, which truncates before it writes: a crash inside it
    left a partial journal, or an empty one. Rename-into-place cannot produce that state — the
    worst outcome is a file that is still too long.
    """
    log = tmp_path / "health.jsonl"
    padding = "x" * 400
    for i in range(200):
        journal.append(log, {"event": str(i), "pad": padding})
    before = [r["event"] for r in journal.tail(log, 10_000)]

    def boom(*_a, **_k):
        raise OSError("rename failed")

    with mock.patch.object(journal.os, "replace", boom):
        for i in range(200, 400):
            journal.append(log, {"event": str(i), "pad": padding})

    kept = [r["event"] for r in journal.tail(log, 10_000)]
    assert kept[:len(before)] == before          # nothing that was there was lost
    assert kept[-1] == "399"                     # and the appends still landed
    assert list(tmp_path.glob("*.tmp")) == []    # no half-written file left behind


def test_append_never_raises_on_an_unwritable_path(tmp_path: Path):
    """A journal that can break a hook would be a new way for memory to die."""
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("", encoding="utf-8")
    journal.append(blocked / "health.jsonl", {"event": "x"})   # must not raise
