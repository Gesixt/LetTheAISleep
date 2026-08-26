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
