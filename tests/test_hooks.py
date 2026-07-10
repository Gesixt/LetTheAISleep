import json
import os
import subprocess
import sys
from pathlib import Path
import importlib.util

from tests.helpers import make_project


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOOKS = Path(__file__).resolve().parents[1] / "claude" / "hooks"


def test_pre_compact_dumps_transcript(tmp_path: Path):
    make_project(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("conversation raw text", encoding="utf-8")
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == "conversation raw text"


def test_pre_compact_missing_transcript_still_writes(tmp_path: Path):
    make_project(tmp_path)
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(tmp_path / "nope.jsonl")}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1


def test_session_start_injects_anchor_when_clean(tmp_path: Path):
    make_project(tmp_path)
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
    make_project(tmp_path)
    from lts.config import load_config
    from lts import paths, pending
    cfg = load_config(tmp_path)
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()


def test_session_start_run_wraps_context(tmp_path: Path):
    make_project(tmp_path)
    from lts import paths, pending
    from lts.config import load_config
    cfg = load_config(tmp_path)
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    ss = _load("session_start", HOOKS / "session_start.py")
    out = ss.run({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "finish sleeping" in out["hookSpecificOutput"]["additionalContext"].lower()


def _usage_transcript(path: Path, ctx_tokens: int):
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "usage": {
            "input_tokens": ctx_tokens, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}},
    }), encoding="utf-8")


def test_user_prompt_submit_force_nudge(tmp_path: Path):
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 900)  # 900 / 1000 = 90% -> force
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "/sleep" in text
    assert "now" in text.lower()


def test_user_prompt_submit_silent_when_low(tmp_path: Path):
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 100)  # 10% -> none
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    assert ups.build_context({"transcript_path": str(t)}, root=tmp_path) == ""
    assert ups.run({"transcript_path": str(t)}, root=tmp_path) == {}


def test_pre_compact_runs_as_subprocess_without_pythonpath(tmp_path: Path):
    make_project(tmp_path)
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
    make_project(tmp_path)
    from lts.config import load_config
    from lts import paths, stm
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    stm.append(paths.stm_file(cfg), "uncommitted fact")
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()


def test_stop_auto_captures_exchanges(tmp_path: Path):
    make_project(tmp_path)
    t = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "study the cart service"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "Cart uses Redis; 3 endpoints; 12345 items cap."},
        ]}},
    ]
    t.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    stop = _load("stop", HOOKS / "stop.py")

    n = stop.capture({"transcript_path": str(t)}, root=tmp_path)
    assert n == 2

    from lts.config import load_config
    from lts import paths, stm
    cfg = load_config(tmp_path)
    buf = stm.read(paths.stm_file(cfg))
    assert "study the cart service" in buf
    assert "12345 items cap" in buf

    # idempotent: same transcript, nothing new captured
    assert stop.capture({"transcript_path": str(t)}, root=tmp_path) == 0


def test_stop_captures_nothing_outside_a_project(tmp_path: Path):
    # cwd inside a project whose config.toml was moved away: capture must be a no-op,
    # not a fresh .ai_memory in the first directory we happened to be standing in
    stray = tmp_path / "arm-scripts"
    stray.mkdir()
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "hello"}}
    ), encoding="utf-8")
    stop = _load("stop", HOOKS / "stop.py")
    assert stop.capture({"transcript_path": str(t), "cwd": str(stray)}) == 0
    assert not (stray / ".ai_memory").exists()


def test_pre_compact_writes_nothing_outside_a_project(tmp_path: Path):
    stray = tmp_path / "arm-scripts"
    stray.mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("raw", encoding="utf-8")
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    assert pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=stray) == {}
    assert not (stray / ".ai_memory").exists()


def test_session_start_warns_when_project_is_unconfigured(tmp_path: Path):
    stray = tmp_path / "arm-scripts"
    stray.mkdir()
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=stray)
    assert "not configured" in text.lower()
    assert "lts doctor" in text
    assert not (stray / ".ai_memory").exists()


def test_stop_resolves_project_from_event_cwd(tmp_path: Path):
    # No explicit root; the hook must locate the project via the event's cwd,
    # not the process working directory.
    make_project(tmp_path)
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "hello cwd"}}
    ), encoding="utf-8")
    stop = _load("stop", HOOKS / "stop.py")
    n = stop.capture({"transcript_path": str(t), "cwd": str(tmp_path)})
    assert n == 1
    from lts.config import load_config
    from lts import paths, stm
    assert "hello cwd" in stm.read(paths.stm_file(load_config(tmp_path)))


def _vault_with_a_teammate_note(root: Path) -> None:
    import subprocess
    vault = root / ".ai_vault"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "knowledge-base" / "Cart Service.md").write_text("cart", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(vault), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Dmitry Mitin")
    git("config", "user.email", "d@example.com")
    git("add", "knowledge-base/Cart Service.md")
    git("-c", "user.name=Petr Ivanov", "-c", "user.email=p@example.com",
        "-c", "commit.gpgsign=false",
        "commit", "-m", "cart", "--author", "Petr Ivanov <p@example.com>")


def test_session_start_appends_the_digest_on_the_clean_path(tmp_path: Path):
    make_project(tmp_path)
    from lts.config import load_config
    from lts import anchor, paths
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor.write_anchor(
        paths.anchor_file(cfg), updated="2020-01-01 00:00",
        last_session="[[S]]", active_topics=["t"], active_notes=["[[N1]]"],
    )
    _vault_with_a_teammate_note(tmp_path)

    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "[[N1]]" in text                       # the anchor is still there
    assert "Cart Service (Petr Ivanov)" in text   # and so is the teammate's work


def test_session_start_suppresses_the_digest_while_a_sleep_is_owed(tmp_path: Path):
    make_project(tmp_path)
    from lts.config import load_config
    from lts import paths, pending
    cfg = load_config(tmp_path)
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    _vault_with_a_teammate_note(tmp_path)

    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()
    assert "Cart Service" not in text             # one demand at a time
