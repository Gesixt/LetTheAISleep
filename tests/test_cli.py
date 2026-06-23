import json
import os
import subprocess
import sys
from pathlib import Path
from lts.cli import main


def test_status_text(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    main(["stm", "append", "--root", str(tmp_path), "--session", "s", "--text", "a fact"])
    capsys.readouterr()
    main(["status", "--root", str(tmp_path), "--session", "s"])
    out = capsys.readouterr().out
    assert "STM buffer" in out and "Sleep debt" in out and "Anchor" in out


def test_status_json(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    main(["status", "--root", str(tmp_path), "--session", "s", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["session"] == "s"
    assert set(data) >= {"stm", "pending", "pressure", "anchor"}
    assert data["pressure"] is None


def test_python_m_lts_runs_as_module(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo)}
    subprocess.run(
        [sys.executable, "-m", "lts", "stm", "append",
         "--root", str(tmp_path), "--session", "s", "--text", "module fact"],
        check=True, env=env, capture_output=True, text=True,
    )
    out = subprocess.run(
        [sys.executable, "-m", "lts", "stm", "read",
         "--root", str(tmp_path), "--session", "s"],
        check=True, env=env, capture_output=True, text=True,
    ).stdout
    assert "module fact" in out


def test_stm_append_read_clear(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    assert main(["stm", "append", "--root", str(tmp_path), "--session", "s", "--text", "fact one"]) == 0
    main(["stm", "read", "--root", str(tmp_path), "--session", "s"])
    assert "fact one" in capsys.readouterr().out
    main(["stm", "clear", "--root", str(tmp_path), "--session", "s"])
    main(["stm", "read", "--root", str(tmp_path), "--session", "s"])
    assert capsys.readouterr().out.strip() == ""


def test_pressure(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    t = tmp_path / "t.jsonl"
    t.write_text("x" * (4 * 170_000), encoding="utf-8")  # ~170k tokens of 200k
    main(["pressure", "--root", str(tmp_path), "--transcript", str(t)])
    assert capsys.readouterr().out.strip() == "force"


def test_pending_has(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "no"
    main(["pending", "dump", "--root", str(tmp_path), "--session", "s", "--text", "raw"])
    capsys.readouterr()  # clear the pending dump output
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "yes"


def test_pending_dump_prints_path(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    main(["pending", "dump", "--root", str(tmp_path), "--session", "s", "--text", "raw"])
    out = capsys.readouterr().out.strip()
    assert out and Path(out).exists()
