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


def _healthy(root: Path, extra: str = ""):
    """A project where every inventory check passes."""
    cfg = _cfg(root, extra)
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


def test_a_level_nobody_can_interpret_is_the_most_severe_thing_present():
    """A typo in a level string must not read as a healthy system.

    `worst` walked the four known levels, matched none, and fell through to `"ok"`: measured
    2026-09-30, `worst([Check("x", "boom", "m")])` was `"ok"`, `demand` was `""` and `render`
    printed `? x: m` *below* the checks that passed — so `lts doctor` exited 0 on a report it could
    not read. This is the subsystem's own defect class in the function that decides the exit code.
    """
    unreadable = health.Check("x", "boom", "m", "f")
    ok = health.Check("y", "ok", "fine")
    fail = health.Check("z", "fail", "broken")
    # "fail", not "boom": every caller compares the answer against the four known levels, and
    # `lts.cli` turns `== "fail"` into the exit code.
    assert health.worst([ok, unreadable]) == "fail"
    assert health.worst([ok, unreadable, fail]) == "fail"
    # The demand agrees with the exit code rather than staying silent about it.
    assert "x: m" in health.demand([ok, unreadable])
    assert "1 check FAILED" in health.demand([ok, unreadable])
    # Worst first: it leads the report instead of sitting under the passing checks.
    lines = health.render([ok, unreadable]).splitlines()
    assert lines[1].strip().startswith("? x:"), lines


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


def test_a_directory_where_the_hook_script_should_be_is_not_a_present_script(tmp_path: Path):
    """`exists()` was true for a directory named stop.py, and `python3 <a directory>` runs nothing.

    "all 4 events wired, scripts present" was the message, which is the class of claim this module
    is about. `cli.py` separates `exists` from `is_file` for the same reason on `--transcript`.
    """
    cfg = _healthy(tmp_path)
    script = tmp_path / "tools" / "hooks" / sync.HOOK_EVENTS["Stop"]
    script.unlink()
    script.mkdir()
    check = _by_id(health.run(cfg), "hooks")
    assert check.level == "fail", check
    assert "scripts present" not in check.message, check
    assert str(script) in check.message, check


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


def test_valid_json_of_the_wrong_shape_fails_hooks_and_costs_no_other_check(tmp_path: Path):
    """settings.json is hand-edited, and every shape below used to raise out of `_check_hooks`.

    `all_checks` propagated it, so `lts doctor` printed a traceback instead of a report and the
    `SessionStart` hook — whose `try` keeps the anchor and the digest — lost the health block and
    the journal record silently, on every run, which then starved the trend checks. So: all ten
    checks are still answered, and the one that could not read the file names the shape it found.
    """
    shapes = [
        ({"hooks": {"Stop": {"hooks": [{"command": "python3 x.py"}]}}}, "not a list of hook groups"),
        ({"hooks": [{"Stop": []}]}, "not an object mapping event names"),
        ({"hooks": "Stop"}, "not an object mapping event names"),
        ({"hooks": {"Stop": ["python3 x.py"]}}, "not an object"),
        ({"hooks": {"Stop": [{"hooks": "python3 x.py"}]}}, "not a list"),
        ({"hooks": {"Stop": [{"hooks": ["python3 x.py"]}]}}, "not an object"),
        ({"hooks": {"Stop": [{"hooks": [{"command": 7}]}]}}, "not a string"),
        ([{"hooks": {}}], "not an object"),
    ]
    for i, (shape, said) in enumerate(shapes):
        cfg = _healthy(tmp_path / f"shape{i}")
        settings = cfg.project_root / ".claude" / "settings.json"
        settings.write_text(json.dumps(shape), encoding="utf-8")
        checks = health.run(cfg, now=_T0)
        assert [c.id for c in checks] == list(health._IDS), shape
        check = _by_id(checks, "hooks")
        assert check.level == "fail", (shape, check)
        assert said in check.message, (shape, check)
        # The other nine still answered, and none of them is the raise-report from `_answered`.
        for other in checks:
            assert "raised" not in other.message, (shape, other)
        assert _by_id(checks, "vault").level == "ok", (shape, checks)


def test_an_event_whose_wiring_cannot_be_read_is_not_reported_as_not_wired(tmp_path: Path):
    """"not wired: Stop" is a claim about the file, and the file does wire Stop — unreadably."""
    cfg = _healthy(tmp_path)
    settings = cfg.project_root / ".claude" / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"]["Stop"] = "python3 stop.py"
    settings.write_text(json.dumps(data), encoding="utf-8")
    check = _by_id(health.run(cfg, now=_T0), "hooks")
    assert check.level == "fail", check
    assert "not wired" not in check.message, check
    assert "Stop: a str, not a list of hook groups" in check.message, check


def test_a_check_that_raises_costs_its_own_answer_and_not_the_other_nine(monkeypatch,
                                                                        tmp_path: Path):
    """Every guard in `healthchecks` was written after a raise had already taken all ten checks
    down; `_answered` is the same lesson applied to the raise nobody has met yet. A report that
    does not appear is the worst outcome for the subsystem that says whether memory is lying, and
    the exception's own text is carried because "internal error" names nothing to act on.
    """
    cfg = _healthy(tmp_path)

    def boom(_cfg):
        raise RuntimeError("the vault walk exploded")

    monkeypatch.setattr(healthchecks, "_check_vault", boom)
    checks = health.run(cfg, now=_T0)
    assert [c.id for c in checks] == list(health._IDS)
    broken = _by_id(checks, "vault")
    assert broken.level == "fail", broken
    assert "RuntimeError" in broken.message, broken
    assert "the vault walk exploded" in broken.message, broken   # not merely "internal error"
    assert _by_id(checks, "hooks").level == "ok", checks
    assert _by_id(checks, "config").level == "ok", checks
    # A broken check is a demand: the report has a hole in it and nothing says the vault is fine.
    assert "vault" in health.demand(checks)


def test_an_empty_hook_command_fails_and_an_unparseable_one_skips(tmp_path: Path):
    """The two states are different verdicts, and the message has to tell them apart.

    An empty or whitespace `command` is a wiring that *cannot run*: that event is dead, and it is
    established, not unmeasurable. It used to land in the `unreadable` bucket and therefore in
    `skip`, so `lts doctor` exited 0 on a hook wired at nothing — measured 2026-09-30: level
    `skip`, `worst` `skip`, exit 0 — and `/memory-status` learned only that something had skipped.
    `skip` still belongs to the command from which no script path can be parsed at all: a wrapper
    or a shell one-liner may be legitimate wiring nobody can verify from here.

    The commands are quoted for the reason `_unusable_transcript` quotes a path: an empty value
    otherwise rendered as "no script to check in: Stop -> ", a sentence ending at an arrow.
    """
    for command, shown in ((" ", "' '"), ("", "''")):
        cfg = _wired_project(tmp_path / f"empty{len(command)}", {"Stop": command})
        check = _by_id(health.run(cfg, now=_T0), "hooks")
        assert check.level == "fail", (command, check)
        assert "empty command" in check.message, (command, check)
        assert f"Stop -> {shown}" in check.message, (command, check)

    wrapper = _wired_project(tmp_path / "wrapper", {"Stop": "bash -c 'run-the-hook'"})
    check = _by_id(health.run(wrapper, now=_T0), "hooks")
    assert check.level == "skip", check
    assert "no script to check in" in check.message, check
    assert "empty command" not in check.message, check


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


def test_an_unbalanced_quote_still_yields_the_script_it_quotes(tmp_path: Path):
    """`_tokens` falls back on a `shlex.split` that raised, and that fallback was pinned by nothing.

    Replacing the `except ValueError` body with `return []` left all 351 tests green (measured
    2026-09-30), so the recovery could have been deleted without a word from the suite. A
    hand-edited wiring with one stray quote is the ordinary way in, and what the fallback buys is
    the balanced quoted groups: the script is still found, so the check verifies it instead of
    reporting a hook it could not read.
    """
    assert healthchecks._tokens('python3 "/x/stop.py" --extra "oops') == [
        "/x/stop.py", "python3", "--extra", '"oops',
    ]
    script = tmp_path / "tools" / "stop.py"
    cfg = _wired_project(tmp_path, {"Stop": f'python3 "{script}" --extra "oops'})
    check = _by_id(health.run(cfg), "hooks")
    # Without the fallback there is no `.py` token, so this would be "no script to check in" — a
    # `skip` about a hook that is in fact wired at the right, present script.
    assert check.level == "ok", check


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


def test_a_session_note_five_minutes_newer_than_the_anchor_warns(tmp_path: Path):
    """The other side of `_ANCHOR_SKEW`, which nothing pinned: the test above and
    `test_an_anchor_older_than_the_newest_session_note_warns` bracket it with a 45-second gap and a
    six-year one, so every value between them passed the suite — raising it from 2 minutes to 6
    hours left all 327 tests green. Five minutes is a gap /sleep's own minute-granular `updated`
    cannot explain: the anchor was not rewritten with the note."""
    cfg = _healthy(tmp_path)
    vault = _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1205"]})
    note = vault / "session-memory" / "Session_2026-09-29_1205.md"
    written = datetime(2026, 9, 29, 12, 5, 0).timestamp()   # local, matching the title
    os.utime(note, (written, written))
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:00",
                            last_session="old", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "warn", check
    assert "a session behind" in check.message, check


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


def test_a_transcript_that_predates_the_mark_entirely_is_not_a_caught_up_one(tmp_path: Path):
    """`--transcript` pointed at some other session's file: every exchange is older than the mark.

    The lexicographic filter drops all of them, so the backlog is 0 and "the normal flush lag" was
    printed about a file the mark has nothing to do with — a specific benign claim from a
    comparison that never happened. `d0a9771` closed the neighbouring state, a file with no
    exchanges at all; `cli.py` notes that this path comes from a human at a terminal, where a typo
    is the ordinary case.
    """
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    tr = _transcript(tmp_path / "old.jsonl",
                     [f"2026-08-01T{hour:02d}:00:00.000Z" for hour in (9, 10, 11, 12)])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(minutes=1)), "capture")
    assert check.level == "skip", check
    assert "does not belong to this file" in check.message, check
    assert "flush lag" not in check.message, check
    assert "within" not in check.message, check
    # Both instants and the distance between them: the quantities of the comparison it did make.
    assert "2026-09-29T12:00:00+00:00" in check.message, check
    assert "2026-08-01T12:00:00+00:00" in check.message, check
    assert "84960 min" in check.message, check


def test_a_mark_whose_uuid_resolves_still_belongs_to_the_file_it_predates(tmp_path: Path):
    """A stamp ahead of every exchange is not proof of a foreign file when the uuid is still there.

    That is a transcript rewritten *under* the mark, which the negative-lag `fail` below reports
    with its distance — so the foreign-file skip must not stand in front of it.
    """
    cfg = _healthy(tmp_path)
    _raw_mark(cfg, "capture", {"uuid": "u0", "timestamp": "2026-09-29T12:00:00.000Z", "count": 1})
    tr = _uuid_transcript(tmp_path / "t.jsonl", [
        ("u0", "2026-09-29T09:00:00.000Z"),
        ("u1", "2026-09-29T09:03:00.000Z"),
        ("u2", "2026-09-29T09:05:00.000Z"),
    ])
    check = _by_id(health.run(cfg, transcript_path=tr, now=_T0 + timedelta(hours=1)), "capture")
    assert check.level == "fail", check
    assert "rewritten under it" in check.message, check
    assert "175 min" in check.message, check
    assert "does not belong to this file" not in check.message, check


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


def test_session_notes_that_cannot_be_dated_at_all_skip_instead_of_comparing(tmp_path: Path):
    """Neither route to a date: no /sleep stamp in the title, and not stattable either (the file
    vanished between the listing and the stat). Nothing is left to compare the anchor against — and
    a skip no action can make measurable carries no fix."""
    cfg = _healthy(tmp_path)
    vault = _vault(tmp_path, {"session-memory": []})
    (vault / "session-memory" / "Notes.md").symlink_to(tmp_path / "gone.md")
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:00",
                            last_session="new", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "skip", check
    assert "could be dated" in check.message
    assert check.fix is None


def test_a_pulled_vault_does_not_look_a_session_behind(tmp_path: Path):
    """`git pull` and `git clone` rewrite every mtime to now — the documented team-mode path.

    Dating notes by mtime made this check warn that the entry points were a session behind on every
    second machine and after every vault pull. `lts.digest` says of this same vault that an mtime
    "would report false positives in a team — it is a fallback, never a preference", and the README
    ranks session notes by title because "the title is the date and, unlike an mtime, it survives a
    `git clone`". So the title decides, and the mtime only where there is no title stamp.
    """
    cfg = _healthy(tmp_path)
    vault = _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    note = vault / "session-memory" / "Session_2026-09-29_1200.md"
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:05",
                            last_session="new", active_topics=[], active_notes=[])
    pulled = datetime(2026, 9, 30, 9, 0, 0).timestamp()   # what the checkout leaves behind
    os.utime(note, (pulled, pulled))
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "ok", check
    assert "2026-09-29 12:05" in check.message, check


def test_a_note_whose_title_carries_no_stamp_is_still_dated_by_its_mtime(tmp_path: Path):
    """The fallback, and `_ANCHOR_SKEW` applies to it as much as to a title.

    Not everything under session-memory/ is a /sleep note, and one that carries no stamp must not
    silently drop out of the comparison — that would be the newest note going unmeasured.
    """
    cfg = _healthy(tmp_path)
    vault = _vault(tmp_path, {"session-memory": ["Notes about the session"]})
    note = vault / "session-memory" / "Notes about the session.md"
    written = datetime(2026, 9, 29, 12, 5, 0).timestamp()
    os.utime(note, (written, written))
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="2026-09-29 12:00",
                            last_session="old", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "warn", check
    assert "Notes about the session.md" in check.message, check


def test_an_anchor_with_an_unreadable_updated_warns(tmp_path: Path):
    cfg = _healthy(tmp_path)
    _vault(tmp_path, {"session-memory": ["Session_2026-09-29_1200"]})
    paths.ensure_sidecar(cfg)
    anchor_mod.write_anchor(paths.anchor_file(cfg), updated="whenever",
                            last_session="new", active_topics=[], active_notes=[])
    check = _by_id(health.run(cfg, now=_T0), "anchor_fresh")
    assert check.level == "warn", check
    assert "whenever" in check.message


def test_the_degenerate_states_in_this_table_never_report_ok(tmp_path: Path):
    """A table of memory states, and the check each one must refuse to pass.

    The name says a table, because that is what this is. It used to be called
    "test_no_check_reports_ok_for_a_measurement_it_could_not_have_made", which claims a guard over
    every check and every state; it is eleven — now nineteen — hand-written states, and a reviewer
    proved the difference by reintroducing two known defects and watching it stay green. Both were
    caught only by their own targeted tests. Two of those holes are closed here: dating a hook
    script by `exists()` rather than `is_file()` (a directory named stop.py bought "all 4 events
    wired, scripts present"), and walking for sidecars without `onerror` (a stray under a mode-000
    directory bought "no stray sidecars"). The test below that asserts every `ok` names the
    quantities it claims to have measured is the half of the job a table cannot do.

    Each instance of the class this was built for reported a check as passing while the comparison
    behind the claim had not run:

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

    def undateable_session_notes(root: Path):
        # No /sleep stamp in the title and not stattable either: no route to a date.
        cfg = _healthy(root)
        vault = _vault(root, {"session-memory": []})
        (vault / "session-memory" / "Notes.md").symlink_to(root / "gone.md")
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

    def an_empty_transcript_file(root: Path):
        # The historically dangerous one: `len(file) // 4` over an empty file is an ordinary 0, and
        # "0/1,000,000 tokens (0%)" was printed for a project whose window nobody had looked at.
        cfg = _healthy(root)
        (root / "t.jsonl").write_text("", encoding="utf-8")
        return cfg, {"transcript_path": root / "t.jsonl"}

    def a_transcript_with_no_usage_record(root: Path):
        cfg = _healthy(root)
        (root / "t.jsonl").write_text(
            json.dumps({"type": "assistant", "uuid": "u0",
                        "timestamp": "2026-09-29T11:00:00.000Z",
                        "message": {"role": "assistant",
                                    "content": [{"type": "text", "text": "hello"}]}}) + "\n",
            encoding="utf-8")
        return cfg, {"transcript_path": root / "t.jsonl"}

    def a_context_window_of_zero(root: Path):
        cfg = _healthy(root, "[sleep]\ncontext_window = 0\n")
        return cfg, {"transcript_path": _transcript(root / "t.jsonl",
                                                    ["2026-09-29T11:00:00.000Z"])}

    def a_directory_the_sidecar_walk_cannot_enter(root: Path):
        cfg = _healthy(root)
        locked = root / "locked"
        (locked / "inner" / ".ai_memory").mkdir(parents=True)
        os.chmod(locked, 0o000)
        return cfg, {}

    def no_vault_directory(root: Path):
        cfg = _cfg(root)
        _wire_hooks(root, scripts_at=root / "tools" / "hooks")
        return cfg, {}

    def an_empty_vault(root: Path):
        cfg = _cfg(root)
        _vault(root)
        _wire_hooks(root, scripts_at=root / "tools" / "hooks")
        return cfg, {}

    def a_directory_where_the_hook_script_belongs(root: Path):
        cfg = _healthy(root)
        script = root / "tools" / "hooks" / sync.HOOK_EVENTS["Stop"]
        script.unlink()
        script.mkdir()
        return cfg, {}

    def a_turn_state_whose_miss_count_is_not_a_count(root: Path):
        # `turnstate.read` guarantees a dict and nothing about what is in it: the file is best-effort
        # and may have been half-written or hand-edited. A miss count that is not a count is no
        # comparison, so the tolerance must not be applied to it — `"lots" < 2` raises, and
        # `misses or 0` would quietly report the passing state.
        return _healthy(root), {"turn": {"misses": "lots", "at": "2026-10-01T06:00:00.000Z"}}

    def settings_json_of_the_wrong_shape(root: Path):
        cfg = _healthy(root)
        (root / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"Stop": {"hooks": [{"command": "python3 stop.py"}]}}}),
            encoding="utf-8")
        return cfg, {}

    states = [
        ("a hook command with no script in it", a_hook_command_nothing_can_be_read_from, "hooks"),
        ("no mark file at all", no_mark_file, "marks"),
        ("a sleep mark file truncated mid-write", truncated_sleep_mark, "marks"),
        ("a sleep mark whose timestamp is None", sleep_mark_without_a_timestamp, "marks"),
        ("a capture mark file truncated mid-write", truncated_capture_mark, "capture"),
        ("a transcript whose entries carry no timestamps", an_unstamped_transcript, "capture"),
        ("session notes that cannot be dated", undateable_session_notes, "anchor_fresh"),
        ("an unparseable stamp whose uuid resolves", a_resolving_uuid_mark, "capture"),
        ("an unparseable stamp whose uuid is gone", a_uuid_mark_the_transcript_does_not_hold,
         "capture"),
        ("a journal record the trend cannot read", a_trend_record_the_window_cannot_read,
         "capture_progress"),
        ("a trend window with too few readable records",
         a_trend_window_with_too_few_readable_records, "anchor_delivery"),
        # `pressure`, `sidecars` and `vault` were not in this table at all until fix wave 1.
        ("an empty transcript file", an_empty_transcript_file, "pressure"),
        ("a transcript with no usage record", a_transcript_with_no_usage_record, "pressure"),
        ("a context window of zero", a_context_window_of_zero, "pressure"),
        ("a directory the sidecar walk cannot enter", a_directory_the_sidecar_walk_cannot_enter,
         "sidecars"),
        ("no vault directory", no_vault_directory, "vault"),
        ("an empty vault", an_empty_vault, "vault"),
        ("a directory where the hook script belongs", a_directory_where_the_hook_script_belongs,
         "hooks"),
        ("valid-JSON settings of the wrong shape", settings_json_of_the_wrong_shape, "hooks"),
        ("a turn state whose miss count is not a count", a_turn_state_whose_miss_count_is_not_a_count,
         "capture_live"),
    ]
    try:
        for i, (label, build, check_id) in enumerate(states):
            cfg, kwargs = build(tmp_path / f"state{i}")
            checks = health.run(cfg, now=_T0, **kwargs)
            check = _by_id(checks, check_id)
            assert check.level in {"skip", "warn", "fail"}, (label, check)
            for phrase in unearned:
                assert phrase not in check.message, (label, phrase, check)
            # No state here may be answered by `_answered`: a check that raised reports `fail`, so
            # it would satisfy the assertion above while measuring nothing at all.
            assert "this check itself raised" not in check.message, (label, check)
    finally:
        # A state that made a directory unreadable must not outlive the test: pytest's own tmp_path
        # cleanup cannot enter it either.
        for path in tmp_path.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)


def _fully_measurable(root: Path):
    """A project where all eleven checks can actually measure, plus the inputs the trends need."""
    cfg = _healthy(root)
    _vault(root, {"knowledge-base": ["Architecture"], "session-memory": ["Session_2026-09-29_1200"]})
    _anchor(cfg)
    _mark(cfg, "sleep", _T0 - timedelta(hours=1))
    _mark(cfg, "capture", _T0)
    tr = _transcript(root / "t.jsonl", [
        "2026-09-29T11:00:00.000Z", "2026-09-29T12:00:30.000Z", "2026-09-29T12:01:00.000Z",
    ])
    history = [{"blocks": ["anchor"], "capture_mark": f"M{i}", "stm_entries": 0} for i in range(5)]
    # `capture_live` needs a previous prompt to compare against; without one it skips, and the table
    # below asserts every check could measure. A mark that moved is the ordinary state, so 0 misses.
    turn = {"misses": 0, "at": "2026-09-29T12:02:00.000Z"}
    return cfg, {"transcript_path": tr, "history": history, "turn": turn}


def test_every_ok_names_the_quantities_it_claims_to_have_measured(tmp_path: Path):
    """The half of the regression net a table of degenerate states cannot supply.

    A blocklist of historical phrases only recognises a wording it has already met: reverting
    `script.is_file()` to `script.exists()` or dropping `onerror` from the sidecar walk left the
    table green, because both defects produce a perfectly ordinary `ok`. So the requirement is
    turned round here — an `ok` must carry the quantities of the comparison it claims, which a check
    that never made the comparison has nothing to fill in. Every figure below is read off a project
    where the check can measure, so what is pinned is real output and not a remembered sentence.
    """
    cfg, kwargs = _fully_measurable(tmp_path)
    checks = health.run(cfg, now=_T0 + timedelta(minutes=2), **kwargs)
    must_name = {
        # The project and where its config was found: this check asserts nothing else.
        "config": ["test-project", str(tmp_path)],
        # Not a quantity but a scope, and the same rule: the walk cannot see inside these.
        "sidecars": ["vendor", "node_modules"],
        # The number of events the message claims to have covered.
        "hooks": [str(len(sync.HOOK_EVENTS)) + " events wired"],
        # How many notes were counted, and where.
        "vault": ["2 notes", str(paths.vault_root(cfg))],
        # Both instants, because "consistent" is a claim about comparing them.
        "marks": ["2026-09-29T11:00:00+00:00", "2026-09-29T12:00:00+00:00"],
        # The backlog it counted and the tolerance it compared the lag against.
        "capture": ["2 exchanges", "10 min"],
        # The two numbers whose ratio is the percentage.
        "pressure": ["100", "1,000,000"],
        # The stamp the anchor carries, which is what was compared against the note's title.
        "anchor_fresh": ["2026-09-29 12:00"],
        # The window each trend read, and for the capture trend the signal it actually saw.
        "anchor_delivery": [f"last {health.TREND_WINDOW} sessions"],
        "capture_progress": ["capture mark moved",
                             f"last {health.TREND_WINDOW} sessions"],
        # The count it compared and the tolerance it compared it against — plus the stamp that dates
        # the evidence, because this check's input is as old as the last prompt and from `lts doctor`
        # that can be hours ago.
        "capture_live": ["0 consecutive", "tolerance of 2", "2026-09-29T12:02:00.000Z"],
    }
    assert set(must_name) == set(health._IDS)        # every check, not a subset of them
    for check in checks:
        assert check.level == "ok", check             # the premise: each one could measure
        for quantity in must_name[check.id]:
            assert quantity in check.message, (check, quantity)


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
    assert f"4 of {health.TREND_WINDOW} runs journalled" in check.message


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
    assert f"last {health.TREND_WINDOW} sessions" in check.message, check


def test_a_growing_buffer_passes_even_with_a_frozen_mark_field(tmp_path: Path):
    cfg = _healthy(tmp_path)
    history = [
        {"blocks": ["anchor"], "capture_mark": "SAME", "stm_entries": i} for i in range(5)
    ]
    check = _by_id(health.run(cfg, history=history, now=_T0), "capture_progress")
    assert check.level == "ok"
    assert "buffer grew" in check.message, check
    assert f"last {health.TREND_WINDOW} sessions" in check.message, check


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
        assert f"4 of the last {health.TREND_WINDOW}" in check.message, (field, check)
        assert "records could be read" in check.message, (field, check)
        # …and it does not describe itself as a measurement over the whole window.
        assert f"in the last {health.TREND_WINDOW} sessions" not in check.message, (field, check)


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
    assert f"4 of the last {health.TREND_WINDOW}" in check.message, check


def test_too_few_usable_records_skip_the_trend_naming_both_counts(tmp_path: Path):
    """Below two records there is no change to see, so the trend says so instead of judging."""
    cfg = _healthy(tmp_path)
    history = _runs(5)
    for record in history[:4]:
        record["capture_mark"] = {"timestamp": "x"}
    check = _by_id(health.run(cfg, history=history, now=_T0), "capture_progress")
    assert check.level == "skip", check
    assert f"1 of the last {health.TREND_WINDOW}" in check.message, check   # usable
    assert f"at least {healthchecks._TREND_MIN}" in check.message, check           # required
    assert "capture_mark" in check.message, check                            # what was unusable
    assert check.fix is None, check
    # Two usable records are enough to see a change, so the trend is measured rather than skipped.
    two = _runs(5, mark="FROZEN")
    for record in two[:3]:
        record["capture_mark"] = {"timestamp": "x"}
    measured = _by_id(health.run(cfg, history=two, now=_T0), "capture_progress")
    assert measured.level == "fail", measured
    assert f"2 of the last {health.TREND_WINDOW}" in measured.message, measured


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
        assert f"2 of {health.TREND_WINDOW} runs journalled" in _by_id(short, check_id).message
        assert f"0 of {health.TREND_WINDOW} runs journalled" in _by_id(empty, check_id).message


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
    history = _runs(5, blocks=(health.ANCHOR_BLOCK,))
    assert _by_id(health.run(cfg, history=history, now=_T0), "anchor_delivery").level == "ok"
    for name in ("health_demand", "sleep_demand", health.ANCHOR_BLOCK, "digest"):
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


def test_a_directory_the_walk_cannot_enter_is_not_reported_as_an_absence(tmp_path: Path):
    """"No stray sidecars" may reach no further than the walk behind it.

    `os.walk` swallowed every PermissionError, so a real stray under a mode-000 directory left this
    check `ok` and `lts doctor` exiting 0 — the absence claim this subsystem exists to catch.
    """
    cfg = _healthy(tmp_path)
    locked = tmp_path / "locked"
    (locked / "inner" / ".ai_memory").mkdir(parents=True)
    os.chmod(locked, 0o000)
    try:
        check = _by_id(health.run(cfg), "sidecars")
    finally:
        os.chmod(locked, 0o755)
    assert check.level == "skip", check
    assert str(locked) in check.message, check
    assert "could not be entered" in check.message, check
    assert check.message.startswith("no stray sidecars in the part of the tree"), check


def test_an_unreadable_directory_does_not_soften_a_stray_that_was_found(tmp_path: Path):
    cfg = _healthy(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    (tmp_path / "orphan" / ".ai_memory").mkdir(parents=True)
    os.chmod(locked, 0o000)
    try:
        check = _by_id(health.run(cfg), "sidecars")
    finally:
        os.chmod(locked, 0o755)
    assert check.level == "fail", check
    assert str(tmp_path / "orphan" / ".ai_memory") in check.message, check
    assert str(locked) in check.message, check


def test_the_clean_sidecar_message_names_the_directories_it_did_not_search(tmp_path: Path):
    """A vendored repository with its own `.ai_memory` is invisible to this walk by design.

    The pruning is deliberate, the unconditional "no stray sidecars" was not: it claimed the whole
    tree. The list is fixed, so the message can state exactly the scope it was given.
    """
    cfg = _healthy(tmp_path)
    (tmp_path / "vendor" / "somerepo" / ".ai_memory").mkdir(parents=True)
    check = _by_id(health.run(cfg), "sidecars")
    assert check.level == "ok", check
    for pruned in ("vendor", "node_modules", ".git", ".venv"):
        assert pruned in check.message, (pruned, check)


# --- capture_live ------------------------------------------------------------------------------
#
# The only detector of a dead `Stop` hook that works mid-session: `capture_progress` reads the health
# journal, which gains a record only at `SessionStart`, so between two compactions it answers `ok`
# from the same records while every exchange since the last one goes uncaptured.
#
# It asks two things, because either alone lies. The mark not moving is not enough — read
# 2026-10-01, `stop.py:65-75` leaves the mark alone for an unreadable transcript (the `if here:`
# guard, so a reset cannot replay the file) and writes the same value back for a turn that produced
# no exchange (`mark_of` names the newest *exchange*, and `is_scaffolding` drops a bare slash command
# and an isMeta record). Something being newer than the mark is not enough either: that is the
# ordinary state between a turn ending and the next `Stop` running.


def _unmoved(root: Path, *, misses: int, newest: str = "2026-09-29T12:30:00.000Z"):
    """A project where the capture mark has not moved and the transcript holds something newer.

    `_T0` is the mark, so the default `newest` is half an hour past it: the state the check is built
    to catch, and the only one in which it may fail.
    """
    cfg = _healthy(root)
    _mark(cfg, "capture", _T0)
    tr = _transcript(root / "t.jsonl", ["2026-09-29T11:00:00.000Z", newest])
    return cfg, {"transcript_path": tr,
                 "turn": {"misses": misses, "at": "2026-10-01T06:00:00.000Z"}}


def test_one_prompt_without_a_capture_is_not_a_failure(tmp_path: Path):
    """An interrupted turn runs no Stop hook at all, so one miss is not evidence of a dead hook.

    The same shape as `_CAPTURE_TOLERANCE`, whose comment records that one exchange behind is the
    measured normal. A check that failed on the first miss would fire every time the user pressed
    Esc, and a demand that cries wolf is the mechanism by which demands get ignored.
    """
    cfg, kwargs = _unmoved(tmp_path, misses=1)
    check = _by_id(health.run(cfg, now=_T0, **kwargs), "capture_live")
    assert check.level == "ok", check
    assert "1" in check.message and "2" in check.message, check


def test_two_prompts_without_a_capture_fail(tmp_path: Path):
    """Both halves true: the mark has not moved, and the transcript holds exchanges newer than it."""
    cfg, kwargs = _unmoved(tmp_path, misses=2)
    check = _by_id(health.run(cfg, now=_T0, **kwargs), "capture_live")
    assert check.level == "fail", check
    assert "2" in check.message and "2026-10-01T06:00:00.000Z" in check.message, check
    assert check.fix, "a failure the user can act on must say how"


def test_an_unmoved_mark_with_nothing_newer_than_it_is_not_a_dead_hook(tmp_path: Path):
    """The correction this check carries: states 1 and 2 of `stop.py:65-75`, excluded at the root.

    An unreadable transcript and a turn that produced no exchange both leave the mark exactly where
    it was, with a `Stop` hook that ran and was right to leave it. The mark is on the newest
    exchange here, so there was nothing to capture — and the miss count is deliberately far past the
    tolerance, because what must not fire is the *second* half of the question, not the first.
    """
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    tr = _transcript(tmp_path / "t.jsonl", ["2026-09-29T11:00:00.000Z", "2026-09-29T11:30:00.000Z"])
    check = _by_id(health.run(cfg, now=_T0, transcript_path=tr,
                              turn={"misses": 9, "at": "2026-10-01T06:00:00.000Z"}), "capture_live")
    assert check.level == "ok", check
    assert "nothing" in check.message.lower(), check
    assert "9" in check.message, check


def test_without_a_transcript_the_second_half_cannot_be_answered(tmp_path: Path):
    """`lts doctor` without `--transcript` can establish the mark has not moved and nothing else.

    A `fail` from the first half alone is the proxy this check was corrected away from, and an `ok`
    would be a pass invented for a comparison that never ran.
    """
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    check = _by_id(health.run(cfg, now=_T0, turn={"misses": 2, "at": "2026-10-01T06:00:00.000Z"}),
                   "capture_live")
    assert check.level == "skip", check
    assert "2" in check.message and "transcript" in check.message, check


def test_a_sleep_in_progress_is_not_an_exemption(tmp_path: Path):
    """The spec's sleep exemption is reinstated by nobody: it was justified by a false reading.

    `stop.py:56-59` — the armed branch — *writes* `watermark.mark_at(now)` to the capture mark before
    returning, so during a sleep the mark moves and the count resets by itself; there is nothing to
    exempt. And the flag is disarmed by `stop.py` itself, so the one state in which it stays armed
    across prompts is a `Stop` hook that is not running — exactly the failure this check exists to
    report. An exemption keyed on the flag would therefore be permanently blind in that state.
    """
    cfg, kwargs = _unmoved(tmp_path, misses=2)
    paths.sleep_flag_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    paths.sleep_flag_file(cfg).touch()
    assert watermark.is_armed(paths.sleep_flag_file(cfg))
    check = _by_id(health.run(cfg, now=_T0, **kwargs), "capture_live")
    assert check.level == "fail", check
    assert "sleep" not in check.message.lower(), check


def test_capture_live_skips_when_no_turn_state_exists(tmp_path: Path):
    cfg = _healthy(tmp_path)
    check = _by_id(health.run(cfg), "capture_live")
    assert check.level == "skip", check
    assert "turn state" in check.message.lower(), check


def test_the_per_turn_set_excludes_the_two_checks_that_walk_a_tree(tmp_path: Path):
    """Measured 2026-10-01 on three real projects: `sidecars` 3.391/130.884/605.770 ms and `vault`
    0.300/24.272/0.415 ms, against a ceiling of 1.121 ms for the whole set — including the two the
    spec's §3.1 table had no figure for, `anchor_delivery` 0.005/0.003/0.004 ms and `capture_live`
    0.235/0.212/0.263 ms on its expensive path. Every figure is in `PER_TURN_IDS`' comment with the
    method; the split is that measurement, not a taste.
    """
    assert "sidecars" not in health.PER_TURN_IDS
    assert "vault" not in health.PER_TURN_IDS
    assert "capture" not in health.PER_TURN_IDS, "needs a full transcript parse"
    assert "capture_live" in health.PER_TURN_IDS
    assert set(health.PER_TURN_IDS) <= set(health._IDS)


def test_selecting_a_subset_does_not_run_the_checks_left_out(tmp_path: Path):
    """The point of the subset is cost, so it must be skipped before it is called, not filtered after.

    A post-filter would still pay the 599.686 ms measured on nextcloud-development, on every turn.
    """
    cfg = _healthy(tmp_path)
    calls: list[str] = []
    original = healthchecks._check_sidecars
    healthchecks._check_sidecars = lambda c: calls.append("sidecars") or original(c)
    try:
        ids = [c.id for c in health.run(cfg, ids=health.PER_TURN_IDS)]
    finally:
        healthchecks._check_sidecars = original
    assert calls == [], "a check outside the subset was called anyway"
    assert "sidecars" not in ids
    assert ids == [i for i in health._IDS if i in health.PER_TURN_IDS]


def test_an_unconfigured_root_skips_only_what_was_asked_for(tmp_path: Path):
    """`run`'s own skip list is written here, so it has to honour `ids` as well.

    Without this the per-turn hook in a project nobody configured would report eleven checks after
    asking for eight — and a report naming checks the caller excluded is a report about a run that
    did not happen.
    """
    cfg = load_config(tmp_path / "nowhere")
    ids = [c.id for c in health.run(cfg, ids=health.PER_TURN_IDS)]
    assert ids == [i for i in health._IDS if i in health.PER_TURN_IDS]
    assert {c.level for c in health.run(cfg, ids=health.PER_TURN_IDS) if c.id != "config"} == {"skip"}


def test_a_capture_mark_with_no_orderable_stamp_skips_rather_than_demanding(tmp_path: Path):
    """The third state in which a working `Stop` hook leaves the mark looking unmoved.

    Found 2026-10-01 while reading `stop.py` for the two the plan names. `mark_of` writes
    `{"uuid", "timestamp", "count"}`, and `timestamp` is `None` for an exchange that carried no stamp
    — the state `test_a_positional_capture_mark_skips_the_marks_check_rather_than_demanding` pins for
    `marks`, and the state the degenerate table calls "a transcript whose entries carry no
    timestamps". Such a mark still advances by uuid, so `watermark.entries_after` resolves it exactly
    and `Stop` captures correctly; but the *timestamp* is what the turn state compares, so it reads as
    unmoved, and it is also what this check would compare the transcript against. Both halves of the
    question would then answer yes about a hook that is working.

    So a mark that exists and cannot be ordered is no evidence either way: `skip`, with the mark in
    the message. `marks` is the check whose subject that mark is, and it reports it. An absent or
    unreadable mark is **not** this state and must keep failing — `stop.py` rewrites a mark it cannot
    read (`entries_after` with an empty mark returns the whole file), so a mark still missing two
    prompts later means nothing is running; that is the assertion at the end.

    The degenerate-state table cannot catch this: it accepts `skip`, `warn` *or* `fail`, and the
    defect here produces a `fail`.
    """
    for label, payload in (("no stamp", {"uuid": "u0", "timestamp": None, "count": 1}),
                           ("unorderable stamp", {"uuid": "u0", "timestamp": "not-a-date",
                                                  "count": 1})):
        cfg = _healthy(tmp_path / label.replace(" ", "-"))
        _raw_mark(cfg, "capture", payload)
        tr = _transcript(cfg.project_root / "t.jsonl",
                         ["2026-09-29T11:00:00.000Z", "2026-09-29T12:30:00.000Z"])
        check = _by_id(health.run(cfg, now=_T0, transcript_path=tr,
                                  turn={"misses": 2, "at": "2026-10-01T06:00:00.000Z"}),
                       "capture_live")
        assert check.level == "skip", (label, check)
        assert "u0" in check.message, (label, check)
        assert "not capturing" not in check.message, (label, check)

    # And the two states that are not this one: no mark file at all, and one nothing can read.
    for label, write in (("absent", lambda c: None),
                         ("truncated", lambda c: _raw_mark(c, "capture", '{"timestamp": "2026-09-2'))):
        cfg = _healthy(tmp_path / f"still-fails-{label}")
        paths.ensure_sidecar(cfg)
        write(cfg)
        tr = _transcript(cfg.project_root / "t.jsonl",
                         ["2026-09-29T11:00:00.000Z", "2026-09-29T12:30:00.000Z"])
        check = _by_id(health.run(cfg, now=_T0, transcript_path=tr,
                                  turn={"misses": 2, "at": "2026-10-01T06:00:00.000Z"}),
                       "capture_live")
        assert check.level == "fail", (label, check)
