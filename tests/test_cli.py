from pathlib import Path
from lts.cli import main


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
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "yes"
