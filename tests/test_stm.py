from pathlib import Path
from lts import stm


def test_append_then_read(tmp_path: Path):
    f = tmp_path / "stm" / "s.md"
    stm.append(f, "decision: use PostgreSQL")
    stm.append(f, "number: 50% budget")
    assert stm.read(f) == "decision: use PostgreSQL\nnumber: 50% budget\n"


def test_read_missing_returns_empty(tmp_path: Path):
    assert stm.read(tmp_path / "nope.md") == ""


def test_is_empty(tmp_path: Path):
    f = tmp_path / "s.md"
    assert stm.is_empty(f) is True
    stm.append(f, "x")
    assert stm.is_empty(f) is False


def test_clear_is_idempotent(tmp_path: Path):
    f = tmp_path / "s.md"
    stm.append(f, "x")
    stm.clear(f)
    assert stm.is_empty(f) is True
    stm.clear(f)  # no error on second clear
