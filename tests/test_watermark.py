import json
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


def test_render_exchanges_is_readable_text(tmp_path: Path):
    text = watermark.render_exchanges([
        {"role": "user", "text": "why PostgreSQL?"},
        {"role": "assistant", "text": "MVCC.\n900s TTL."},
    ])
    assert "[user] why PostgreSQL?" in text
    assert "900s TTL." in text
    assert "{" not in text  # prose, not raw JSONL
