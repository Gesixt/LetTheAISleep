import json
import os
import subprocess
import sys
from pathlib import Path
from lts.cli import main
from tests.helpers import make_project


def test_status_text(tmp_path: Path, capsys):
    make_project(tmp_path)
    main(["stm", "append", "--root", str(tmp_path), "--text", "a fact"])
    capsys.readouterr()
    main(["status", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "STM buffer" in out and "Sleep debt" in out and "Anchor" in out


def test_status_json(tmp_path: Path, capsys):
    make_project(tmp_path)
    main(["status", "--root", str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert set(data) >= {"project", "project_slug", "stm", "pending", "pressure", "anchor"}
    assert data["pressure"] is None


def test_python_m_lts_runs_as_module(tmp_path: Path):
    make_project(tmp_path)
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo)}
    subprocess.run(
        [sys.executable, "-m", "lts", "stm", "append",
         "--root", str(tmp_path), "--text", "module fact"],
        check=True, env=env, capture_output=True, text=True,
    )
    out = subprocess.run(
        [sys.executable, "-m", "lts", "stm", "read", "--root", str(tmp_path)],
        check=True, env=env, capture_output=True, text=True,
    ).stdout
    assert "module fact" in out


def test_stm_append_read_clear(tmp_path: Path, capsys):
    make_project(tmp_path)
    assert main(["stm", "append", "--root", str(tmp_path), "--text", "fact one"]) == 0
    main(["stm", "read", "--root", str(tmp_path)])
    assert "fact one" in capsys.readouterr().out
    main(["stm", "clear", "--root", str(tmp_path)])
    main(["stm", "read", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == ""


def test_pressure_falls_back_to_the_size_estimate_without_usage_counters(tmp_path: Path, capsys):
    """No `usage` anywhere in the transcript is the one case where size is all we have.

    Even then it is measured against the *configured* window. This test used to assert `force`
    for a 680 KB file, which was the hardcoded 200k window talking, not the project's.
    """
    make_project(tmp_path, "[sleep]\ncontext_window = 200000\n")
    t = tmp_path / "t.jsonl"
    t.write_text("x" * (4 * 170_000), encoding="utf-8")  # ~170k tokens
    main(["pressure", "--root", str(tmp_path), "--transcript", str(t)])
    assert capsys.readouterr().out.strip() == "force"        # 170k / 200k

    wide = tmp_path / "wide"
    make_project(wide, "[sleep]\ncontext_window = 1000000\n")
    main(["pressure", "--root", str(wide), "--transcript", str(t)])
    assert capsys.readouterr().out.strip() == "none"         # the same file, 170k / 1M


def test_pending_has(tmp_path: Path, capsys):
    make_project(tmp_path)
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "no"
    main(["pending", "dump", "--root", str(tmp_path), "--session", "s", "--text", "raw"])
    capsys.readouterr()  # clear the pending dump output
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "yes"


def test_pending_dump_prints_path(tmp_path: Path, capsys):
    make_project(tmp_path)
    main(["pending", "dump", "--root", str(tmp_path), "--session", "s", "--text", "raw"])
    out = capsys.readouterr().out.strip()
    assert out and Path(out).exists()


def test_pending_list_and_clear(tmp_path: Path, capsys):
    make_project(tmp_path)
    main(["pending", "dump", "--root", str(tmp_path), "--session", "s", "--text", "raw"])
    capsys.readouterr()
    main(["pending", "list", "--root", str(tmp_path)])
    listed = capsys.readouterr().out.strip().splitlines()
    # absolute paths, so a skill never has to build one relative to its shell's cwd
    assert len(listed) == 1 and Path(listed[0]).is_absolute() and Path(listed[0]).exists()
    main(["pending", "clear", "--root", str(tmp_path)])
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "no"


def test_anchor_write_then_render(tmp_path: Path, capsys):
    make_project(tmp_path)
    main([
        "anchor", "write", "--root", str(tmp_path),
        "--last-session", "Session_2026-07-10_0853",
        "--topic", "slug bug", "--topic", "project root",
        "--note", "knowledge-base/Project Root Resolution",
        "--next-task", "ship the fix",
    ])
    capsys.readouterr()
    main(["anchor", "render", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "Session_2026-07-10_0853" in out
    assert "knowledge-base/Project Root Resolution" in out
    data = json.loads((tmp_path / ".ai_memory" / "anchor.json").read_text(encoding="utf-8"))
    assert data["active_topics"] == ["slug bug", "project root"]
    assert data["next_task"] == "ship the fix"


def test_writes_refuse_outside_a_project(tmp_path: Path, capsys):
    stray = tmp_path / "arm-scripts"
    stray.mkdir()
    assert main(["stm", "append", "--root", str(stray), "--text", "orphan"]) == 2
    err = capsys.readouterr().err
    assert "no lts project" in err
    assert not (stray / ".ai_memory").exists()   # the whole point: nothing was created


def test_reads_outside_a_project_are_harmless(tmp_path: Path, capsys):
    stray = tmp_path / "arm-scripts"
    stray.mkdir()
    assert main(["stm", "read", "--root", str(stray)]) == 0
    assert capsys.readouterr().out == ""
    assert not (stray / ".ai_memory").exists()


def test_doctor_reports_health_and_exits_zero_when_sound(tmp_path: Path, capsys):
    from tests.test_health import _healthy
    _healthy(tmp_path)
    code = main(["doctor", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Memory health" in out
    assert "hooks:" in out


def test_doctor_exits_one_when_a_check_fails(tmp_path: Path, capsys):
    code = main(["doctor", "--root", str(tmp_path / "nowhere")])
    assert code == 1
    assert "config:" in capsys.readouterr().out


def test_doctor_json_lists_every_check(tmp_path: Path, capsys):
    from lts import health
    from tests.test_health import _healthy
    _healthy(tmp_path)
    main(["doctor", "--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in payload["checks"]] == list(health._IDS)
    assert payload["worst"] in ("ok", "warn", "skip", "fail")


def test_doctor_transcript_enables_the_transcript_bound_checks(tmp_path: Path, capsys):
    from tests.test_health import _healthy, _transcript
    _healthy(tmp_path)
    tr = _transcript(tmp_path / "t.jsonl", ["2026-09-29T12:00:00.000Z"])
    main(["doctor", "--root", str(tmp_path), "--transcript", str(tr), "--json"])
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["pressure"]["level"] != "skip"


def test_session_name_single_developer(tmp_path: Path, capsys):
    make_project(tmp_path)
    assert main(["session-name", "--root", str(tmp_path), "--at", "2026-07-10 10:02"]) == 0
    assert capsys.readouterr().out.strip() == "Session_2026-07-10_1002"


def test_session_name_team_mode_json(tmp_path: Path, capsys):
    make_project(tmp_path, 'author = "dmitrii"\n')
    assert main([
        "session-name", "--root", str(tmp_path), "--at", "2026-07-10 10:02", "--json"
    ]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "title": "Session_dmitrii_2026-07-10_1002",
        "directory": "session-memory/dmitrii",
    }


def test_digest_is_silent_without_a_vault(tmp_path: Path, capsys):
    make_project(tmp_path)
    assert main(["digest", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_digest_json_reports_unavailability(tmp_path: Path, capsys):
    make_project(tmp_path)
    assert main(["digest", "--root", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["available"] is False
    assert data["empty"] is True


def test_status_json_carries_author_and_vault_path(tmp_path: Path, capsys):
    make_project(tmp_path, 'author = "dmitrii"\n')
    main(["status", "--root", str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["author"] == "dmitrii"
    assert data["vault_path"].endswith(".ai_vault")


def test_stm_clear_arms_the_sleep_flag(tmp_path: Path):
    # Step 6 of /sleep is `lts stm clear`; arming there is what tells the hooks that
    # everything up to this point is consolidated, so they stop re-reporting it.
    from lts import paths, watermark
    from lts.config import load_config
    make_project(tmp_path)
    main(["stm", "append", "--root", str(tmp_path), "--text", "a fact"])
    main(["stm", "clear", "--root", str(tmp_path)])
    cfg = load_config(tmp_path)
    assert watermark.is_armed(paths.sleep_flag_file(cfg))


def _sync_fixture(tmp_path: Path):
    """A source clone and a project installed against an older one."""
    from lts import sync
    source = tmp_path / "source"
    (source / "claude" / "hooks").mkdir(parents=True)
    for script in sync.HOOK_EVENTS.values():
        (source / "claude" / "hooks" / script).write_text("# hook\n", encoding="utf-8")
    (source / "claude" / "skills" / "sleep").mkdir(parents=True)
    (source / "claude" / "skills" / "sleep" / "SKILL.md").write_text("new\n", encoding="utf-8")

    target = tmp_path / "target"
    make_project(target)
    (target / ".claude" / "skills" / "sleep").mkdir(parents=True)
    (target / ".claude" / "skills" / "sleep" / "SKILL.md").write_text("old\n", encoding="utf-8")
    return source, target


def test_update_refreshes_the_target_and_says_what_changed(tmp_path: Path, capsys):
    source, target = _sync_fixture(tmp_path)
    assert main(["update", "--target", str(target), "--source", str(source)]) == 0
    out = capsys.readouterr().out
    assert "skills/sleep" in out
    assert (target / ".claude" / "skills" / "sleep" / "SKILL.md").read_text(
        encoding="utf-8") == "new\n"


def test_update_check_exits_nonzero_while_stale_and_zero_once_synced(tmp_path: Path, capsys):
    source, target = _sync_fixture(tmp_path)
    assert main(["update", "--target", str(target), "--source", str(source), "--check"]) == 1
    assert (target / ".claude" / "skills" / "sleep" / "SKILL.md").read_text(
        encoding="utf-8") == "old\n"
    main(["update", "--target", str(target), "--source", str(source)])
    capsys.readouterr()
    assert main(["update", "--target", str(target), "--source", str(source), "--check"]) == 0


def test_update_refuses_a_directory_that_has_no_memory_installed(tmp_path: Path, capsys):
    source, _ = _sync_fixture(tmp_path)
    stray = tmp_path / "stray"
    stray.mkdir()
    assert main(["update", "--target", str(stray), "--source", str(source)]) == 2
    assert "no lts project" in capsys.readouterr().err
    assert not (stray / ".claude").exists()


def test_memory_map_prints_the_map(tmp_path: Path, capsys):
    # The hook injects this on every turn; a command makes it inspectable without one.
    make_project(tmp_path)
    kb = tmp_path / ".ai_vault" / "knowledge-base"
    kb.mkdir(parents=True)
    (kb / "Cart Service.md").write_text("# Cart Service\n", encoding="utf-8")
    assert main(["memory-map", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Memory map" in out and "Cart Service" in out


def test_pressure_measures_live_context_not_the_size_of_the_file(tmp_path: Path, capsys):
    """`lts pressure` measured `len(transcript_file) // 4` against a hardcoded 200,000 window.

    A transcript is append-only across `--resume`, so its size is the whole history of the
    project, not the live window: on the real ppss transcript this path estimated 45,413,420
    tokens — 22,707% of that window — while the live session held 301,343 of its configured
    1,000,000. (The percentage recorded beside that count, "9900%", follows from no window;
    `healthchecks._check_pressure` carries the account.)
    """
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "usage": {
            "input_tokens": 900, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
    }), encoding="utf-8")
    main(["pressure", "--root", str(tmp_path), "--transcript", str(t)])
    assert capsys.readouterr().out.strip() == "force"   # 900 / 1000, not 231 / 200000


def test_doctor_refuses_a_transcript_path_it_cannot_read(tmp_path: Path, capsys):
    """A typo in `--transcript` used to buy a clean bill of health.

    `transcript.context_tokens` returns 0 for a file that is not there, `status.collect` wraps
    that zero in a dict the `pressure` check accepts as a measurement, and the run ended at
    "pressure: 0/1,000,000 tokens (0%)", exit 0 — a green report from a measurement that never
    happened.
    """
    from tests.test_health import _healthy
    _healthy(tmp_path)
    code = main(["doctor", "--root", str(tmp_path), "--transcript", "/nonexistent/path.jsonl"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "transcript not found: '/nonexistent/path.jsonl'" in captured.err
    assert "pressure" not in captured.out


def test_doctor_refuses_a_transcript_that_is_not_a_file(tmp_path: Path, capsys):
    from tests.test_health import _healthy
    _healthy(tmp_path)
    directory = tmp_path / "not-a-transcript"
    directory.mkdir()
    code = main(["doctor", "--root", str(tmp_path), "--transcript", str(directory)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert f"transcript is not a file: {str(directory)!r}" in captured.err


def test_doctor_json_carries_the_message_and_fix_of_every_check(tmp_path: Path, capsys):
    """The JSON is what a caller other than a human reads; a level alone is not actionable."""
    main(["doctor", "--root", str(tmp_path / "nowhere"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    by_id = {c["id"]: c for c in payload["checks"]}
    assert all(isinstance(c["message"], str) and c["message"] for c in payload["checks"])
    assert all("fix" in c for c in payload["checks"])
    assert by_id["config"]["level"] == "fail"
    assert "install.py" in by_id["config"]["fix"]
    # A skip that no action would make measurable carries no fix, and the JSON keeps that null.
    assert by_id["sidecars"]["fix"] is None


def test_doctor_exits_zero_when_the_worst_check_is_a_warning(tmp_path: Path, capsys):
    """Exit 1 is reserved for `fail`: a warning must not stop a caller that checks the code."""
    from tests.test_health import _cfg, _vault, _wire_hooks
    _cfg(tmp_path)
    _vault(tmp_path)  # a vault directory with no notes: `vault` warns, nothing fails
    _wire_hooks(tmp_path, scripts_at=tmp_path / "tools" / "hooks")
    code = main(["doctor", "--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["worst"] == "warn"
    assert code == 0


def test_doctor_transcript_reaches_the_capture_check(tmp_path: Path, capsys):
    """`capture` skips without a transcript, so only a state that gives it one discriminates.

    It also needs a capture mark: without one `capture` skips whatever the flag says, which is
    why the `_healthy` fixture alone could not tell the two paths apart.
    """
    from tests.test_health import _T0, _healthy, _mark, _transcript
    cfg = _healthy(tmp_path)
    _mark(cfg, "capture", _T0)
    tr = _transcript(tmp_path / "t.jsonl", ["2026-09-29T12:30:00.000Z"])

    main(["doctor", "--root", str(tmp_path), "--json"])
    without = {c["id"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert without["capture"]["level"] == "skip"

    main(["doctor", "--root", str(tmp_path), "--transcript", str(tr), "--json"])
    with_it = {c["id"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert with_it["capture"]["level"] == "ok"
    assert "behind the capture mark" in with_it["capture"]["message"]


def test_pressure_refuses_a_transcript_path_it_cannot_read(tmp_path: Path, capsys):
    """A mistyped path used to come back as a confident `none`.

    `transcript.context_tokens` returns 0 for a file that is not there, and 0 tokens is below
    every threshold — the caller was told there is no pressure by a read that never happened.
    Both this command and `status` take the path from a human at a terminal; no skill or hook
    shells out to `lts pressure` (the only consumer of the level is
    `claude/hooks/user_prompt_submit.py`, which calls `transcript.pressure_level` in-process).
    """
    make_project(tmp_path)
    code = main(["pressure", "--root", str(tmp_path), "--transcript", "/nonexistent/path.jsonl"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "transcript not found: '/nonexistent/path.jsonl'" in captured.err


def test_status_refuses_a_transcript_path_it_cannot_read(tmp_path: Path, capsys):
    make_project(tmp_path)
    code = main(["status", "--root", str(tmp_path), "--transcript", "/nonexistent/path.jsonl"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "transcript not found: '/nonexistent/path.jsonl'" in captured.err


def test_an_omitted_transcript_is_not_a_bad_one(tmp_path: Path, capsys):
    """`status` and `doctor` take the flag optionally; omitting it asks a narrower question, and
    both already say which checks went unmeasured. Only a path that was given and cannot be read
    is a refusal."""
    from tests.test_health import _healthy
    _healthy(tmp_path)
    assert main(["status", "--root", str(tmp_path)]) == 0
    assert "STM buffer" in capsys.readouterr().out
    assert main(["doctor", "--root", str(tmp_path)]) == 0
    assert "no transcript given" in capsys.readouterr().out


def test_a_usable_transcript_still_reaches_pressure_and_status(tmp_path: Path, capsys):
    """The guard must not change what either command prints when the path is readable."""
    make_project(tmp_path, "[sleep]\ncontext_window = 1000\n")
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({
        "type": "assistant", "uuid": "u0", "timestamp": "2026-09-29T12:00:00.000Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
                    "usage": {"input_tokens": 900}},
    }) + "\n", encoding="utf-8")
    assert main(["pressure", "--root", str(tmp_path), "--transcript", str(t)]) == 0
    assert capsys.readouterr().out.strip() == "force"
    assert main(["status", "--root", str(tmp_path), "--transcript", str(t), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pressure"]["tokens"] == 900


def test_the_json_shape_needs_no_encoder_because_every_field_is_a_string(tmp_path: Path, capsys):
    """`json.dumps(..., default=str)` was a leftover of the old report shape, which held `Path`
    objects. `Check`'s four fields are `str | None` by construction, so the encoder guarded
    nothing — and with it gone a future field that is not a string raises here instead of being
    quietly stringified. This pins the shape that makes the removal safe."""
    from tests.test_health import _healthy
    _healthy(tmp_path)
    main(["doctor", "--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    for check in payload["checks"]:
        assert isinstance(check["id"], str)
        assert isinstance(check["level"], str)
        assert isinstance(check["message"], str)
        assert check["fix"] is None or isinstance(check["fix"], str)


def test_an_empty_transcript_value_is_a_bad_path_not_an_absent_one(tmp_path: Path, capsys):
    """An unset shell variable expands to "", which is the case the guard exists for.

    It used to slip through as though the flag had been omitted: `pressure --transcript ""`
    raised an uncaught `IsADirectoryError` (`Path("")` is `.`) and `status --transcript ""`
    printed a full report with no context line at all, both without saying anything was wrong.
    """
    from tests.test_health import _healthy
    _healthy(tmp_path)
    for cmd in ("pressure", "status", "doctor"):
        for value in ("", "   "):
            code = main([cmd, "--root", str(tmp_path), "--transcript", value])
            captured = capsys.readouterr()
            assert code == 2, (cmd, value)
            assert captured.out == "", (cmd, value)
            # The value is quoted, so whitespace is legible rather than an empty tail.
            assert repr(value) in captured.err, (cmd, value)


def test_doctor_reads_the_journal_so_the_trend_checks_can_answer(tmp_path: Path, capsys):
    """`doctor` passed no history, so both trend checks skipped however full the journal was.

    They are the two checks that cannot be answered from the filesystem at all, so skipping them
    in the one command whose job is to report health left them answerable only inside the hook.
    """
    from lts import journal, paths
    from lts.config import load_config
    make_project(tmp_path)
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    for i in range(5):
        journal.append(paths.health_journal_file(cfg), {
            "at": f"2026-09-29T14:0{i}:00.000Z",
            "event": "session_start",
            "blocks": ["anchor", "digest"],
            "checks": {},
            "stm_entries": i,
            "capture_mark": f"2026-09-29T14:0{i}:00.000Z",
        })
    main(["doctor", "--root", str(tmp_path), "--json"])
    levels = {c["id"]: c["level"] for c in json.loads(capsys.readouterr().out)["checks"]}
    assert levels["anchor_delivery"] == "ok"
    assert levels["capture_progress"] == "ok"
