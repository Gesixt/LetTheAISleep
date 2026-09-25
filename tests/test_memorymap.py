from pathlib import Path

from lts import memorymap
from lts.config import load_config
from tests.helpers import make_project


def _vault(root: Path, notes: dict[str, list[str]]) -> Path:
    """Build a vault: {folder: [note titles]}. "" means a note at the vault root."""
    vault = root / ".ai_vault"
    for folder, titles in notes.items():
        d = vault / folder if folder else vault
        d.mkdir(parents=True, exist_ok=True)
        for t in titles:
            (d / f"{t}.md").write_text(f"# {t}\n", encoding="utf-8")
    return vault


def _cfg(root: Path, extra: str = ""):
    make_project(root, extra)
    return load_config(root)


def test_knowledge_base_comes_first_and_sessions_last(tmp_path: Path):
    """Permanent topical notes carry the most meaning per title, so they lead the map.

    Session notes are episodic — worth listing, but the last thing to spend budget on.
    """
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {
        "session-memory": ["Session_2026-07-11_0028"],
        "knowledge-base": ["Architecture"],
        "designs": ["Cart Service"],
    })
    lines = memorymap.render(cfg).splitlines()
    groups = [ln.split(":")[0] for ln in lines if ":" in ln and ln[0].isalpha()]
    assert groups == ["knowledge-base", "designs", "session-memory"]


def test_newest_session_first(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"session-memory": [
        "Session_2026-07-11_0028", "Session_2026-08-26_1356", "Session_2026-07-10_0853",
    ]})
    line = next(ln for ln in memorymap.render(cfg).splitlines() if ln.startswith("session-memory"))
    assert line.index("2026-08-26") < line.index("2026-07-11") < line.index("2026-07-10")


def test_counts_every_note_in_the_header(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["A", "B"], "session-memory": ["Session_2026-01-01_0000"]})
    assert "(3 notes)" in memorymap.render(cfg)


def test_budget_truncates_and_says_how_many_are_missing(tmp_path: Path):
    # 500 titles would be ~10k tokens on every single turn; the map must degrade, not balloon.
    cfg = _cfg(tmp_path, "\n[memory]\nmap_budget = 200\n")
    _vault(tmp_path, {"knowledge-base": [f"Note number {i:03d}" for i in range(40)]})
    out = memorymap.render(cfg)
    assert len(out) <= 200
    assert "more" in out
    assert "Note number 000" in out          # the budget is spent from the top
    assert "Note number 039" not in out


def test_silent_when_there_is_nothing_to_say(tmp_path: Path):
    no_vault = _cfg(tmp_path / "a")
    assert memorymap.render(no_vault) == ""

    empty = _cfg(tmp_path / "b")
    (tmp_path / "b" / ".ai_vault").mkdir()
    assert memorymap.render(empty) == ""

    disabled = _cfg(tmp_path / "c", "\n[memory]\nmap_budget = 0\n")
    _vault(tmp_path / "c", {"knowledge-base": ["Architecture"]})
    assert memorymap.render(disabled) == ""

    unconfigured = load_config(tmp_path / "d")
    assert memorymap.render(unconfigured) == ""


def test_team_mode_author_folders_collapse_into_one_group(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {
        "session-memory/dmitrii": ["Session_dmitrii_2026-07-10_1002"],
        "session-memory/petr": ["Session_petr_2026-07-09_1730"],
    })
    lines = [ln for ln in memorymap.render(cfg).splitlines() if ln.startswith("session-memory")]
    assert len(lines) == 1
    assert "dmitrii" in lines[0] and "petr" in lines[0]
