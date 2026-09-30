import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lts import anchor as anchor_mod
from lts import health, healthchecks, paths, sync, watermark
from lts.config import load_config
from tests.helpers import make_project


def _cfg(root: Path, extra: str = ""):
    make_project(root, extra)
    return load_config(root)


def _vault(root: Path, notes: dict[str, list[str]] | None = None) -> Path:
    """Build a vault: {folder: [note titles]}. "" means a note at the vault root."""
    vault = root / ".ai_vault"
    vault.mkdir(parents=True, exist_ok=True)
    for folder, titles in (notes or {}).items():
        d = vault / folder if folder else vault
        d.mkdir(parents=True, exist_ok=True)
        for t in titles:
            (d / f"{t}.md").write_text(f"# {t}\n", encoding="utf-8")
    return vault


def _wire_hooks(root: Path, *, scripts_at: Path, events: list[str] | None = None) -> None:
    """Write a .claude/settings.json wiring `events` at real, existing script files."""
    events = events if events is not None else list(sync.HOOK_EVENTS)
    scripts_at.mkdir(parents=True, exist_ok=True)
    hooks = {}
    for event in events:
        script = scripts_at / sync.HOOK_EVENTS[event]
        script.write_text("", encoding="utf-8")
        hooks[event] = [{"hooks": [{"type": "command", "command": f'python3 "{script}"'}]}]
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


def _wire_commands(root: Path, commands: dict[str, str]) -> None:
    """Write a .claude/settings.json with a literal command per event, verbatim.

    `_wire_hooks` only produces the wiring `sync` writes; this one exists for the hand-edited
    shapes the check has to survive (a trailing flag, a quoted interpreter, a wrapper).
    """
    hooks = {
        event: [{"hooks": [{"type": "command", "command": command}]}]
        for event, command in commands.items()
    }
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")


def _wired_project(tmp_path: Path, commands: dict[str, str]):
    """A project wired at real scripts, with `commands` replacing those events verbatim."""
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A"]})
    scripts = tmp_path / "tools"
    scripts.mkdir(parents=True, exist_ok=True)
    full = {}
    for event, script in sync.HOOK_EVENTS.items():
        (scripts / script).write_text("", encoding="utf-8")
        full[event] = f'python3 "{scripts / script}"'
    full.update(commands)
    _wire_commands(tmp_path, full)
    return cfg


def _healthy(root: Path):
    """A project where every inventory check passes."""
    cfg = _cfg(root)
    _vault(root, {"knowledge-base": ["Architecture"]})
    _wire_hooks(root, scripts_at=root / "tools" / "hooks")
    return cfg


def _anchor(cfg, updated: str = "2026-09-29 12:00") -> None:
    """An anchor with content, so `anchor_delivery` has something whose delivery it can judge.

    `_healthy` writes none: a project that has not run /sleep yet is the ordinary state on the day
    it is installed. `SessionStart` records the `anchor` block only when `render_anchor` returned
    something, so without this the journal of a fresh project and the journal of a hook withholding
    a real anchor are the same five records.
    """
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated=updated, last_session="s",
                            active_topics=["topic"], active_notes=["Architecture"])


def _by_id(checks, check_id: str) -> health.Check:
    return next(c for c in checks if c.id == check_id)


def test_worst_orders_failure_above_everything(tmp_path: Path):
    ok = health.Check("a", "ok", "")
    skip = health.Check("b", "skip", "")
    warn = health.Check("c", "warn", "")
    fail = health.Check("d", "fail", "")
    assert health.worst([ok]) == "ok"
    assert health.worst([ok, skip]) == "skip"
    assert health.worst([ok, skip, warn]) == "warn"
    assert health.worst([ok, skip, warn, fail]) == "fail"
    assert health.worst([]) == "ok"


def test_a_demand_names_every_failure_and_its_fix(tmp_path: Path):
    checks = [
        health.Check("hooks", "fail", "the Stop hook script is missing", "run lts update"),
        health.Check("vault", "warn", "vault is empty", "run /sleep"),
    ]
    out = health.demand(checks)
    assert "1 check FAILED" in out
    assert "hooks: the Stop hook script is missing" in out
    assert "Fix: run lts update" in out


def test_a_warning_alone_never_demands_anything(tmp_path: Path):
    """Warnings that interrupt work get trained away, and the failures go with them."""
    assert health.demand([health.Check("vault", "warn", "vault is empty", "x")]) == ""
    assert health.demand([health.Check("vault", "ok", "fine")]) == ""


def test_render_shows_the_worst_first_and_never_hides_a_skip(tmp_path: Path):
    checks = [
        health.Check("config", "ok", "configured"),
        health.Check("capture", "skip", "no transcript path given"),
        health.Check("hooks", "fail", "not wired", "run lts update"),
    ]
    out = health.render(checks)
    assert out.index("hooks") < out.index("capture") < out.index("config")
    assert "no transcript path given" in out
    assert "Fix: run lts update" in out


def test_an_unconfigured_root_fails_config_and_skips_the_rest(tmp_path: Path):
    cfg = load_config(tmp_path / "nowhere")
    checks = health.run(cfg)
    assert _by_id(checks, "config").level == "fail"
    assert {c.level for c in checks if c.id != "config"} == {"skip"}
    assert [c.id for c in checks] == list(health._IDS)


def test_a_healthy_project_passes_every_inventory_check(tmp_path: Path):
    cfg = _healthy(tmp_path)
    checks = health.run(cfg)
    for check_id in ("config", "sidecars", "hooks", "vault"):
        assert _by_id(checks, check_id).level == "ok", _by_id(checks, check_id)


def test_a_stray_sidecar_fails_because_that_memory_is_orphaned(tmp_path: Path):
    cfg = _healthy(tmp_path)
    (tmp_path / "sub" / ".ai_memory").mkdir(parents=True)
    check = _by_id(health.run(cfg), "sidecars")
    assert check.level == "fail"
    assert "sub" in check.message


def test_a_nested_lts_project_is_not_a_stray(tmp_path: Path):
    cfg = _healthy(tmp_path)
    make_project(tmp_path / "sub")
    (tmp_path / "sub" / ".ai_memory").mkdir(parents=True)
    assert _by_id(health.run(cfg), "sidecars").level == "ok"


def test_a_missing_hook_event_fails(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A"]})
    _wire_hooks(tmp_path, scripts_at=tmp_path / "tools", events=["SessionStart", "Stop"])
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail"
    assert "PreCompact" in check.message and "UserPromptSubmit" in check.message


def test_a_wired_script_that_no_longer_exists_fails(tmp_path: Path):
    """The ~/tools move: settings.json still looks right, and capture is dead everywhere."""
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A"]})
    scripts = tmp_path / "tools" / "hooks"
    _wire_hooks(tmp_path, scripts_at=scripts)
    (scripts / sync.HOOK_EVENTS["Stop"]).unlink()
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail"
    assert "Stop" in check.message


def test_a_missing_settings_file_fails_hooks(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A"]})
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail"
    assert "settings.json" in check.message


def test_unreadable_settings_fails_rather_than_passing_quietly(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A"]})
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{not json", encoding="utf-8")
    assert _by_id(health.run(cfg), "hooks").level == "fail"


def test_a_missing_vault_fails_but_an_empty_one_only_warns(tmp_path: Path):
    """A fresh project is legitimately empty; a vanished vault is not."""
    gone = _cfg(tmp_path / "a")
    _wire_hooks(tmp_path / "a", scripts_at=tmp_path / "a" / "tools")
    assert _by_id(health.run(gone), "vault").level == "fail"

    empty = _cfg(tmp_path / "b")
    _vault(tmp_path / "b")
    _wire_hooks(tmp_path / "b", scripts_at=tmp_path / "b" / "tools")
    assert _by_id(health.run(empty), "vault").level == "warn"


def test_a_trailing_flag_does_not_hide_a_missing_script(tmp_path: Path):
    """`python3 /abs/stop.py --verbose`: the last word is the flag, and capture is still dead."""
    cfg = _wired_project(
        tmp_path, {"Stop": f"python3 {tmp_path / 'gone' / 'stop.py'} --verbose"}
    )
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail", check
    assert "Stop" in check.message and "stop.py" in check.message


def test_a_quoted_interpreter_does_not_hide_a_missing_script(tmp_path: Path):
    """The first quoted group can be the interpreter; the script is the next bare word."""
    cfg = _wired_project(
        tmp_path, {"Stop": f'"/usr/bin/python3" {tmp_path / "gone" / "stop.py"}'}
    )
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail", check
    assert "Stop" in check.message and "stop.py" in check.message


def test_a_wrapper_command_is_reported_as_unverified_not_as_healthy(tmp_path: Path):
    """No `.py` anywhere: the wrapper may be fine, but nothing here was checked."""
    cfg = _wired_project(tmp_path, {"Stop": 'bash -c "lts hook stop"'})
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "skip", check
    assert "Stop" in check.message
    assert 'bash -c "lts hook stop"' in check.message


def test_a_wrapper_command_does_not_mask_a_real_failure(tmp_path: Path):
    """An unverifiable command never softens a failure found elsewhere."""
    cfg = _wired_project(
        tmp_path,
        {"Stop": 'bash -c "lts hook stop"',
         "PreCompact": f'python3 "{tmp_path / "gone" / "pre_compact.py"}"'},
    )
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail", check
    assert "PreCompact" in check.message


def test_a_script_wired_to_the_wrong_event_fails(tmp_path: Path):
    """An existing script is not the right script: Stop must not run session_start.py."""
    cfg = _wired_project(
        tmp_path, {"Stop": f'python3 "{tmp_path / "tools" / "session_start.py"}"'}
    )
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail", check
    assert "Stop" in check.message
    assert "stop.py" in check.message and "session_start.py" in check.message


def test_a_demand_counts_more_than_one_failure(tmp_path: Path):
    out = health.demand([
        health.Check("hooks", "fail", "not wired"),
        health.Check("vault", "fail", "vault directory missing"),
    ])
    assert "2 checks FAILED" in out
    assert "hooks: not wired" in out and "vault: vault directory missing" in out


def test_render_shows_a_fix_only_where_something_is_wrong(tmp_path: Path):
    """A passing check has nothing to fix, so its fix text must not be printed."""
    out = health.render([
        health.Check("config", "ok", "configured", "never print this"),
        health.Check("vault", "warn", "vault is empty", "run /sleep"),
    ])
    assert "never print this" not in out
    assert "Fix: run /sleep" in out


def _mark(cfg, which: str, when: datetime) -> None:
    target = paths.capture_mark_file(cfg) if which == "capture" else paths.sleep_mark_file(cfg)
    paths.ensure_sidecar(cfg)
    watermark.write_mark(target, watermark.mark_at(when))


def _transcript(path: Path, stamps: list[str]) -> Path:
    """A transcript with one assistant message per stamp, plus a usage record."""
    lines = []
    for i, stamp in enumerate(stamps):
        lines.append(json.dumps({
            "type": "assistant", "uuid": f"u{i}", "timestamp": stamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": f"msg {i}"}],
                        "usage": {"input_tokens": 100}},
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_equal_marks_are_normal_because_a_sleep_writes_both_at_one_instant(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    _mark(cfg, "sleep", _T0)
    assert _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks").level == "ok"


def test_a_sleep_mark_ahead_of_capture_fails(tmp_path: Path):
    """Consolidation would be claiming material the capture never read."""
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    _mark(cfg, "sleep", _T0 + timedelta(minutes=5))
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=10)), "marks")
    assert check.level == "fail"
    assert "ahead of" in check.message


def test_a_mark_in_the_future_fails(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0 + timedelta(hours=1))
    check = _by_id(health.run(cfg, now=_T0), "marks")
    assert check.level == "fail"
    assert "future" in check.message


def test_no_marks_at_all_is_a_skip_not_a_pass(tmp_path: Path):
    cfg = _healthy(tmp_path)
    assert _by_id(health.run(cfg, now=_T0), "marks").level == "skip"


def test_one_exchange_behind_the_capture_mark_is_the_measured_normal(tmp_path: Path):
    """Stop reads the transcript before the turn's last message is flushed, and mark_of
    deliberately stops short of it — so exactly one behind means nothing is wrong."""
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    tr = _transcript(tmp_path / "t.jsonl", ["2026-09-29T12:30:00.000Z"])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=1)), "capture")
    assert check.level == "ok"


def test_a_stale_capture_mark_with_a_real_backlog_fails(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    tr = _transcript(tmp_path / "t.jsonl", [
        "2026-09-29T12:20:00.000Z", "2026-09-29T12:40:00.000Z", "2026-09-29T13:00:00.000Z",
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=2)), "capture")
    assert check.level == "fail"
    assert "not capturing" in check.message


def test_a_busy_turn_inside_the_tolerance_does_not_trip_capture(tmp_path: Path):
    """Several exchanges within ten minutes is a busy turn, not a dead hook."""
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    tr = _transcript(tmp_path / "t.jsonl", [
        "2026-09-29T12:00:30.000Z", "2026-09-29T12:01:00.000Z", "2026-09-29T12:02:00.000Z",
    ])
    assert _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(minutes=5)),
                  "capture").level == "ok"


def test_capture_skips_with_a_reason_when_no_transcript_is_given(tmp_path: Path):
    cfg = _healthy(tmp_path)
    check = _by_id(health.run(cfg, now=_T0), "capture")
    assert check.level == "skip"
    assert "--transcript" in check.message


def test_tokens_above_the_window_fail_and_name_both_numbers(tmp_path: Path):
    """The impossible-percentage bug, caught as an assertion on an output.

    The numbers are the ones recorded for ppss. The check does not care which of them is
    trustworthy — that is the point of asserting on the output.
    """
    cfg = _healthy(tmp_path)
    metrics = {"pressure": {"tokens": 45_413_420, "window": 1_000_000}}
    check = _by_id(health.run(cfg, metrics=metrics, now=_T0), "pressure")
    assert check.level == "fail"
    assert "45,413,420" in check.message and "1,000,000" in check.message


def test_tokens_inside_the_window_pass(tmp_path: Path):
    cfg = _healthy(tmp_path)
    metrics = {"pressure": {"tokens": 276_013, "window": 1_000_000}}
    check = _by_id(health.run(cfg, metrics=metrics, now=_T0), "pressure")
    assert check.level == "ok"
    assert "28%" in check.message


def test_a_window_of_zero_fails_rather_than_dividing(tmp_path: Path):
    cfg = _healthy(tmp_path)
    metrics = {"pressure": {"tokens": 100, "window": 0}}
    assert _by_id(health.run(cfg, metrics=metrics, now=_T0), "pressure").level == "fail"


def test_pressure_skips_with_a_reason_without_a_transcript(tmp_path: Path):
    cfg = _healthy(tmp_path)
    check = _by_id(health.run(cfg, metrics={"pressure": None}, now=_T0), "pressure")
    assert check.level == "skip"
    assert "--transcript" in check.message


def test_an_anchor_older_than_the_newest_session_note_warns(tmp_path: Path):
    """A /sleep wrote a session note and did not update the anchor."""
    cfg = _healthy(tmp_path)
    _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2020-01-01 00:00",
                            last_session="old", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "warn"
    assert "a session behind" in check.message


def test_a_fresh_anchor_passes(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2099-01-01 00:00",
                            last_session="new", active_topics=[], active_notes=[])
    assert _by_id(health.run(cfg, now=_T0), "anchor_fresh").level == "ok"


def test_session_notes_without_an_anchor_fail(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "fail"
    assert "no anchor" in check.message


def test_team_mode_session_notes_are_found_through_the_author_folder(tmp_path: Path):
    """Team mode nests notes by author, so a flat listing would miss them entirely."""
    cfg = _healthy(tmp_path)
    _vault(tmp_path, {"session-memory/dmitrii": ["Session_dmitrii_2026-09-29_1200"]})
    assert _by_id(health.run(cfg, now=_T0), "anchor_fresh").level == "fail"


def test_no_capture_mark_at_all_is_a_skip_not_a_pass(tmp_path: Path):
    """A hook that is wired but has never written a mark is not a hook that is up to date.

    `entries_after` returns the whole transcript for an empty mark, so the backlog count is real
    but there is no mark to measure it against — and no time claim may be made about it.
    """
    cfg = _healthy(tmp_path)
    tr = _transcript(tmp_path / "t.jsonl", [
        f"2026-09-29T{hour:02d}:00:00.000Z" for hour in range(4, 13)
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=1)), "capture")
    assert check.level == "skip", check
    assert "no capture mark" in check.message
    assert "within" not in check.message


def test_an_unparseable_capture_mark_fails_because_the_backlog_looks_empty(tmp_path: Path):
    """A corrupt mark is the dangerous case: the lexicographic filter reports nothing behind."""
    cfg = _healthy(tmp_path)
    paths.ensure_sidecar(cfg)
    watermark.write_mark(paths.capture_mark_file(cfg), {"timestamp": "not-a-date"})
    tr = _transcript(tmp_path / "t.jsonl", [
        "2026-09-29T12:20:00.000Z", "2026-09-29T12:40:00.000Z", "2026-09-29T13:00:00.000Z",
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=2)), "capture")
    assert check.level == "fail", check
    assert "not-a-date" in check.message


def test_a_mark_inside_the_clock_skew_is_not_called_impossible(tmp_path: Path):
    """_SKEW exists because a hook and the file it reads can disagree by seconds."""
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0 + timedelta(seconds=30))
    assert _by_id(health.run(cfg, now=_T0), "marks").level == "ok"


def test_an_anchor_truncated_to_the_minute_is_not_a_session_behind(tmp_path: Path):
    """_ANCHOR_SKEW exists because /sleep writes the note first and the anchor second, and
    `updated` is minute-granular — so the anchor can look up to a minute older than its note."""
    cfg = _healthy(tmp_path)
    vault = _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    note = vault / "session-memory" / "Session_2026-09-29_1200.md"
    written = datetime(2026, 9, 29, 12, 0, 45).timestamp()  # local, as `fromtimestamp` reads it
    os.utime(note, (written, written))
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:00",
                            last_session="new", active_topics=[], active_notes=[])
    assert _by_id(health.run(cfg, now=_T0), "anchor_fresh").level == "ok"


def _raw_mark(cfg, which: str, payload: dict | str) -> Path:
    """Write a mark file verbatim: a dict as JSON, a string as the literal bytes on disk.

    The degenerate shapes cannot be produced through `_mark`: `write_mark` is a plain,
    non-atomic write, so a hook killed mid-write leaves a partial file, and `mark_of` writes
    `timestamp: None` for an exchange that carried no stamp.
    """
    paths.ensure_sidecar(cfg)
    target = paths.capture_mark_file(cfg) if which == "capture" else paths.sleep_mark_file(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        watermark.write_mark(target, payload)
    return target


def _uuid_transcript(path: Path, entries: list[tuple[str, str | None]]) -> Path:
    """A transcript of (uuid, timestamp) lines — `None` for a line that carries no stamp.

    `_transcript` stamps every line. This one exists because `entries_after` resolves a mark by
    uuid without looking at timestamps at all, so the backlog it returns can legitimately hold
    entries that are unstamped, or older than the mark itself.
    """
    lines = [
        json.dumps({
            "type": "assistant", "uuid": uuid, "timestamp": stamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": f"msg {i}"}],
                        "usage": {"input_tokens": 100}},
        })
        for i, (uuid, stamp) in enumerate(entries)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_sleep_mark_without_a_timestamp_never_claims_consistency(tmp_path: Path):
    """`mark_of` writes `timestamp: None` when the exchange it marks carried no stamp.

    That None short-circuited `sleep > capture` and the check fell through to "marks consistent"
    — asserting consistency between two marks, one of which had never been read. The mark itself
    is still resolvable by uuid, so the honest answer is a skip, not a demand.
    """
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    _raw_mark(cfg, "sleep", {"uuid": "abc", "timestamp": None, "count": 7})
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "skip", check
    assert "sleep mark" in check.message and "abc" in check.message
    assert "consistent" not in check.message
    assert check.fix is None


def test_a_truncated_sleep_mark_file_fails_instead_of_claiming_consistency(tmp_path: Path):
    """`write_mark` is a plain write, so a killed hook leaves a partial file behind, and
    `read_mark` swallows the JSONDecodeError into {} — indistinguishable from "no mark at all".

    A corrupt sleep mark is the single state in which "consolidation claims material the capture
    never read" cannot be ruled out, so it is the last one that may be reported as healthy.
    """
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    _raw_mark(cfg, "sleep", '{"timestamp": "2026-09-2')
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "fail", check
    assert "sleep mark" in check.message
    assert "consistent" not in check.message


def test_an_unparseable_capture_mark_fails_the_marks_check_as_well(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "sleep", _T0)
    _raw_mark(cfg, "capture", {"timestamp": "not-a-date", "count": 7})
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "fail", check
    assert "capture mark" in check.message and "not-a-date" in check.message


def test_marks_consistent_is_only_reachable_when_both_marks_were_read(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    _mark(cfg, "sleep", _T0 - timedelta(minutes=5))
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "ok", check
    assert "consistent" in check.message


def test_a_capture_mark_alone_passes_without_claiming_a_comparison(tmp_path: Path):
    """Captured and not yet consolidated is the ordinary state of a working project."""
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "ok", check
    assert "no sleep mark" in check.message
    assert "consistent" not in check.message


def test_a_sleep_mark_with_no_capture_mark_cannot_be_compared(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "sleep", _T0)
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "skip", check
    assert "no capture mark" in check.message


def test_a_capture_mark_with_no_timestamp_is_not_reported_as_never_written(tmp_path: Path):
    """`entries_after` could have resolved this mark exactly, by uuid — the file is right there,
    so "no capture mark yet" is a false statement and "the next Stop hook writes one" misdirects.
    """
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "u0", "timestamp": None, "count": 1})
    tr = _transcript(tmp_path / "t.jsonl", ["2026-09-29T12:20:00.000Z"])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=1)), "capture")
    assert check.level == "skip", check
    assert "resolved by uuid" in check.message
    assert "no capture mark yet" not in check.message
    assert "within" not in check.message


def test_a_truncated_capture_mark_file_is_not_reported_as_never_written(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", '{"timestamp": "2026-09-2')
    tr = _transcript(tmp_path / "t.jsonl", ["2026-09-29T12:20:00.000Z"])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=1)), "capture")
    assert check.level == "skip", check
    assert "could not be read" in check.message
    assert "no capture mark yet" not in check.message
    assert "within" not in check.message


def test_a_backlog_older_than_its_own_mark_is_not_called_within_tolerance(tmp_path: Path):
    """A rewritten transcript, reached through `entries_after`'s uuid branch: the backlog carries
    stamps *older* than the mark, so the lag is negative and slipped under the threshold — and
    "within 10 min of the newest exchange" was printed however far off it was."""
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "u0", "timestamp": "2026-09-29T12:00:00.000Z", "count": 1})
    tr = _uuid_transcript(tmp_path / "t.jsonl", [
        ("u0", "2026-09-29T12:00:00.000Z"),
        ("u1", "2026-09-29T09:00:00.000Z"),
        ("u2", "2026-09-29T09:05:00.000Z"),
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=1)), "capture")
    assert check.level == "fail", check
    assert "within" not in check.message
    assert "175 min" in check.message  # 12:00 back to 09:05, as a distance


def test_an_unstamped_backlog_is_measured_to_now_and_says_which(tmp_path: Path):
    """`transcript.read_exchanges` stores `timestamp: None` for a line without one, and the uuid
    branch of `entries_after` returns those entries unfiltered — so the newest exchange can carry
    no stamp, and the lag must then be measured to `now` and say so."""
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "u0", "timestamp": "2026-09-29T03:00:00.000Z", "count": 1})
    tr = _uuid_transcript(tmp_path / "t.jsonl", [
        ("u0", "2026-09-29T03:00:00.000Z"), ("u1", None), ("u2", None),
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0), "capture")
    assert check.level == "fail", check
    assert "540 min" in check.message
    assert "no readable timestamp" in check.message


def test_a_transcript_path_that_is_not_there_skips(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    check = _by_id(health.run(cfg, transcript_path=tmp_path / "gone.jsonl", now=_T0), "capture")
    assert check.level == "skip", check
    assert "transcript not found" in check.message


def test_an_anchor_with_no_session_notes_passes_saying_there_was_nothing_to_compare(
    tmp_path: Path,
):
    cfg = _healthy(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:00",
                            last_session="new", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "ok", check
    assert "no session notes" in check.message


def test_session_notes_that_cannot_be_statted_skip_instead_of_comparing(tmp_path: Path):
    """A note listed but not stattable (it vanished between the two calls) leaves nothing to
    compare the anchor against — and a skip no action can make measurable carries no fix."""
    cfg = _healthy(tmp_path)
    vault = _vault(tmp_path, {"session-memory": []})
    (vault / "session-memory" / "Session_2026-09-29_1200.md").symlink_to(tmp_path / "gone.md")
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:00",
                            last_session="new", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "skip", check
    assert "could be read to compare" in check.message
    assert check.fix is None


def test_an_anchor_with_an_unreadable_updated_warns(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="whenever",
                            last_session="new", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "warn", check
    assert "whenever" in check.message


def test_no_check_reports_ok_for_a_measurement_it_could_not_have_made(tmp_path: Path):
    """The regression net for one defect class, found three separate times in `lts.health`.

    Each instance reported a check as passing while the comparison behind the claim had not run:

    1. `hooks` printed "all 4 events wired, scripts present" when `_script_path` returned None
       for a command it could not parse and that None was dropped;
    2. `capture` printed "N exchanges behind the capture mark, within 10 min of the newest
       exchange" when the mark was absent or unparseable, so no lag had been computed at all;
    3. `marks` printed "marks consistent" when one of the two marks was unreadable, because the
       None operand short-circuited the `sleep > capture` comparison.

    The trend checks are in the net too, and they need a state that supplies `history`: without one
    they land on the "no journal history" skip, which is a different claim and would exercise
    nothing. Theirs is the same defect in its quantitative form — describing a window they only
    partly read as though they had read all of it.

    So for every degenerate memory state below the affected check must land on skip, warn or
    fail, and its message must be free of a quantitative claim it did not earn.
    """
    # "events wired," not "wired": the hooks *failure* message reads "not wired: <events>",
    # so the bare word would make a correct fail trip this net. "in the last" is the trend form of
    # the same claim — a message that says "in the last 5 sessions" has claimed the whole window,
    # which a degenerate state cannot have earned; a partial measurement says "in the 4 of the last
    # 5 sessions whose records could be read" and does not match.
    unearned = ("within", "consistent", "scripts present", "events wired,", "in the last")

    def no_mark_file(root: Path):
        return _healthy(root), {}

    def truncated_sleep_mark(root: Path):
        cfg = _healthy(root)
        _mark(cfg, "capture", _T0)
        _raw_mark(cfg, "sleep", '{"timestamp": "2026-09-2')
        return cfg, {}

    def sleep_mark_without_a_timestamp(root: Path):
        cfg = _healthy(root)
        _mark(cfg, "capture", _T0)
        _raw_mark(cfg, "sleep", {"uuid": "abc", "timestamp": None, "count": 7})
        return cfg, {}

    def truncated_capture_mark(root: Path):
        cfg = _healthy(root)
        _raw_mark(cfg, "capture", '{"timestamp": "2026-09-2')
        return cfg, {"transcript_path": _transcript(
            root / "t.jsonl", ["2026-09-29T11:00:00.000Z", "2026-09-29T11:30:00.000Z"])}

    def an_unstamped_transcript(root: Path):
        cfg = _healthy(root)
        _raw_mark(cfg, "capture",
                  {"uuid": "u0", "timestamp": "2026-09-29T03:00:00.000Z", "count": 1})
        return cfg, {"transcript_path": _uuid_transcript(
            root / "t.jsonl", [("u0", "2026-09-29T03:00:00.000Z"), ("u1", None), ("u2", None)])}

    def unstattable_session_notes(root: Path):
        cfg = _healthy(root)
        vault = _vault(root, {"session-memory": []})
        (vault / "session-memory" / "Session_2026-09-29_1200.md").symlink_to(root / "gone.md")
        paths.ensure_sidecar(cfg)
        anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:00",
                                last_session="new", active_topics=[], active_notes=[])
        return cfg, {}

    def a_hook_command_nothing_can_be_read_from(root: Path):
        # Instance 1 of the class: `_script_path` returns None for a command it cannot parse, and
        # the None used to be dropped — leaving "all 4 events wired, scripts present".
        return _wired_project(root, {"Stop": 'bash -c "lts hook stop"'}), {}

    def a_resolving_uuid_mark(root: Path):
        cfg = _healthy(root)
        _raw_mark(cfg, "capture", {"uuid": "u0", "timestamp": "not-a-date", "count": 1})
        return cfg, {"transcript_path": _transcript(
            root / "t.jsonl", ["2026-09-29T11:00:00.000Z", "2026-09-29T11:30:00.000Z"])}

    def a_trend_record_the_window_cannot_read(root: Path):
        # Four readable records out of five: measurable, but only over four.
        history = _runs(5)
        history[2]["stm_entries"] = "lots"
        return _healthy(root), {"history": history}

    def a_trend_window_with_too_few_readable_records(root: Path):
        history = _runs(5)
        for record in history[:4]:
            record["blocks"] = 5
        return _healthy(root), {"history": history}

    def a_uuid_mark_the_transcript_does_not_hold(root: Path):
        cfg = _healthy(root)
        _raw_mark(cfg, "capture", {"uuid": "rotated-away", "timestamp": "not-a-date", "count": 1})
        return cfg, {"transcript_path": _transcript(
            root / "t.jsonl", ["2026-09-29T11:00:00.000Z", "2026-09-29T11:30:00.000Z"])}

    states = [
        ("a hook command with no script in it", a_hook_command_nothing_can_be_read_from, "hooks"),
        ("no mark file at all", no_mark_file, "marks"),
        ("a sleep mark file truncated mid-write", truncated_sleep_mark, "marks"),
        ("a sleep mark whose timestamp is None", sleep_mark_without_a_timestamp, "marks"),
        ("a capture mark file truncated mid-write", truncated_capture_mark, "capture"),
        ("a transcript whose entries carry no timestamps", an_unstamped_transcript, "capture"),
        ("session notes that cannot be statted", unstattable_session_notes, "anchor_fresh"),
        ("an unparseable stamp whose uuid resolves", a_resolving_uuid_mark, "capture"),
        ("an unparseable stamp whose uuid is gone", a_uuid_mark_the_transcript_does_not_hold,
         "capture"),
        ("a journal record the trend cannot read", a_trend_record_the_window_cannot_read,
         "capture_progress"),
        ("a trend window with too few readable records",
         a_trend_window_with_too_few_readable_records, "anchor_delivery"),
    ]
    for i, (label, build, check_id) in enumerate(states):
        cfg, kwargs = build(tmp_path / f"state{i}")
        check = _by_id(health.run(cfg, now=_T0, **kwargs), check_id)
        assert check.level in {"skip", "warn", "fail"}, (label, check)
        for phrase in unearned:
            assert phrase not in check.message, (label, phrase, check)


# --- a mark that still resolves is not corruption ----------------------------------------------
#
# `entries_after` tries the uuid branch first and returns `exchanges[i+1:]` on a match, never
# consulting the timestamp — so a `mark_of` shape whose exchange carried no stamp resolves
# exactly, and a demand raised against it is a false alarm. It *falls through* to the
# lexicographic timestamp branch when the uuid is not among the exchanges, which is where an
# unparseable stamp becomes dangerous again.


def test_a_positional_capture_mark_skips_the_marks_check_rather_than_demanding(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _mark(cfg, "sleep", _T0)
    _raw_mark(cfg, "capture", {"uuid": "abc", "timestamp": None, "count": 7})
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "skip", check
    assert "capture mark" in check.message and "abc" in check.message
    assert "consistent" not in check.message
    assert check.fix is None


def test_a_corrupt_mark_outranks_a_positional_one_in_the_marks_check(tmp_path: Path):
    """A skip must never be returned in place of a failure found on the other mark."""
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "abc", "timestamp": None, "count": 7})
    _raw_mark(cfg, "sleep", '{"timestamp": "2026-09-2')
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "fail", check
    assert "sleep mark" in check.message


def test_a_mark_with_neither_a_timestamp_nor_a_uuid_still_fails(tmp_path: Path):
    """Nothing left to resolve by: not comparable, and not recoverable either."""
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    _raw_mark(cfg, "sleep", {"count": 7})
    check = _by_id(health.run(cfg, now=_T0 + timedelta(minutes=1)), "marks")
    assert check.level == "fail", check
    assert "sleep mark" in check.message


def test_an_unparseable_capture_stamp_whose_uuid_resolves_skips_with_the_count(tmp_path: Path):
    """The uuid branch ran and resolved the backlog exactly, so the count is real — but the
    timestamp it would have been measured against is not, so no time claim may be made."""
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "u0", "timestamp": "not-a-date", "count": 1})
    tr = _transcript(tmp_path / "t.jsonl", [
        "2026-09-29T11:00:00.000Z", "2026-09-29T11:30:00.000Z", "2026-09-29T12:00:00.000Z",
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=2)), "capture")
    assert check.level == "skip", check
    assert "2 exchange" in check.message          # u1 and u2, resolved positionally
    assert "resolved by uuid" in check.message
    assert "min" not in check.message             # no time claim of any kind
    assert "within" not in check.message


def test_an_unparseable_capture_stamp_whose_uuid_is_gone_still_fails(tmp_path: Path):
    """The subtle one: the uuid branch falls through when the uuid is not found, so the
    lexicographic filter runs on an unparseable stamp and the transcript looks fully captured."""
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "rotated-away", "timestamp": "not-a-date", "count": 1})
    tr = _transcript(tmp_path / "t.jsonl", [
        "2026-09-29T11:00:00.000Z", "2026-09-29T11:30:00.000Z", "2026-09-29T12:00:00.000Z",
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=2)), "capture")
    assert check.level == "fail", check
    assert "rotated-away" in check.message and "not-a-date" in check.message


def test_a_capture_mark_with_no_stamp_whose_uuid_is_gone_falls_back_to_the_count(tmp_path: Path):
    """No stamp at all means no lexicographic trap: `entries_after` reaches its count branch, so
    the backlog is positional guesswork rather than a silent nothing — a skip, not a demand."""
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "rotated-away", "timestamp": None, "count": 1})
    tr = _transcript(tmp_path / "t.jsonl", [
        "2026-09-29T11:00:00.000Z", "2026-09-29T11:30:00.000Z",
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=2)), "capture")
    assert check.level == "skip", check
    assert "rotated-away" in check.message
    assert "would be resolved by count" in check.message  # nothing has been resolved yet
    assert "within" not in check.message


def test_a_future_mark_is_reported_even_when_the_other_mark_is_positional(tmp_path: Path):
    """A clock fault is a fault of the mark that parsed, so the other mark's shape cannot excuse it.

    The positional `skip` returned before the future-mark comparison ran, so a sleep mark five
    hours ahead of `now` went unreported whenever the capture mark happened to be positional —
    a `skip` standing in for a `fail` on the other mark, which is what the two-pass order exists
    to prevent.
    """
    for i, shape in enumerate(({"uuid": "abc", "timestamp": None},
                               {"uuid": "abc", "timestamp": "not-a-date"})):
        cfg = _healthy(tmp_path / f"shape{i}")
        _raw_mark(cfg, "capture", shape)
        _mark(cfg, "sleep", _T0 + timedelta(hours=5))
        check = _by_id(health.run(cfg, now=_T0), "marks")
        assert check.level == "fail", (shape, check)
        assert "future" in check.message, (shape, check)
        assert "clock" in (check.fix or ""), (shape, check)


def test_a_non_numeric_token_count_does_not_take_the_whole_report_down(tmp_path: Path):
    """`_check_anchor_fresh` goes out of its way not to raise out of `run`; so must this one.

    A traceback out of `run` takes every other check's answer with it — the report that says
    whether memory is lying would simply not appear.
    """
    cfg = _healthy(tmp_path)
    metrics = {"pressure": {"tokens": "lots", "window": 1_000_000}}
    check = _by_id(health.run(cfg, metrics=metrics, now=_T0), "pressure")
    assert check.level in {"fail", "skip"}, check
    assert "lots" in check.message


def test_a_pressure_measurement_that_is_not_a_measurement_does_not_raise(tmp_path: Path):
    cfg = _healthy(tmp_path)
    check = _by_id(health.run(cfg, metrics={"pressure": "yes"}, now=_T0), "pressure")
    assert check.level in {"fail", "skip"}, check
    assert "yes" in check.message


def _runs(n: int, *, blocks=("anchor",), mark="M1", entries=0) -> list[dict]:
    return [
        {"event": "session_start", "blocks": list(blocks),
         "capture_mark": mark, "stm_entries": entries}
        for _ in range(n)
    ]


def test_a_field_no_trend_knows_how_to_read_is_unusable_rather_than_fatal(tmp_path: Path):
    """`_trend_window` takes field names from its caller, and a name outside `_TREND_FIELDS` used to
    raise `KeyError` out of the guard written to stop exactly that — one new trend check away."""
    carrying = _runs(5)
    for record in carrying:
        record["nonesuch"] = "x"          # the value that reached `_TREND_FIELDS[field]` and raised
    for history in (_runs(5), carrying):  # the field absent, and the field present
        usable, skipped = healthchecks._trend_window(history, "capture_progress", "nonesuch")
        assert usable == []
        assert skipped is not None and skipped.level == "skip", skipped
        assert "nonesuch" in skipped.message, skipped
        assert skipped.fix is None, skipped


def test_five_sessions_without_the_anchor_fail(tmp_path: Path):
    """The two-month bug, in one check. Undetectable from a filesystem snapshot."""
    cfg = _healthy(tmp_path)
    _anchor(cfg)
    history = _runs(5, blocks=("sleep_demand",))
    check = _by_id(health.run(cfg, history=history, now=_T0), "anchor_delivery")
    assert check.level == "fail"
    assert "has not reached the model" in check.message


def test_a_project_that_has_not_slept_yet_is_not_told_the_anchor_is_being_withheld(tmp_path: Path):
    """No anchor and five runs without one are the ordinary first days of a project.

    The journal cannot tell that state from a hook withholding a real anchor: neither records the
    block. So the check asks the filesystem whether there was anything to deliver, and reports the
    two apart — a demand here would be a false demand on every project between install and its
    first /sleep, and it landed beside `anchor_fresh` skipping the same object for the same reason.
    """
    cfg = _healthy(tmp_path)
    history = _runs(5, blocks=("digest",))
    checks = health.run(cfg, history=history, now=_T0)
    check = _by_id(checks, "anchor_delivery")
    assert check.level == "skip", check
    assert "no anchor to deliver" in check.message, check
    assert "/sleep" in check.message, check
    assert check.fix is None, check
    assert "anchor_delivery" not in health.demand(checks), checks
    # …and the demand returns the moment there is an anchor the runs could have carried.
    _anchor(cfg)
    withheld = _by_id(health.run(cfg, history=history, now=_T0), "anchor_delivery")
    assert withheld.level == "fail", withheld
    assert "has not reached the model" in withheld.message, withheld


def test_one_anchor_in_the_window_is_enough(tmp_path: Path):
    cfg = _healthy(tmp_path)
    history = _runs(4, blocks=("sleep_demand",)) + _runs(1, blocks=("anchor",))
    assert _by_id(health.run(cfg, history=history, now=_T0),
                  "anchor_delivery").level == "ok"


def test_a_short_history_skips_the_trend_rather_than_passing_it(tmp_path: Path):
    """The journal starts empty in every project; a young trend must say so, not say ok."""
    cfg = _healthy(tmp_path)
    check = _by_id(health.run(cfg, history=_runs(4, blocks=("sleep_demand",)), now=_T0),
                   "anchor_delivery")
    assert check.level == "skip"
    # Not a bare "5": "0 of 5" would satisfy that, leaving the count the check actually has unpinned.
    assert f"4 of {healthchecks._TREND_WINDOW} runs journalled" in check.message


def test_a_frozen_mark_and_a_flat_buffer_fail_capture_progress(tmp_path: Path):
    cfg = _healthy(tmp_path)
    check = _by_id(health.run(cfg, history=_runs(5, mark="FROZEN", entries=0), now=_T0),
                   "capture_progress")
    assert check.level == "fail"
    assert "not capturing" in check.message


def test_a_moving_mark_passes_even_with_an_always_empty_buffer(tmp_path: Path):
    """Sleeping every session keeps stm_entries at 0 legitimately, so the mark decides."""
    cfg = _healthy(tmp_path)
    history = [
        {"blocks": ["anchor"], "capture_mark": f"M{i}", "stm_entries": 0} for i in range(5)
    ]
    check = _by_id(health.run(cfg, history=history, now=_T0), "capture_progress")
    assert check.level == "ok"
    # The ok message names the signal it measured and the window it measured over; "progressing"
    # named neither, and nothing in the unearned-phrase net could bite on it.
    assert "capture mark moved" in check.message, check
    assert f"last {healthchecks._TREND_WINDOW} sessions" in check.message, check


def test_a_growing_buffer_passes_even_with_a_frozen_mark_field(tmp_path: Path):
    cfg = _healthy(tmp_path)
    history = [
        {"blocks": ["anchor"], "capture_mark": "SAME", "stm_entries": i} for i in range(5)
    ]
    check = _by_id(health.run(cfg, history=history, now=_T0), "capture_progress")
    assert check.level == "ok"
    assert "buffer grew" in check.message, check
    assert f"last {healthchecks._TREND_WINDOW} sessions" in check.message, check


def test_no_history_skips_both_trend_checks(tmp_path: Path):
    cfg = _healthy(tmp_path)
    checks = health.run(cfg, now=_T0)
    assert _by_id(checks, "anchor_delivery").level == "skip"
    assert _by_id(checks, "capture_progress").level == "skip"


def test_a_malformed_journal_record_costs_itself_and_not_the_trend(tmp_path: Path):
    """A record read off disk may hold any JSON type, and the trend checks used to raise on each
    shape below — taking all ten checks down with them, and (once `SessionStart` reads the journal
    from disk) the hook itself. `lts.journal` filters non-JSON and non-dict lines but not field
    types, and its promise is that a bad line costs itself, never the history. Skipping the whole
    check would break that promise one layer up: one corrupt line would blind the trend for five
    sessions. So the record is dropped, the rest is measured, and the message says how much of the
    window was read.
    """
    cfg = _healthy(tmp_path)
    _anchor(cfg)   # so the `blocks` window is judged on delivery, not skipped for having nothing
    shapes = [
        # The third record of each window is the malformed one. The `blocks` window carries no
        # anchor anywhere, so the type error is actually reached rather than short-circuited past.
        ("stm_entries", "lots", "capture_progress", "anchor"),            # ValueError
        ("capture_mark", {"timestamp": "x"}, "capture_progress", "anchor"),  # unhashable in a set
        ("blocks", 5, "anchor_delivery", "sleep_demand"),                 # not iterable
    ]
    for field, value, check_id, block in shapes:
        history = _runs(5, blocks=(block,))
        history[2][field] = value
        checks = health.run(cfg, history=history, now=_T0)
        assert [c.id for c in checks] == list(health._IDS), (field, value)
        check = _by_id(checks, check_id)
        # The four usable records still carry the failure, and the claim is about four, not five.
        assert check.level == "fail", (field, check)
        assert f"4 of the last {healthchecks._TREND_WINDOW}" in check.message, (field, check)
        assert "records could be read" in check.message, (field, check)
        # …and it does not describe itself as a measurement over the whole window.
        assert f"in the last {healthchecks._TREND_WINDOW} sessions" not in check.message, (field, check)


def test_the_records_left_after_a_bad_one_are_measured_not_assumed_to_fail(tmp_path: Path):
    """Dropping the record must not degrade into a default `fail`: what is left is read."""
    cfg = _healthy(tmp_path)
    history = [
        {"blocks": ["anchor"], "capture_mark": f"M{i}", "stm_entries": 0} for i in range(5)
    ]
    history[1]["stm_entries"] = "lots"
    check = _by_id(health.run(cfg, history=history, now=_T0), "capture_progress")
    assert check.level == "ok", check
    assert "capture mark moved" in check.message, check
    assert f"4 of the last {healthchecks._TREND_WINDOW}" in check.message, check


def test_too_few_usable_records_skip_the_trend_naming_both_counts(tmp_path: Path):
    """Below two records there is no change to see, so the trend says so instead of judging."""
    cfg = _healthy(tmp_path)
    history = _runs(5)
    for record in history[:4]:
        record["capture_mark"] = {"timestamp": "x"}
    check = _by_id(health.run(cfg, history=history, now=_T0), "capture_progress")
    assert check.level == "skip", check
    assert f"1 of the last {healthchecks._TREND_WINDOW}" in check.message, check   # usable
    assert f"at least {healthchecks._TREND_MIN}" in check.message, check           # required
    assert "capture_mark" in check.message, check                            # what was unusable
    assert check.fix is None, check
    # Two usable records are enough to see a change, so the trend is measured rather than skipped.
    two = _runs(5, mark="FROZEN")
    for record in two[:3]:
        record["capture_mark"] = {"timestamp": "x"}
    measured = _by_id(health.run(cfg, history=two, now=_T0), "capture_progress")
    assert measured.level == "fail", measured
    assert f"2 of the last {healthchecks._TREND_WINDOW}" in measured.message, measured


def test_no_history_is_not_the_same_as_an_empty_journal(tmp_path: Path):
    """"0 of 5 runs journalled" for a `history` nobody passed is a statement about a file nobody
    read — a check that quietly did not run, reported as a measurement. Once `SessionStart` reads
    the journal, forgetting to pass it would leave that sentence standing forever."""
    checks = health.run(_healthy(tmp_path), now=_T0)
    for check_id in ("anchor_delivery", "capture_progress"):
        check = _by_id(checks, check_id)
        assert check.level == "skip", check
        assert "journalled" not in check.message, check
        assert "no journal history" in check.message, check
        assert check.fix is None, check          # a wiring fault; there is nothing to do here


def test_a_supplied_but_short_history_still_counts_its_runs(tmp_path: Path):
    cfg = _healthy(tmp_path)
    short = health.run(cfg, history=_runs(2), now=_T0)
    empty = health.run(cfg, history=[], now=_T0)
    for check_id in ("anchor_delivery", "capture_progress"):
        assert f"2 of {healthchecks._TREND_WINDOW} runs journalled" in _by_id(short, check_id).message
        assert f"0 of {healthchecks._TREND_WINDOW} runs journalled" in _by_id(empty, check_id).message


def test_a_fresh_project_is_not_told_the_stop_hook_is_broken(tmp_path: Path):
    """Five SessionStart runs before the first exchange record `capture_mark: None` every time and
    a flat buffer — and so do five runs of a project whose Stop hook died on install. From the
    journal the two are the same records, so the check names both and demands neither; only the
    frozen mark, which proves a hook ran once, is a demand."""
    cfg = _healthy(tmp_path)
    fresh = _by_id(health.run(cfg, history=_runs(5, mark=None), now=_T0), "capture_progress")
    assert fresh.level == "skip", fresh
    assert "no capture mark" in fresh.message, fresh
    assert "nothing has been captured here yet" in fresh.message, fresh   # the benign reading
    assert "the Stop hook has never written a mark" in fresh.message, fresh   # and the other one
    assert "hooks check" in (fresh.fix or ""), fresh
    assert "the Stop hook is not capturing" not in fresh.message, fresh
    frozen = _by_id(health.run(cfg, history=_runs(5, mark="FROZEN"), now=_T0), "capture_progress")
    assert frozen.level == "fail", frozen
    assert "FROZEN" in frozen.message and "not capturing" in frozen.message, frozen


def test_the_anchor_block_name_is_one_constant_shared_with_the_record(tmp_path: Path):
    """`record`'s caller decides which block names it passes, and a plausible typo there would fail
    this check permanently. So the name is a constant, and the vocabulary is spelled out in the
    docstring that caller reads."""
    cfg = _healthy(tmp_path)
    history = _runs(5, blocks=(healthchecks._ANCHOR_BLOCK,))
    assert _by_id(health.run(cfg, history=history, now=_T0), "anchor_delivery").level == "ok"
    for name in ("health_demand", "sleep_demand", healthchecks._ANCHOR_BLOCK, "digest"):
        assert name in (health.record.__doc__ or ""), name


def test_run_now_returns_every_declared_check_in_id_order(tmp_path: Path):
    """`_IDS` is the vocabulary the record and the short-circuit share, so `run` must cover it."""
    cfg = _healthy(tmp_path)
    assert [c.id for c in health.run(cfg, now=_T0)] == list(health._IDS)


def test_the_record_states_what_the_run_emitted(tmp_path: Path):
    cfg = _healthy(tmp_path)
    checks = health.run(cfg, now=_T0)
    rec = health.record(cfg, checks, ["sleep_demand", "anchor"], now=_T0)
    assert rec["event"] == "session_start"
    assert rec["blocks"] == ["sleep_demand", "anchor"]
    assert rec["checks"]["config"] == "ok"
    assert set(rec["checks"]) == set(health._IDS)
    assert rec["at"] == watermark.mark_at(_T0)["timestamp"]   # one format, not merely a Z
    assert rec["notes"] == 1                      # the one knowledge-base note from _healthy
    assert rec["stm_entries"] == 0 and rec["pending"] == 0


def test_the_record_round_trips_through_the_journal(tmp_path: Path):
    from lts import journal
    cfg = _healthy(tmp_path)
    log = paths.health_journal_file(cfg)
    rec = health.record(cfg, health.run(cfg, now=_T0), ["anchor"], now=_T0)
    journal.append(log, rec)
    assert journal.tail(log, 1)[0]["blocks"] == ["anchor"]


def test_a_nested_lts_project_is_named_rather_than_passed_over_in_silence(tmp_path: Path):
    """`doctor.collect` used to print "! nested lts project root: ...". The check that replaced
    it computed the same discrimination and reported neither half, so the fact left the product."""
    cfg = _healthy(tmp_path)
    make_project(tmp_path / "sub")
    (tmp_path / "sub" / ".ai_memory").mkdir(parents=True)
    check = _by_id(health.run(cfg), "sidecars")
    assert check.level == "ok"  # a nested project is legitimate, not a fault
    assert str(tmp_path / "sub" / ".ai_memory") in check.message


def test_a_named_nested_project_does_not_hide_a_real_stray(tmp_path: Path):
    cfg = _healthy(tmp_path)
    make_project(tmp_path / "sub")
    (tmp_path / "sub" / ".ai_memory").mkdir(parents=True)
    (tmp_path / "orphan" / ".ai_memory").mkdir(parents=True)
    check = _by_id(health.run(cfg), "sidecars")
    assert check.level == "fail"
    assert str(tmp_path / "orphan" / ".ai_memory") in check.message
    assert str(tmp_path / "sub" / ".ai_memory") in check.message
