# Let The AI Sleep

Hybrid memory for Claude Code: an STM working buffer, a hook-enforced "sleep"
consolidation cycle, and a session anchor — built as a thin layer on top of
[Basic Memory](https://github.com/basicmachines-co/basic-memory) (which provides
the Markdown vault, CRUD, and local hybrid full-text + vector search).

## What it does
- **STM buffer** — Claude appends key facts as it works (`lts stm append`), surviving `/compact`.
- **/sleep** — consolidates the buffer into linked long-term notes with zero loss, then clears STM.
- **Hooks** — `PreCompact` snapshots raw material (backstop), `SessionStart` injects the anchor or
  forces an unfinished sleep to complete, `UserPromptSubmit` adds escalating sleep pressure.
- **/recall** — graph-priority hybrid retrieval, loading only what's relevant.

> **Note on semantic search:** Basic Memory serves full-text and graph links immediately on write,
> but vector embeddings are rebuilt by `basic-memory reindex --embeddings -p <project>` (not on every
> write). `/sleep` runs this after consolidating, and `install.py` lists it as a setup step.

## Requirements
- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- Basic Memory: `uv tool install basic-memory` (AGPL-3.0; used as a separate process over MCP)

## Install
```bash
uv venv && uv pip install -e '.[dev]'
python install.py
```
Then follow the printed next steps to create a Basic Memory project and register its MCP.

## License note
This project talks to Basic Memory only over MCP (no linking/embedding). Basic Memory itself is
AGPL-3.0; this repository's own license is declared separately.
