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


# One record exactly as `health.record` writes it, copied from this project's own `health.jsonl`
# on 2026-09-30 (read through `lts.paths.health_journal_file`). The six records in that file were
# 465-469 bytes, 468 on average; this is the smallest of them. Its size is what `_KEEP` and
# `_MAX_BYTES` are sized against, so it is pinned here rather than described in a comment.
_MEASURED_RECORD = {
    "at": "2026-09-29T14:40:08.110Z",
    "event": "session_start",
    "blocks": ["sleep_demand", "anchor", "digest"],
    "checks": {"config": "ok", "sidecars": "ok", "hooks": "ok", "vault": "ok", "marks": "ok",
               "capture": "skip", "pressure": "skip", "anchor_fresh": "ok",
               "anchor_delivery": "ok", "capture_progress": "ok"},
    "stm_entries": 122,
    "stm_bytes": 93852,
    "capture_mark": "2026-09-29T14:37:19.246Z",
    "sleep_mark": "2026-09-25T13:07:35.592Z",
    "pending": 0,
    "notes": 20,
}
_MEASURED_BYTES = 469      # the widest of the six, the one the bound must hold for


def test_a_record_is_the_size_the_bound_was_computed_from():
    """If the record grows, the arithmetic behind `_MAX_BYTES` stops holding — say so here."""
    assert len(json.dumps(_MEASURED_RECORD, ensure_ascii=False)) + 1 == 465
    assert _MEASURED_BYTES >= 465


def test_the_threshold_sits_above_the_steady_state_keep_implies():
    """`_MAX_BYTES` must bound the file, not merely trigger a trim that then never stops.

    With `_KEEP` records costing more than `_MAX_BYTES`, `st_size > _MAX_BYTES` is true on every
    append once the log is full, and the trim becomes a whole-file rewrite on every /compact. The
    two constants have to be read together, so they are asserted together.
    """
    steady = journal._KEEP * _MEASURED_BYTES
    assert steady == 93_800                                     # 91.6 KiB of history
    assert steady < journal._MAX_BYTES                          # under a 128 KiB ceiling
    headroom = (journal._MAX_BYTES - steady) // _MEASURED_BYTES
    assert headroom == 79, headroom     # ~79 appends between trims, not one trim per append


def test_the_log_is_bounded_by_construction(tmp_path: Path):
    """It is appended to on every compaction forever, so it must never grow without limit.

    Two numbers, in the two directions that matter: `_MAX_BYTES` is the ceiling on the file and
    `_KEEP` is the floor on the history. `assert len(kept) <= _KEEP` used to stand here and was
    the wrong shape — it held only because the trim fired on *every* append, which is the defect
    this pair of constants was corrected to remove. Between trims the file legitimately holds more
    than `_KEEP` records; what it must never do is exceed `_MAX_BYTES` or fall below `_KEEP`.
    """
    log = tmp_path / "health.jsonl"
    padding = "x" * 400
    for i in range(700):
        journal.append(log, {"event": str(i), "pad": padding})
    kept = journal.tail(log, 10_000)
    # No slack: the `* 2` this line used to carry was the room that existed because `_KEEP`
    # records did not in fact fit beneath `_MAX_BYTES`. They do now.
    assert log.stat().st_size <= journal._MAX_BYTES
    assert len(kept) >= journal._KEEP          # the depth the trim promises to leave behind
    assert kept[-1]["event"] == "699"          # the newest survives


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
