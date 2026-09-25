import os
from pathlib import Path

from lts import anchor, memorymap
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
    vault = _vault(tmp_path, {"knowledge-base": [f"Note number {i:03d}" for i in range(40)]})
    # One mtime for all of them: what a `git clone` leaves behind. Recency then says nothing and
    # the alphabet is the whole ranking, so the map must still be deterministic.
    for note in (vault / "knowledge-base").iterdir():
        os.utime(note, (1_700_000_000, 1_700_000_000))
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


def _anchor(root: Path, notes: list[str]) -> None:
    anchor.write_anchor(
        root / ".ai_memory" / "anchor.json",
        updated="2026-09-25 15:38",
        last_session=notes[0] if notes else "",
        active_topics=["ranking"],
        active_notes=notes,
    )


def test_the_anchors_notes_lead_the_map(tmp_path: Path):
    """The anchor is the one ranking signal we have: the last sleep chose these notes itself.

    They are hoisted above the group tiers because the entry points cross them — the last
    session note lives in `session-memory`, which is the first group truncation drops.
    """
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {
        "knowledge-base": ["Architecture", "Memory Map"],
        "session-memory": ["Session_2026-09-25_1534"],
    })
    _anchor(tmp_path, ["session-memory/Session_2026-09-25_1534", "knowledge-base/Memory Map"])
    lines = memorymap.render(cfg).splitlines()
    assert lines[1] == "active: session-memory/Session_2026-09-25_1534 · knowledge-base/Memory Map"
    assert lines[2].startswith("knowledge-base:")


def test_an_anchored_note_is_not_listed_twice(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["Architecture", "Memory Map"]})
    _anchor(tmp_path, ["knowledge-base/Memory Map"])
    out = memorymap.render(cfg)
    assert out.count("Memory Map") == 1
    assert out.splitlines()[2] == "knowledge-base: Architecture"


def test_a_stale_anchor_entry_is_dropped(tmp_path: Path):
    """An anchor outlives the notes it points at. Advertising a deleted note costs a wasted
    `read_note` and teaches the model the map lies."""
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["Architecture"]})
    _anchor(tmp_path, ["knowledge-base/Deleted Note", "knowledge-base/Architecture"])
    out = memorymap.render(cfg)
    assert "Deleted Note" not in out
    assert out.splitlines()[1] == "active: knowledge-base/Architecture"


def test_no_anchor_means_no_active_line(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _vault(tmp_path, {"knowledge-base": ["Architecture"]})
    assert "active:" not in memorymap.render(cfg)


def test_the_anchor_survives_a_budget_that_the_alphabet_would_eat(tmp_path: Path):
    """The ppss case: 247 notes, room for 23. Alphabetical order showed `A…` and buried the
    notes the last sleep had just marked as the live ones."""
    cfg = _cfg(tmp_path, "\n[memory]\nmap_budget = 260\n")
    _vault(tmp_path, {"knowledge-base": [f"Note number {i:03d}" for i in range(247)]})
    _anchor(tmp_path, ["knowledge-base/Note number 246"])
    out = memorymap.render(cfg)
    assert len(out) <= 260
    assert "Note number 246" in out
    assert "(247 notes)" in out


def test_recently_touched_notes_come_before_stale_ones(tmp_path: Path):
    """Within a topical group the title says nothing about relevance, so recency ranks it."""
    cfg = _cfg(tmp_path)
    vault = _vault(tmp_path, {"knowledge-base": ["Aardvark", "Zebra"]})
    os.utime(vault / "knowledge-base" / "Aardvark.md", (1_700_000_000, 1_700_000_000))
    os.utime(vault / "knowledge-base" / "Zebra.md", (1_800_000_000, 1_800_000_000))
    line = next(ln for ln in memorymap.render(cfg).splitlines() if ln.startswith("knowledge-base"))
    assert line.index("Zebra") < line.index("Aardvark")


def test_session_notes_are_ranked_by_name_not_by_mtime(tmp_path: Path):
    """A session note's title *is* its date — exact, and it survives a `git clone`, which
    flattens every mtime in the vault to the checkout time. Editing an old session note to fix
    a typo must not make it look like the latest chapter."""
    cfg = _cfg(tmp_path)
    vault = _vault(tmp_path, {"session-memory": [
        "Session_2026-07-10_0853", "Session_2026-09-25_1534",
    ]})
    os.utime(vault / "session-memory" / "Session_2026-07-10_0853.md", (1_900_000_000, 1_900_000_000))
    line = next(ln for ln in memorymap.render(cfg).splitlines() if ln.startswith("session-memory"))
    assert line.index("2026-09-25") < line.index("2026-07-10")
