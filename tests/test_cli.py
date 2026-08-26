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


def test_pressure(tmp_path: Path, capsys):
    make_project(tmp_path)
    t = tmp_path / "t.jsonl"
    t.write_text("x" * (4 * 170_000), encoding="utf-8")  # ~170k tokens of 200k
    main(["pressure", "--root", str(tmp_path), "--transcript", str(t)])
    assert capsys.readouterr().out.strip() == "force"


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


def test_doctor_flags_unconfigured_root(tmp_path: Path, capsys):
    stray = tmp_path / "arm-scripts"
    stray.mkdir()
    assert main(["doctor", "--root", str(stray)]) == 1
    assert "no config.toml" in capsys.readouterr().out


def test_doctor_json_on_healthy_project(tmp_path: Path, capsys):
    make_project(tmp_path)
    assert main(["doctor", "--root", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["configured"] is True


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
