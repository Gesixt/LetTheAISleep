"""Codex rollout adapter, observed on codex-cli 0.169.0.

Only response_item messages are dialogue. Event messages duplicate it; reasoning,
tool traffic, developer instructions and injected environment records are not memory.
The rollout is an unstable interface; tests/fixtures pins the observed shape.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lts import transcript


def timestamp(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return None  # never guess a local timezone
        return dt.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except ValueError:
        return None


def _record(raw) -> dict:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError):
        return {}


def _exchange(d: dict, fallback_id: str = '') -> dict | None:
    p = d.get('payload')
    if d.get('type') != 'response_item' or not isinstance(p, dict):
        return None
    if p.get('type') != 'message' or p.get('role') not in ('user', 'assistant'):
        return None
    if p.get('role') == 'assistant' and p.get('phase') not in (None, 'commentary', 'final_answer'):
        return None
    content = p.get('content')
    if not isinstance(content, list):
        return None
    text = '\n'.join(b['text'] for b in content if isinstance(b, dict)
                     and b.get('type') in ('input_text', 'output_text')
                     and isinstance(b.get('text'), str)).strip()
    if not text or text.startswith(('<environment_context>', '# AGENTS.md instructions',
                                    '<turn_aborted>', '<permissions instructions>')):
        return None
    if p['role'] == 'user' and transcript._BARE_SLASH_COMMAND.fullmatch(text):
        return None
    return {'role': p['role'], 'text': text, 'uuid': p.get('id') or fallback_id,
            'timestamp': timestamp(d.get('timestamp'))}


def _usage(path: Path) -> dict:
    for raw in transcript._tail_lines(path):
        d = _record(raw)
        p = d.get('payload')
        if d.get('type') == 'event_msg' and isinstance(p, dict) and p.get('type') == 'token_count':
            info = p.get('info')
            if isinstance(info, dict) and isinstance(info.get('last_token_usage'), dict):
                return info
    return {}


@dataclass(frozen=True)
class Codex:
    id: str = 'codex'
    default_context_window: int = 0  # unknown until the rollout reports the active model

    def read_exchanges(self, path: Path) -> list[dict]:
        result = []
        try:
            with path.open('rb') as stream:
                for index, raw in enumerate(stream):
                    # Stable across appends; include position so identical undated messages
                    # remain distinct. The path scopes IDs across unrelated sessions.
                    identity = hashlib.sha256(str(path.resolve()).encode() + b":" + str(index).encode() + b":" + raw.rstrip(b"\r\n")).hexdigest()
                    ex = _exchange(_record(raw), 'codex-' + identity)
                    if ex:
                        result.append(ex)
        except OSError:
            return []
        return result

    def measure_context(self, path: Path) -> tuple[int, str]:
        info = _usage(path)
        tokens = info.get('last_token_usage', {}).get('input_tokens')
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
            # cached_input_tokens are already part of input_tokens, not additional context.
            return tokens, transcript.USAGE
        if not path.is_file():
            return 0, transcript.NO_FILE
        return path.stat().st_size // 4, transcript.ESTIMATE

    def context_window(self, path: Path) -> int:
        value = _usage(path).get('model_context_window')
        return value if isinstance(value, int) and value > 0 else 0

    def newest_exchange_after(self, path: Path, stamp: str | None) -> bool:
        boundary = timestamp(stamp)
        for raw in transcript._tail_lines(path):
            ex = _exchange(_record(raw))
            if ex:
                if boundary is None:
                    return True
                if ex['timestamp']:
                    return ex['timestamp'] > boundary
        return False


ADAPTER = Codex()
