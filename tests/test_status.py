import json
from pathlib import Path

from lts import anchor, paths, pending, status, stm
from lts.config import load_config
from tests.helpers import make_project


def _cfg(tmp_path: Path):
    return load_config(make_project(tmp_path))


def test_collect_reports_project(tmp_path: Path):
    (tmp_path / "config.toml").write_text('[vault]\nproject = "myproject"\n', encoding="utf-8")
    cfg = load_config(tmp_path)
    m = status.collect(cfg)
    assert m["project"] == "myproject"
    assert "myproject" in status.render(m)


def test_collect_empty(tmp_path: Path):
    cfg = _cfg(tmp_path)
    m = status.collect(cfg)
    assert m["stm"] == {"lines": 0, "bytes": 0, "approx_tokens": 0}
    assert m["pending"] == {"snapshots": 0, "bytes": 0}
    assert m["pressure"] is None
    assert m["anchor"]["exists"] is False


def test_collect_stm_and_pending(tmp_path: Path):
    cfg = _cfg(tmp_path)
    stm.append(paths.stm_file(cfg), "fact one")
    stm.append(paths.stm_file(cfg), "fact two: 12345")
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw snapshot body")
    m = status.collect(cfg)
    assert m["stm"]["lines"] == 2
    assert m["stm"]["bytes"] > 0
    assert m["stm"]["approx_tokens"] >= 1
    assert m["pending"]["snapshots"] == 1
    assert m["pending"]["bytes"] == len("raw snapshot body")


def test_collect_pressure(tmp_path: Path):
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    cfg = load_config(tmp_path)
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant",
        "usage": {"input_tokens": 850, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}}}), encoding="utf-8")
    m = status.collect(cfg, transcript_path=t)
    assert m["pressure"]["level"] == "force"      # 850 / 1000 = 85%
    assert m["pressure"]["window"] == 1000
    assert 0.8 <= m["pressure"]["ratio"] <= 1.0


def test_collect_anchor(tmp_path: Path):
    cfg = _cfg(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor.write_anchor(
        paths.anchor_file(cfg),
        updated="2026-06-23 15:30",
        last_session="[[S]]",
        active_topics=["t1", "t2"],
        active_notes=["[[N1]]", "[[N2]]", "[[N3]]"],
    )
    m = status.collect(cfg)
    assert m["anchor"]["exists"] is True
    assert m["anchor"]["updated"] == "2026-06-23 15:30"
    assert m["anchor"]["active_notes"] == 3
    assert m["anchor"]["active_topics"] == 2


def test_render_contains_sections(tmp_path: Path):
    cfg = _cfg(tmp_path)
    stm.append(paths.stm_file(cfg), "fact")
    text = status.render(status.collect(cfg))
    assert "STM" in text
    assert "Sleep debt" in text
    assert "Anchor" in text
