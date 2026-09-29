import json
from pathlib import Path
from lts import transcript


def test_extract_text_from_str_and_blocks():
    assert transcript.extract_text("hello") == "hello"
    blocks = [
        {"type": "thinking", "thinking": "secret"},
        {"type": "text", "text": "visible answer"},
        {"type": "tool_use", "name": "x"},
    ]
    assert transcript.extract_text(blocks) == "visible answer"
    assert transcript.extract_text(None) == ""


def test_read_exchanges(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    lines = [
        {"type": "mode", "mode": "x"},  # metadata — ignored
        {"type": "user", "message": {"role": "user", "content": "study the cart service"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "Cart uses Redis and 3 endpoints."},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "noise"},
        ]}},  # no text -> skipped
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    ex = transcript.read_exchanges(f)
    assert [e["role"] for e in ex] == ["user", "assistant"]
    assert ex[0]["text"] == "study the cart service"
    assert ex[1]["text"] == "Cart uses Redis and 3 endpoints."


def test_read_exchanges_missing(tmp_path: Path):
    assert transcript.read_exchanges(tmp_path / "nope.jsonl") == []


def test_context_tokens_from_usage(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    lines = [
        {"type": "assistant", "message": {"role": "assistant", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50}}},
        # the LAST assistant usage wins (reflects current context)
        {"type": "assistant", "message": {"role": "assistant", "usage": {
            "input_tokens": 2, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 100}}},
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    assert transcript.context_tokens(f) == 2 + 2000 + 100


def test_context_tokens_falls_back_to_char_estimate(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    f.write_text("x" * 400, encoding="utf-8")  # no usage data
    assert transcript.context_tokens(f) == 100  # 400 // 4


def test_estimate_tokens_missing(tmp_path: Path):
    assert transcript.estimate_tokens(tmp_path / "nope.jsonl") == 0


def test_estimate_tokens_approx(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    f.write_text("x" * 400, encoding="utf-8")
    assert transcript.estimate_tokens(f) == 100  # 400 chars / 4


def test_pressure_levels():
    w = 1000
    assert transcript.pressure_level(100, w, 0.6, 0.8) == "none"
    assert transcript.pressure_level(600, w, 0.6, 0.8) == "warn"
    assert transcript.pressure_level(799, w, 0.6, 0.8) == "warn"
    assert transcript.pressure_level(800, w, 0.6, 0.8) == "force"


def test_read_exchanges_carries_the_entry_uuid_and_timestamp(tmp_path: Path):
    # The sleep watermark points at a specific message, so exchanges must be identifiable.
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({
        "type": "user", "uuid": "u1", "timestamp": "2026-08-26T10:00:00Z",
        "message": {"role": "user", "content": "hi"},
    }), encoding="utf-8")
    ex = transcript.read_exchanges(t)[0]
    assert ex["uuid"] == "u1"
    assert ex["timestamp"] == "2026-08-26T10:00:00Z"


def _raw(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return path


def _user(text: str, uuid: str, **extra) -> dict:
    return {"type": "user", "uuid": uuid, "timestamp": "2026-08-26T11:00:00.000Z",
            "message": {"role": "user", "content": text}, **extra}


def test_read_exchanges_drops_slash_command_scaffolding(tmp_path: Path):
    """`/compact` writes four user records; none of them is material worth remembering.

    Left in, they land in the STM buffer and — worse — make PreCompact write a snapshot of
    pure scaffolding right after a sleep, so SessionStart demands a sleep that is not owed.
    """
    t = _raw(tmp_path / "t.jsonl", [
        _user("/compact", "c1"),
        _user("<local-command-caveat>Caveat: the messages below…</local-command-caveat>",
              "c2", isMeta=True),
        _user("<command-name>/compact</command-name>\n<command-args></command-args>", "c3"),
        _user("<local-command-stdout>Compacted</local-command-stdout>", "c4"),
        _user("real question", "c5"),
    ])
    assert [e["uuid"] for e in transcript.read_exchanges(t)] == ["c5"]


def test_read_exchanges_drops_the_compaction_summary(tmp_path: Path):
    # The raw tail it summarises was already captured by the PreCompact snapshot.
    t = _raw(tmp_path / "t.jsonl", [
        _user("This session is being continued from a previous conversation…", "s1",
              isCompactSummary=True, isVisibleInTranscriptOnly=True),
        _user("real question", "s2"),
    ])
    assert [e["uuid"] for e in transcript.read_exchanges(t)] == ["s2"]


def test_read_exchanges_keeps_a_slash_command_that_carries_an_instruction(tmp_path: Path):
    t = _raw(tmp_path / "t.jsonl", [_user("/sleep and then explain the schema", "k1")])
    assert [e["uuid"] for e in transcript.read_exchanges(t)] == ["k1"]


def test_pressure_level_has_no_verdict_on_an_impossible_measurement():
    """More tokens than the window fit is not "full" — it is a measurement that cannot be true.

    `force` on such a reading is a claim about pressure derived from a figure nothing stands
    behind, and `none` would be the same claim in the other direction.
    """
    w = 1000
    assert transcript.pressure_level(1001, w, 0.6, 0.8) == "unknown"
    assert transcript.pressure_level(1000, w, 0.6, 0.8) == "force"     # the boundary still holds
