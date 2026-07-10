"""Names and directories for notes. The model never composes these itself.

Basic Memory resolves `[[Title]]` by title and permalink, not by containing folder. Two session
notes called `Session_2026-07-10_1002` in different author folders would make every link to them
ambiguous, so in team mode the author goes into the title as well as the path.
"""

from __future__ import annotations

from datetime import datetime

from lts.config import Config
from lts.slug import slugify_project

KNOWLEDGE_DIR = "knowledge-base"
SESSION_DIR = "session-memory"


class InvalidAuthor(ValueError):
    """The author name would not survive Basic Memory's directory slugification."""


def validate_author(author: str) -> str:
    slug = slugify_project(author)
    if not slug:
        raise InvalidAuthor(f"author {author!r} has no letters or digits to slugify")
    if slug != author:
        raise InvalidAuthor(f"author must be slug-stable: use {slug!r}, not {author!r}")
    return author


def session_directory(cfg: Config) -> str:
    return f"{SESSION_DIR}/{cfg.author}" if cfg.author else SESSION_DIR


def session_note_name(cfg: Config, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M")
    return f"Session_{cfg.author}_{stamp}" if cfg.author else f"Session_{stamp}"