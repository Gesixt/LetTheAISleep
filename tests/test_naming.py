from datetime import datetime
from pathlib import Path

import pytest

from lts import naming
from lts.config import load_config
from tests.helpers import make_project

AT = datetime(2026, 7, 10, 10, 2)


def _cfg(tmp_path: Path, author: str | None = None):
    extra = f'author = "{author}"\n' if author else ""
    make_project(tmp_path, extra)
    return load_config(tmp_path)


def test_single_developer_names_are_unchanged(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert naming.session_note_name(cfg, AT) == "Session_2026-07-10_1002"
    assert naming.session_directory(cfg) == "session-memory"


def test_team_mode_namespaces_directory_and_title(tmp_path: Path):
    cfg = _cfg(tmp_path, "dmitrii")
    # the title carries the author because Basic Memory resolves [[links]] by title, not folder
    assert naming.session_note_name(cfg, AT) == "Session_dmitrii_2026-07-10_1002"
    assert naming.session_directory(cfg) == "session-memory/dmitrii"


def test_validate_author_accepts_a_slug():
    assert naming.validate_author("dmitrii") == "dmitrii"
    assert naming.validate_author("dmitry-mitin") == "dmitry-mitin"


def test_validate_author_rejects_a_non_slug_and_suggests_one():
    with pytest.raises(naming.InvalidAuthor) as exc:
        naming.validate_author("Dmitry Mitin")
    assert "dmitry-mitin" in str(exc.value)


def test_validate_author_rejects_an_empty_slug():
    with pytest.raises(naming.InvalidAuthor):
        naming.validate_author("---")