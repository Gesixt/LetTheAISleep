from dataclasses import dataclass, replace
from pathlib import Path

from lts import clients, transcript

_BASE = '[vault]\nproject = "test-project"\n'


def make_project(root: Path, extra: str = "") -> Path:
    """Mark `root` as an lts project root — a config.toml with a [vault] section."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.toml").write_text(_BASE + extra, encoding="utf-8")
    return root


# A context figure chosen to differ from what any transcript in this suite really measures, so a
# test that reads it back has read the probe adapter's answer and not `lts.transcript`'s.
PROBE_TOKENS = 4242


@dataclass(frozen=True)
class ProbeClient:
    """A registered client whose transcript parser answers differently from Claude Code's.

    `tests/test_hooks.py`'s `second_client` registers the Claude Code adapter under a second id,
    which is what asking *which file* a client touches needs. It cannot ask *which parser* read
    that file: both ids parse identically, so a caller that ignored its `client` and reached for
    `lts.transcript` directly would pass either way. Every answer below is therefore distinct from
    what the real parser returns for a transcript holding exchanges — no exchanges, a fixed token
    count, and nothing newer than any mark.

    It is not a stand-in for a second real client: it parses nothing at all. It exists so that the
    *routing* of a transcript read is observable.
    """

    id: str = "probe"
    default_context_window: int = 1_000_000

    def read_exchanges(self, path: Path) -> list[dict]:
        return []

    def measure_context(self, path: Path) -> tuple[int, str]:
        return PROBE_TOKENS, transcript.USAGE

    def newest_exchange_after(self, path: Path, stamp: str | None) -> bool:
        return False


def register_alias_client(monkeypatch, client_id: str = "second") -> str:
    """Register the Claude Code adapter under a second id, for the duration of one test.

    The same parser, a different state directory: this is what asking *which file* a client reads
    needs. For *which parser* read it, see `ProbeClient` — an alias cannot answer that, because
    both ids parse identically.

    The adapter is copied with its `id` set to `client_id`, not registered under a second key as
    itself. A registry key and its adapter's `id` are the same string for every real client, and
    `lts doctor` relies on it: it reports under `clients.resolve(...).id`, so an adapter keeping
    its old `id` would be asked about as `second` and answer about `claude_code`.
    """
    monkeypatch.setitem(clients._REGISTRY, client_id,
                        replace(clients.resolve(clients.DEFAULT), id=client_id))
    return client_id


def register_probe_client(monkeypatch) -> str:
    """Register `ProbeClient` for the duration of one test; returns its id."""
    monkeypatch.setitem(clients._REGISTRY, ProbeClient.id, ProbeClient())
    return ProbeClient.id
