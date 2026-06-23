from pathlib import Path
from lts import anchor


def test_write_then_read_roundtrip(tmp_path: Path):
    f = tmp_path / "anchor.json"
    anchor.write_anchor(
        f,
        updated="2026-06-23 15:30",
        last_session="[[Session_2026-06-23_1530]]",
        active_topics=["db-choice"],
        active_notes=["[[DB_choice]]"],
    )
    data = anchor.read_anchor(f)
    assert data["active_topics"] == ["db-choice"]
    assert data["last_session"] == "[[Session_2026-06-23_1530]]"


def test_read_missing_returns_empty(tmp_path: Path):
    assert anchor.read_anchor(tmp_path / "nope.json") == {}


def test_read_invalid_json_returns_empty(tmp_path: Path):
    f = tmp_path / "anchor.json"
    f.write_text("not json", encoding="utf-8")
    assert anchor.read_anchor(f) == {}


def test_render_anchor_includes_notes(tmp_path: Path):
    block = anchor.render_anchor({
        "updated": "2026-06-23 15:30",
        "last_session": "[[S]]",
        "active_topics": ["t1"],
        "active_notes": ["[[N1]]", "[[N2]]"],
    })
    assert "[[N1]]" in block and "[[N2]]" in block
    assert anchor.render_anchor({}) == ""
