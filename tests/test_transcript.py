from pathlib import Path
from lts import transcript


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
