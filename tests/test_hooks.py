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


def test_user_prompt_submit_silent_when_low(tmp_path: Path):
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    _usage_transcript(t, 100)  # 10% -> none
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    assert ups.build_context({"transcript_path": str(t)}, root=tmp_path) == ""
    assert ups.run({"transcript_path": str(t)}, root=tmp_path) == {}


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
