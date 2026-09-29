import json
from pathlib import Path

from lts import health, sync
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


import json as _json
from datetime import datetime, timedelta, timezone

from lts import paths, watermark


def _mark(cfg, which: str, when: datetime) -> None:
    target = paths.capture_mark_file(cfg) if which == "capture" else paths.sleep_mark_file(cfg)
    paths.ensure_sidecar(cfg)
    watermark.write_mark(target, watermark.mark_at(when))


def _transcript(path: Path, stamps: list[str]) -> Path:
    """A transcript with one assistant message per stamp, plus a usage record."""
    lines = []
    for i, stamp in enumerate(stamps):
        lines.append(_json.dumps({
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
    """The 9900% bug, caught as an assertion on an output."""
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
    from lts import anchor as anchor_mod
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2020-01-01 00:00",
                            last_session="old", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "warn"
    assert "a session behind" in check.message


def test_a_fresh_anchor_passes(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    paths.ensure_sidecar(cfg)
    from lts import anchor as anchor_mod
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
