from pathlib import Path

from lts import doctor
from tests.helpers import make_project


def test_find_sidecars_reports_every_nested_one(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / ".ai_memory").mkdir()
    (tmp_path / "a" / ".ai_memory").mkdir(parents=True)
    (tmp_path / "b" / "c" / ".ai_memory").mkdir(parents=True)
    found = doctor.find_sidecars(tmp_path)
    assert [p.parent.name for p in found] == ["a", "c"]


def test_find_sidecars_ignores_the_roots_own(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / ".ai_memory").mkdir()
    assert doctor.find_sidecars(tmp_path) == []


def test_find_sidecars_skips_vendor_directories(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / "node_modules" / "pkg" / ".ai_memory").mkdir(parents=True)
    (tmp_path / ".git" / ".ai_memory").mkdir(parents=True)
    assert doctor.find_sidecars(tmp_path) == []
