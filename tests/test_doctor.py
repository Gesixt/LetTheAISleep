import os
from pathlib import Path

from lts import doctor
from tests.helpers import make_project


def test_find_sidecars_reports_every_nested_one(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / ".ai_memory").mkdir()
    (tmp_path / "a" / ".ai_memory").mkdir(parents=True)
    (tmp_path / "b" / "c" / ".ai_memory").mkdir(parents=True)
    found, unreadable = doctor.find_sidecars(tmp_path)
    assert [p.parent.name for p in found] == ["a", "c"]
    assert unreadable == []


def test_find_sidecars_ignores_the_roots_own(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / ".ai_memory").mkdir()
    assert doctor.find_sidecars(tmp_path) == ([], [])


def test_find_sidecars_skips_vendor_directories(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / "node_modules" / "pkg" / ".ai_memory").mkdir(parents=True)
    (tmp_path / ".git" / ".ai_memory").mkdir(parents=True)
    assert doctor.find_sidecars(tmp_path) == ([], [])


def test_a_directory_the_walk_cannot_enter_is_returned_not_swallowed(tmp_path: Path):
    """`os.walk` without `onerror` drops every PermissionError and yields nothing for that subtree.

    The sidecar under it then disappeared and the caller reported "no stray sidecars" — an absence
    claim about a tree this function had never seen, with `lts doctor` exiting 0 on it.
    """
    make_project(tmp_path)
    locked = tmp_path / "locked"
    (locked / "inner" / ".ai_memory").mkdir(parents=True)
    (tmp_path / "open" / ".ai_memory").mkdir(parents=True)
    os.chmod(locked, 0o000)
    try:
        found, unreadable = doctor.find_sidecars(tmp_path)
    finally:
        os.chmod(locked, 0o755)
    assert unreadable == [locked]
    # What could be read is still read: an unreadable subtree costs itself and not the walk.
    assert found == [tmp_path / "open" / ".ai_memory"]
