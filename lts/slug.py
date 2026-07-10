"""Basic Memory slugifies project and folder names; mirror that rule.

Basic Memory registers a project under a slug key (`LetTheAISleep` -> `let-the-aisleep`)
while still reporting the original display name over MCP. Its CLI looks projects up by the
slug, so `basic-memory project info LetTheAISleep` fails — with a misleading "cloud mode"
error rather than "not found". Anything we hand to the CLI must be slugified first.
"""

from __future__ import annotations

import re

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def slugify_project(name: str) -> str:
    """`LetTheAISleep` -> `let-the-aisleep`; already-slug names pass through unchanged."""
    s = _CAMEL_BOUNDARY.sub("-", name)
    s = _NON_ALNUM.sub("-", s)
    return s.strip("-").lower()