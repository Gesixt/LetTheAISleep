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


def _snapshots(root: Path) -> list[Path]:
    return sorted((root / ".ai_memory" / "pending_consolidation").glob("*.md"))


def test_pre_compact_dumps_transcript(tmp_path: Path):
    make_project(tmp_path)
    transcript = _transcript(tmp_path / "t.jsonl", [_msg("user", "conversation text", "u1")])
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)
    snaps = _snapshots(tmp_path)
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == "[user] conversation text"


def test_pre_compact_writes_nothing_for_a_missing_transcript(tmp_path: Path):
    make_project(tmp_path)
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(tmp_path / "nope.jsonl")}, root=tmp_path)
    assert _snapshots(tmp_path) == []


def test_pre_compact_snapshots_only_what_follows_the_sleep_mark(tmp_path: Path):
    # The chapter before the mark is already in long-term notes; re-dumping it is what made
    # every post-sleep session open by investigating a snapshot it had already consolidated.
    make_project(tmp_path)
    from lts import paths, watermark
    from lts.config import load_config
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    transcript = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "the vacations chapter", "u1", "2026-07-23T09:04:00Z"),
        _msg("assistant", "getVacationHistory done", "u2", "2026-07-23T09:20:00Z"),
        _msg("user", "the release-builder chapter", "u3", "2026-08-26T12:40:00Z"),
    ])
    watermark.write_mark(paths.sleep_mark_file(cfg), {
        "uuid": "u2", "timestamp": "2026-07-23T09:20:00Z", "count": 2,
    })

    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)

    body = _snapshots(tmp_path)[0].read_text(encoding="utf-8")
    assert "release-builder" in body
    assert "getVacationHistory" not in body
    assert "vacations chapter" not in body


def test_pre_compact_writes_no_snapshot_when_nothing_is_new(tmp_path: Path):
    # No new material means no sleep is owed — SessionStart must stay quiet.
    make_project(tmp_path)
    from lts import paths, watermark
    from lts.config import load_config
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    transcript = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "already consolidated", "u1", "2026-08-26T10:00:00Z"),
    ])
    watermark.write_mark(paths.sleep_mark_file(cfg), watermark.mark_of(transcript))

    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)
    assert _snapshots(tmp_path) == []


def test_pre_compact_advances_the_mark_so_a_second_compact_does_not_duplicate(tmp_path: Path):
    make_project(tmp_path)
    transcript = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "one chapter", "u1", "2026-08-26T10:00:00Z"),
    ])
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    event = {"session_id": "s", "transcript_path": str(transcript)}
    pre.run(event, root=tmp_path)
    pre.run(event, root=tmp_path)
    assert len(_snapshots(tmp_path)) == 1


def test_stop_drops_the_sleep_turn_when_the_sleep_flag_is_armed(tmp_path: Path):
    # `lts stm clear` runs mid-turn; without this the Stop hook immediately refills the
    # freshly emptied buffer with the sleep's own narration.
    make_project(tmp_path)
    from lts import paths, stm, watermark
    from lts.config import load_config
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    transcript = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "/sleep", "u1", "2026-08-26T10:00:00Z"),
        _msg("assistant", "Sleeping. Collecting the material.", "u2", "2026-08-26T10:01:00Z"),
    ])
    watermark.arm(paths.sleep_flag_file(cfg))

    stop = _load("stop", HOOKS / "stop.py")
    assert stop.capture({"transcript_path": str(transcript)}, root=tmp_path) == 0
    assert stm.is_empty(paths.stm_file(cfg))
    assert not watermark.is_armed(paths.sleep_flag_file(cfg))


def test_stop_marks_the_sleep_so_the_next_compact_is_silent(tmp_path: Path):
    make_project(tmp_path)
    from lts import paths, watermark
    from lts.config import load_config
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    transcript = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "/sleep", "u1", "2026-08-26T10:00:00Z"),
        _msg("assistant", "Consolidated.", "u2", "2026-08-26T10:01:00Z"),
    ])
    watermark.arm(paths.sleep_flag_file(cfg))

    stop = _load("stop", HOOKS / "stop.py")
    stop.capture({"transcript_path": str(transcript)}, root=tmp_path)

    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)
    assert _snapshots(tmp_path) == []


def test_stop_captures_the_first_turn_of_a_new_shorter_transcript(tmp_path: Path):
    # The old numeric cursor was per-project, so a fresh session's short transcript was
    # sliced away entirely and its opening turn never reached STM.
    make_project(tmp_path)
    from lts import paths, stm
    from lts.config import load_config
    cfg = load_config(tmp_path)
    stop = _load("stop", HOOKS / "stop.py")

    long_one = _transcript(tmp_path / "old.jsonl", [
        _msg("user", f"turn {i}", f"o{i}", f"2026-08-25T10:{i:02d}:00Z") for i in range(20)
    ])
    stop.capture({"transcript_path": str(long_one)}, root=tmp_path)
    stm.clear(paths.stm_file(cfg))

    fresh = _transcript(tmp_path / "new.jsonl", [
        _msg("user", "opening turn of a new session", "n1", "2026-08-26T09:00:00Z"),
    ])
    assert stop.capture({"transcript_path": str(fresh)}, root=tmp_path) == 1
    assert "opening turn of a new session" in stm.read(paths.stm_file(cfg))


def test_stop_migrates_the_legacy_capture_offset(tmp_path: Path):
    # Existing sidecars carry `.lts-capture-offset`; honour it once so upgrading does not
    # replay the whole transcript into STM.
    make_project(tmp_path)
    from lts import paths, stm
    from lts.config import load_config
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    (paths.sidecar_root(cfg) / "stm" / ".lts-capture-offset").write_text("2", encoding="utf-8")
    transcript = _transcript(tmp_path / "t.jsonl", [
        _msg("user", "already captured", "u1", "2026-08-26T10:00:00Z"),
        _msg("assistant", "also already captured", "u2", "2026-08-26T10:01:00Z"),
        _msg("user", "genuinely new", "u3", "2026-08-26T10:02:00Z"),
    ])

    stop = _load("stop", HOOKS / "stop.py")
    assert stop.capture({"transcript_path": str(transcript)}, root=tmp_path) == 1
    buf = stm.read(paths.stm_file(cfg))
    assert "genuinely new" in buf
    assert "already captured" not in buf
    assert not (paths.sidecar_root(cfg) / "stm" / ".lts-capture-offset").exists()


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


def test_user_prompt_submit_states_the_figure_but_does_not_nudge_when_low(tmp_path: Path):
    # It used to be silent altogether below the warn threshold. Silence is what left the model
    # with no measured number to quote, so the figure is now unconditional; only the nudge is not.
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 100)  # 10% -> none
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert text == "Context window: 100/1,000 tokens (10%)"
    assert "/sleep" not in text


def test_pre_compact_runs_as_subprocess_without_pythonpath(tmp_path: Path):
    make_project(tmp_path)
    transcript = _transcript(tmp_path / "t.jsonl", [_msg("user", "raw transcript", "u1")])
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


def test_session_start_keeps_the_anchor_when_the_digest_blows_up(tmp_path: Path):
    # A dangling *.md symlink makes the mtime scan's note.stat() raise. The anchor must
    # still render; the digest degrades to nothing rather than dropping everything.
    make_project(tmp_path)
    from lts.config import load_config
    from lts import anchor, paths
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor.write_anchor(
        paths.anchor_file(cfg), updated="2020-01-01 00:00",
        last_session="[[S]]", active_topics=["t"], active_notes=["[[N1]]"],
    )
    vault = tmp_path / ".ai_vault"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "knowledge-base" / "Dangling.md").symlink_to(vault / "does-not-exist.md")

    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "[[N1]]" in text


def test_session_start_shows_the_digest_while_a_sleep_is_owed(tmp_path: Path):
    make_project(tmp_path)
    from lts.config import load_config
    from lts import anchor, paths, pending
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    # The digest measures "since the last sleep", so it needs an anchor for a baseline.
    anchor.write_anchor(
        paths.anchor_file(cfg), updated="2020-01-01 00:00",
        last_session="[[S]]", active_topics=["t"], active_notes=["[[N1]]"],
    )
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    _vault_with_a_teammate_note(tmp_path)

    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()
    assert "Cart Service" in text                 # a teammate's work is not worth hiding


def test_session_start_says_exactly_what_is_owed(tmp_path: Path):
    # A bare "unfinished sleep" made every session open by investigating whether the
    # material was a duplicate. It never is any more, so say so and give the size.
    make_project(tmp_path)
    from lts import paths, pending, stm
    from lts.config import load_config
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    stm.append(paths.stm_file(cfg), "[user] a fact")
    stm.append(paths.stm_file(cfg), "[assistant] another fact")
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "[user] raw tail")

    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()
    assert "2 STM" in text
    assert "1 pending snapshot" in text
    assert "duplicate" in text.lower()   # tells the model not to re-check for one


def test_stop_marks_the_sleep_at_the_hook_instant_not_the_last_flushed_message(tmp_path: Path):
    """The sleep's own closing message must not leak into the next snapshot.

    Observed in production 2026-08-26: Stop ran 113 ms after the closing message was
    produced but before Claude Code had written it to the transcript, so the mark named the
    message before it and `/compact` snapshotted the sleep's own summary (4485 B) — enough
    for SessionStart to announce an unfinished sleep that was already done.
    """
    from datetime import datetime, timezone
    from lts import paths, watermark
    from lts.config import load_config
    make_project(tmp_path)
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    t = tmp_path / "t.jsonl"
    _transcript(t, [_msg("user", "/sleep", "u1", "2026-08-26T11:01:00.000Z")])
    watermark.arm(paths.sleep_flag_file(cfg))
    hook_ran_at = datetime(2026, 8, 26, 11, 2, 3, 928000, tzinfo=timezone.utc)

    stop = _load("stop", HOOKS / "stop.py")
    stop.capture({"transcript_path": str(t)}, root=tmp_path, now=hook_ran_at)

    # …and only now does Claude Code flush the closing message it had already stamped.
    _transcript(t, [
        _msg("user", "/sleep", "u1", "2026-08-26T11:01:00.000Z"),
        _msg("assistant", "Consolidated.", "u2", "2026-08-26T11:02:03.815Z"),
    ])
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(t)}, root=tmp_path)
    assert _snapshots(tmp_path) == []


def test_pre_compact_leaves_the_sleep_flag_for_stop(tmp_path: Path):
    """Only Stop knows the sleep turn has ended, so PreCompact must not consume the flag.

    An auto-compact mid-sleep used to disarm it, and the rest of the sleep's narration then
    went into the buffer the sleep had just emptied.
    """
    from datetime import datetime, timezone
    from lts import paths, stm, watermark
    from lts.config import load_config
    make_project(tmp_path)
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    t = tmp_path / "t.jsonl"
    _transcript(t, [_msg("user", "/sleep", "u1", "2026-08-26T11:01:00.000Z")])
    watermark.arm(paths.sleep_flag_file(cfg))

    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(t)},
            root=tmp_path, now=datetime(2026, 8, 26, 11, 1, 30, tzinfo=timezone.utc))
    assert _snapshots(tmp_path) == []
    assert watermark.is_armed(paths.sleep_flag_file(cfg))
    assert watermark.read_mark(paths.sleep_mark_file(cfg)) == {
        "timestamp": "2026-08-26T11:01:30.000Z"
    }

    _transcript(t, [
        _msg("user", "/sleep", "u1", "2026-08-26T11:01:00.000Z"),
        _msg("assistant", "…rest of the sleep.", "u2", "2026-08-26T11:02:00.000Z"),
    ])
    stop = _load("stop", HOOKS / "stop.py")
    stop.capture({"transcript_path": str(t)}, root=tmp_path,
                 now=datetime(2026, 8, 26, 11, 2, 3, tzinfo=timezone.utc))
    assert stm.is_empty(paths.stm_file(cfg))
    assert not watermark.is_armed(paths.sleep_flag_file(cfg))


def test_pre_compact_never_marks_past_material_it_did_not_snapshot(tmp_path: Path):
    """The instant is right for a sleep (which discards on purpose) but wrong here.

    A snapshot can only contain what was already flushed, so the mark must name the last
    exchange it actually wrote out. Marking "now" would step over a message that was stamped
    earlier but reached the file later, dropping it from memory altogether.
    """
    from datetime import datetime, timezone
    make_project(tmp_path)
    t = tmp_path / "t.jsonl"
    _transcript(t, [_msg("user", "first", "u1", "2026-08-26T11:00:00.000Z")])
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    compact_ran_at = datetime(2026, 8, 26, 11, 5, 0, tzinfo=timezone.utc)
    pre.run({"session_id": "s", "transcript_path": str(t)}, root=tmp_path, now=compact_ran_at)

    _transcript(t, [
        _msg("user", "first", "u1", "2026-08-26T11:00:00.000Z"),
        _msg("assistant", "flushed late", "u2", "2026-08-26T11:04:59.000Z"),
    ])
    pre.run({"session_id": "s", "transcript_path": str(t)}, root=tmp_path, now=compact_ran_at)

    snaps = _snapshots(tmp_path)
    assert len(snaps) == 2
    assert snaps[1].read_text(encoding="utf-8") == "[assistant] flushed late"


def test_session_start_shows_the_anchor_even_when_a_sleep_is_owed(tmp_path: Path):
    """The sleep demand used to *replace* the anchor, which hid memory exactly when it was needed.

    "One demand at a time" assumed sessions end often. A session that runs for two months on
    /sleep + /compact never has an empty STM buffer, so the anchor — the only thing telling the
    model which notes exist — was never delivered at all, and /recall had nothing to aim at.
    """
    from lts import anchor, paths, stm
    from lts.config import load_config
    make_project(tmp_path)
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor.write_anchor(
        paths.anchor_file(cfg),
        updated="2026-09-25 10:00",
        last_session="Session_2026-09-25_1000",
        active_topics=["the memory map"],
        active_notes=["knowledge-base/Architecture"],
    )
    stm.append(paths.stm_file(cfg), "un-consolidated fact")

    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "compact"}, root=tmp_path)

    assert "finish sleeping" in text.lower()          # the demand still stands
    assert "Session_2026-09-25_1000" in text          # …and no longer hides the anchor
    assert "knowledge-base/Architecture" in text
    assert text.index("finish sleeping") < text.index("Session_2026-09-25_1000")


def _vault_note(root: Path, folder: str, title: str) -> None:
    d = root / ".ai_vault" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{title}.md").write_text(f"# {title}\n", encoding="utf-8")


def test_user_prompt_submit_injects_the_memory_map(tmp_path: Path):
    # Between two compactions nothing else tells the model the vault exists.
    make_project(tmp_path)
    _vault_note(tmp_path, "knowledge-base", "Cart Service")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 100)
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "Memory map" in text
    assert "Cart Service" in text


def test_user_prompt_submit_puts_the_pressure_line_after_the_map(tmp_path: Path):
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    _vault_note(tmp_path, "knowledge-base", "Cart Service")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 900)  # 90% -> force
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "Cart Service" in text
    assert text.index("Cart Service") < text.index("/sleep")


def test_user_prompt_submit_maps_nothing_outside_a_project(tmp_path: Path):
    # No config.toml, so a neighbouring .ai_vault is not ours to advertise.
    _vault_note(tmp_path, "knowledge-base", "Cart Service")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 900)
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "Cart Service" not in text


def test_user_prompt_submit_always_states_the_measured_context(tmp_path: Path):
    """The model invented "~75%" because no measured figure was ever in front of it.

    Below the warn threshold this hook said nothing at all, and no skill passes --transcript
    to `lts status`, so the one number that exists was never quotable. Observed 2026-09-25 in
    ppss: the turn's own usage was 276,013 (27.6%) and the model reported ~75%.
    """
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 276)  # 27.6% — well below warn, where the hook used to be silent
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "Context window" in text
    assert "28%" in text
    assert "/sleep" not in text          # a figure, not a nudge: there is no pressure yet


def test_the_pressure_nudge_never_quotes_a_band_of_its_own(tmp_path: Path):
    # "(~60%+)" in the nudge is what invited the model to state a specific number it had not
    # measured. The measured line sits right above it; the nudge only says what to do.
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 900)
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "90%" in text and "/sleep" in text
    assert "60%+" not in text and "80%+" not in text


def test_session_start_prepends_the_demand_when_a_check_fails(tmp_path: Path):
    """A failing check must be the first thing the model reads, because it says the
    rest may be untrustworthy."""
    from tests.test_health import _cfg, _vault
    _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A"]})       # no .claude/settings.json -> hooks fail
    ss = _load("session_start", HOOKS / "session_start.py")
    out = ss.build_context({"cwd": str(tmp_path)})
    assert out.startswith("## Memory health:")
    assert "hooks:" in out


def test_session_start_says_nothing_new_when_memory_is_sound(tmp_path: Path):
    from tests.test_health import _healthy
    _healthy(tmp_path)
    ss = _load("session_start", HOOKS / "session_start.py")
    out = ss.build_context({"cwd": str(tmp_path)})
    assert "Memory health" not in out


def test_session_start_journals_one_record_naming_what_it_emitted(tmp_path: Path):
    from lts import journal, paths
    from lts import anchor as anchor_mod
    from tests.test_health import _healthy
    cfg = _healthy(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2099-01-01 00:00",
                            last_session="s", active_topics=["t"], active_notes=["n"])
    ss = _load("session_start", HOOKS / "session_start.py")
    ss.build_context({"cwd": str(tmp_path)})
    records = journal.tail(paths.health_journal_file(cfg), 5)
    assert len(records) == 1
    assert "anchor" in records[0]["blocks"]
    assert records[0]["checks"]["hooks"] == "ok"


def test_session_start_records_the_demand_it_emitted(tmp_path: Path):
    from lts import journal, paths
    from lts.config import load_config
    from tests.test_health import _cfg, _vault
    _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A"]})
    ss = _load("session_start", HOOKS / "session_start.py")
    ss.build_context({"cwd": str(tmp_path)})
    cfg = load_config(tmp_path)
    assert "health_demand" in journal.tail(paths.health_journal_file(cfg), 1)[0]["blocks"]


def test_session_start_survives_an_unreadable_journal(tmp_path: Path):
    """A journal that can break a hook would be a new way for memory to die."""
    from lts import paths
    from lts.config import load_config
    from tests.test_health import _healthy
    _healthy(tmp_path)
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    paths.health_journal_file(cfg).write_text("{broken\n", encoding="utf-8")
    ss = _load("session_start", HOOKS / "session_start.py")
    ss.build_context({"cwd": str(tmp_path)})     # must not raise


def test_context_line_refuses_to_print_an_impossible_percentage():
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    line = ups._context_line(45_413_420, 1_000_000)
    assert "%" not in line
    assert "45,413,420" in line and "1,000,000" in line
    assert "impossible" in line


def test_context_line_still_prints_a_real_percentage():
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    line = ups._context_line(276_013, 1_000_000)
    assert "276,013/1,000,000" in line and "28%" in line


def test_user_prompt_submit_does_not_nudge_on_a_figure_it_has_just_disowned(tmp_path: Path):
    """One measurement, one verdict. The block used to say the figure was unknown and then
    demand a /sleep derived from it."""
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 45_413)      # far past the window: impossible
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "impossible" in text
    assert "/sleep" not in text
