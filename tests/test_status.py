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


def test_render_shows_author_only_in_team_mode(tmp_path: Path):
    make_project(tmp_path, 'author = "dmitrii"\n')
    text = status.render(status.collect(load_config(tmp_path)))
    assert "Author:" in text and "dmitrii" in text

    solo = tmp_path / "solo"
    make_project(solo)
    assert "Author:" not in status.render(status.collect(load_config(solo)))


def test_render_names_the_command_each_form_belongs_to(tmp_path: Path):
    """The CLI takes the slug for `project info` and the display name for `reindex -p`.

    Calling the slug "the Basic Memory CLI name" sent readers to `reindex -p <slug>`, which
    fails with "Project not found" (verified against basic-memory 0.22.1).
    """
    (tmp_path / "config.toml").write_text('[vault]\nproject = "LetTheAISleep"\n', encoding="utf-8")
    text = status.render(status.collect(load_config(tmp_path)))
    assert "CLI name" not in text
    assert "reindex -p LetTheAISleep" in text
    assert "project info let-the-aisleep" in text


def test_render_omits_the_name_hint_when_the_slug_is_the_name(tmp_path: Path):
    (tmp_path / "config.toml").write_text('[vault]\nproject = "lowercase-name"\n', encoding="utf-8")
    text = status.render(status.collect(load_config(tmp_path)))
    assert "Basic Memory:" not in text


def test_render_prints_no_percentage_for_a_reading_that_cannot_be_true(tmp_path: Path):
    """`lts status` printed "45413420/1000000 tok (4541%) — unknown": an honest level beside a
    figure that cannot be one, in the same human-facing line."""
    metrics = {
        "project": "p", "stm": {"lines": 0, "bytes": 0, "approx_tokens": 0},
        "pending": {"snapshots": 0, "bytes": 0},
        "anchor": {"exists": False, "updated": None, "active_notes": 0, "active_topics": 0},
        "pressure": {"tokens": 45_413_420, "window": 1_000_000, "ratio": 45.413,
                     "level": "unknown", "source": "usage"},
    }
    line = [l for l in status.render(metrics).splitlines() if "Context" in l][0]
    assert "%" not in line
    assert "45413420" in line and "1000000" in line
    assert "/sleep" not in line


def test_render_marks_a_figure_that_was_estimated_rather_than_measured(tmp_path: Path):
    metrics = {
        "project": "p", "stm": {"lines": 0, "bytes": 0, "approx_tokens": 0},
        "pending": {"snapshots": 0, "bytes": 0},
        "anchor": {"exists": False, "updated": None, "active_notes": 0, "active_topics": 0},
        "pressure": {"tokens": 12, "window": 1_000_000, "ratio": 0.0,
                     "level": "none", "source": "estimate"},
    }
    line = [l for l in status.render(metrics).splitlines() if "Context" in l][0]
    assert "estimate" in line


def test_collect_records_how_the_figure_was_obtained(tmp_path: Path):
    from lts.config import load_config
    from tests.helpers import make_project
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    junk = tmp_path / "notes.txt"
    junk.write_text("x" * 48, encoding="utf-8")
    m = status.collect(load_config(tmp_path), transcript_path=junk)
    assert m["pressure"]["source"] == "estimate"
