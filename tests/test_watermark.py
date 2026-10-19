import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lts import watermark


def _msg(role: str, text: str, uuid: str | None = None, ts: str | None = None) -> dict:
    d = {"type": role, "message": {"role": role, "content": text}}
    if uuid:
        d["uuid"] = uuid
    if ts:
        d["timestamp"] = ts
    return d


def _transcript(path: Path, entries: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return path


def test_mark_of_names_the_last_exchange(tmp_path: Path):
    t = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "a", "u1", "2026-08-26T10:00:00Z"),
        _msg("assistant", "b", "u2", "2026-08-26T10:01:00Z"),
    ])
    assert watermark.mark_of(t) == {
        "uuid": "u2", "timestamp": "2026-08-26T10:01:00Z", "count": 2,
    }


def test_mark_of_a_missing_transcript_is_empty(tmp_path: Path):
    assert watermark.mark_of(tmp_path / "nope.jsonl") == {}


def test_entries_after_returns_only_what_follows_the_marked_uuid(tmp_path: Path):
    t = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "old chapter", "u1", "2026-04-01T10:00:00Z"),
        _msg("assistant", "old answer", "u2", "2026-04-01T10:01:00Z"),
        _msg("user", "new chapter", "u3", "2026-08-26T10:00:00Z"),
    ])
    new = watermark.entries_after(t, {"uuid": "u2", "timestamp": "2026-04-01T10:01:00Z", "count": 2})
    assert [e["text"] for e in new] == ["new chapter"]


def test_entries_after_falls_back_to_timestamp_when_the_uuid_is_gone(tmp_path: Path):
    # A transcript rewritten by compaction keeps the timeline but loses the marked uuid.
    t = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "old chapter", "z1", "2026-04-01T10:00:00Z"),
        _msg("assistant", "new answer", "z2", "2026-08-26T10:01:00Z"),
    ])
    new = watermark.entries_after(t, {"uuid": "vanished", "timestamp": "2026-04-01T10:00:00Z", "count": 1})
    assert [e["text"] for e in new] == ["new answer"]


def test_entries_after_falls_back_to_count_without_uuid_or_timestamp(tmp_path: Path):
    t = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "one"), _msg("assistant", "two"), _msg("user", "three"),
    ])
    new = watermark.entries_after(t, {"count": 2})
    assert [e["text"] for e in new] == ["three"]


def test_entries_after_ignores_a_count_that_overruns_a_shorter_transcript(tmp_path: Path):
    # A fresh session's transcript is short; a stale count from the previous one must not
    # swallow it whole (that is what kept the first turn of every session out of STM).
    t = _transcript(tmp_path / "t.jsonl", [_msg("user", "first turn of a new session")])
    new = watermark.entries_after(t, {"count": 58})
    assert [e["text"] for e in new] == ["first turn of a new session"]


def test_entries_after_returns_everything_without_a_mark(tmp_path: Path):
    t = _transcript(tmp_path / "t.jsonl", [_msg("user", "a", "u1"), _msg("assistant", "b", "u2")])
    assert len(watermark.entries_after(t, {})) == 2


def test_mark_round_trips_on_disk(tmp_path: Path):
    f = tmp_path / "sleep-mark.json"
    watermark.write_mark(f, {"uuid": "u9", "timestamp": "2026-08-26T10:00:00Z", "count": 9})
    assert watermark.read_mark(f)["uuid"] == "u9"


def test_reading_a_corrupt_or_absent_mark_yields_no_mark(tmp_path: Path):
    assert watermark.read_mark(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert watermark.read_mark(bad) == {}


def test_the_sleep_flag_round_trips(tmp_path: Path):
    flag = tmp_path / ".lts-sleep-armed"
    assert not watermark.is_armed(flag)
    watermark.arm(flag)
    assert watermark.is_armed(flag)
    watermark.disarm(flag)
    assert not watermark.is_armed(flag)
    watermark.disarm(flag)  # idempotent


def test_arming_records_the_instant_it_happened(tmp_path: Path):
    """The flag must say *when*, or a hook cannot tell a sleep from a latch left on disk.

    Measured 2026-10-19 across six real sleeps in two projects: the gap between `lts stm clear`
    arming the flag and the `Stop` hook consuming it was **8.3–16.9 s**. The incident that
    produced this requirement had a gap of hours, so the age is what separates the two.
    """
    flag = tmp_path / ".lts-sleep-armed"
    when = datetime(2026, 10, 19, 5, 37, 1, tzinfo=timezone.utc)
    watermark.arm(flag, at=when)
    assert watermark.armed_at(flag) == "2026-10-19T05:37:01.000Z"


def test_a_flag_with_no_instant_reads_as_unknown_not_as_now(tmp_path: Path):
    """Every flag already on disk in a deployed project is an empty file.

    `None` is "I cannot establish the age", which the hooks must treat as stale — the asymmetry
    is that honouring an unknown-age latch moves the sleep mark over unconsolidated material,
    while ignoring one costs a few captured lines of the sleep's own narration.
    """
    legacy = tmp_path / ".lts-sleep-armed"
    legacy.write_text("", encoding="utf-8")
    assert watermark.is_armed(legacy)
    assert watermark.armed_at(legacy) is None

    corrupt = tmp_path / "corrupt"
    corrupt.write_text("{not json", encoding="utf-8")
    assert watermark.armed_at(corrupt) is None
    assert watermark.armed_at(tmp_path / "absent") is None


def test_only_a_stamp_inside_the_bound_counts_as_a_sleep_in_progress(tmp_path: Path):
    """Each `False` here is a state in which honouring the latch would move the sleep mark."""
    flag = tmp_path / ".lts-sleep-armed"
    now = datetime(2026, 10, 16, 9, 40, 3, tzinfo=timezone.utc)

    watermark.arm(flag, at=now - timedelta(seconds=16, milliseconds=900))
    assert watermark.armed_recently(flag, now), "the slowest of the six measured sleeps"
    watermark.arm(flag, at=now - watermark.SLEEP_FLAG_MAX_AGE)
    assert watermark.armed_recently(flag, now), "the bound itself is inclusive"
    watermark.arm(flag, at=now - watermark.SLEEP_FLAG_MAX_AGE - timedelta(seconds=1))
    assert not watermark.armed_recently(flag, now), "one second past it is not"

    # Stamped ahead of the hook that reads it. One clock writes both, so this is a rewritten
    # file or a machine whose time moved — neither is evidence that a sleep is in progress.
    watermark.arm(flag, at=now + timedelta(seconds=1))
    assert not watermark.armed_recently(flag, now)

    flag.write_text("", encoding="utf-8")
    assert not watermark.armed_recently(flag, now), "no stamp is ignorance, not freshness"
    flag.write_text('{"timestamp": "not a date"}', encoding="utf-8")
    assert not watermark.armed_recently(flag, now)
    watermark.disarm(flag)
    assert not watermark.armed_recently(flag, now)


def test_the_bound_stays_between_the_measured_sleep_and_the_incident():
    """The value is a judgement with a margin, so this holds the bracket the margin must sit in.

    The tests above are all relative to `SLEEP_FLAG_MAX_AGE`, so none of them notices it being
    widened: measured 2026-10-19, moving it from 5 minutes to 60 left the whole suite green. A
    bound that drifts up is the defect coming back, just slower.

    **Floor, one minute:** the six measured sleeps consumed the latch in 8.3–16.9 s, and a bound
    near that would start discarding real sleeps whose tail runs a little long. **Ceiling, thirty
    minutes:** beyond that a latch can outlive a coffee break and be eaten by a `Stop` belonging to
    unrelated work, which is the 2026-10-16 incident in miniature.
    """
    assert timedelta(minutes=1) <= watermark.SLEEP_FLAG_MAX_AGE <= timedelta(minutes=30)


def test_render_exchanges_is_readable_text(tmp_path: Path):
    text = watermark.render_exchanges([
        {"role": "user", "text": "why PostgreSQL?"},
        {"role": "assistant", "text": "MVCC.\n900s TTL."},
    ])
    assert "[user] why PostgreSQL?" in text
    assert "900s TTL." in text
    assert "{" not in text  # prose, not raw JSONL


def test_mark_at_records_an_instant_rather_than_a_position(tmp_path: Path):
    from datetime import datetime, timezone
    mark = watermark.mark_at(datetime(2026, 8, 26, 11, 2, 3, 928000, tzinfo=timezone.utc))
    assert mark == {"timestamp": "2026-08-26T11:02:03.928Z"}


def test_entries_after_an_instant_excludes_a_message_not_yet_flushed(tmp_path: Path):
    """The Stop hook can read the transcript before the turn's last message is written to it.

    A mark naming the last *flushed* exchange therefore sits one message short, and that
    message leaks into the next snapshot. An instant covers it: Claude Code stamps a message
    when it is produced, and the hook always runs after that.
    """
    from datetime import datetime, timezone
    t = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "/sleep", "u1", "2026-08-26T11:01:00.000Z"),
        _msg("assistant", "Consolidated.", "u2", "2026-08-26T11:02:03.815Z"),
    ])
    hook_ran_at = datetime(2026, 8, 26, 11, 2, 3, 928000, tzinfo=timezone.utc)

    assert watermark.entries_after(t, watermark.mark_at(hook_ran_at)) == []

    _transcript(t, [
        _msg("user", "/sleep", "u1", "2026-08-26T11:01:00.000Z"),
        _msg("assistant", "Consolidated.", "u2", "2026-08-26T11:02:03.815Z"),
        _msg("user", "next question", "u3", "2026-08-26T11:04:47.179Z"),
    ])
    later = watermark.entries_after(t, watermark.mark_at(hook_ran_at))
    assert [e["uuid"] for e in later] == ["u3"]
