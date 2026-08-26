"""Basic Memory slugifies project and folder names; mirror that rule.

Basic Memory registers a project under a slug key (`LetTheAISleep` -> `let-the-aisleep`)
while still reporting the original display name over MCP. Which form its CLI wants depends
on the subcommand, so slugifying everything is wrong (checked against basic-memory 0.22.1):

    basic-memory project info let-the-aisleep   works
    basic-memory project info LetTheAISleep     fails, "set to cloud mode" (= no such project)
    basic-memory reindex -p LetTheAISleep       works
    basic-memory reindex -p let-the-aisleep     fails, "Project not found."

MCP tool calls (`write_note`, `search`, ...) take the display name. So: slugify only for
`project info`; hand every other caller `cfg.project` verbatim.
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