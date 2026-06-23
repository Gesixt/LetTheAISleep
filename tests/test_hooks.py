import json
import os
import subprocess
import sys
from pathlib import Path
import importlib.util


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOOKS = Path(__file__).resolve().parents[1] / "claude" / "hooks"


def test_pre_compact_dumps_transcript(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("conversation raw text", encoding="utf-8")
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == "conversation raw text"


def test_pre_compact_missing_transcript_still_writes(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(tmp_path / "nope.jsonl")}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1


def test_session_start_injects_anchor_when_clean(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    from lts.config import load_config
    from lts import anchor, paths
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor.write_anchor(
        paths.anchor_file(cfg),
        updated="2026-06-23 15:30",
        last_session="[[S]]",
        active_topics=["t"],
        active_notes=["[[N1]]"],
    )
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "[[N1]]" in text
    assert "finish sleeping" not in text.lower()


def test_session_start_forces_completion_when_pending(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    from lts.config import load_config
    from lts import paths, pending
    cfg = load_config(tmp_path)
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()


def test_session_start_run_wraps_context(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    from lts import paths, pending
    from lts.config import load_config
    cfg = load_config(tmp_path)
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    ss = _load("session_start", HOOKS / "session_start.py")
    out = ss.run({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "finish sleeping" in out["hookSpecificOutput"]["additionalContext"].lower()


def test_user_prompt_submit_force_nudge(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    t = tmp_path / "t.jsonl"
    t.write_text("x" * (4 * 170_000), encoding="utf-8")  # ~85% of 200k window
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "/sleep" in text
    assert "now" in text.lower()


def test_user_prompt_submit_silent_when_low(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    t = tmp_path / "t.jsonl"
    t.write_text("x" * 4000, encoding="utf-8")  # ~1k tokens
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    assert ups.build_context({"transcript_path": str(t)}, root=tmp_path) == ""
    assert ups.run({"transcript_path": str(t)}, root=tmp_path) == {}


def test_pre_compact_runs_as_subprocess_without_pythonpath(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("raw transcript", encoding="utf-8")
    event = {"session_id": "s", "transcript_path": str(transcript)}
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "pre_compact.py")],
        input=json.dumps(event), capture_output=True, text=True,
        cwd=str(tmp_path), env=env,
    )
    assert proc.returncode == 0, proc.stderr
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1


def test_session_start_forces_completion_when_stm_present(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    from lts.config import load_config
    from lts import paths, stm
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    stm.append(paths.stm_file(cfg), "uncommitted fact")
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()
