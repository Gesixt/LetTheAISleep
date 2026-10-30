"""Claude Code's transcript, behind the client interface.

No parsing lives here. `lts.transcript` is the implementation and stays exactly where every
existing caller and test already expects it; this module only makes it *one* client among others,
so that adding a second one does not mean editing the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lts import transcript


@dataclass(frozen=True)
class ClaudeCode:
    id: str = "claude_code"
    default_context_window: int = transcript.DEFAULT_WINDOW

    def read_exchanges(self, path: Path) -> list[dict]:
        return transcript.read_exchanges(Path(path))

    def measure_context(self, path: Path) -> tuple[int, str]:
        return transcript.measure_context(Path(path))


ADAPTER = ClaudeCode()
