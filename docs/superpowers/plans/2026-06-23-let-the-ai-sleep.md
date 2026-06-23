# Let The AI Sleep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code integration layer that turns Basic Memory into Claude's hybrid memory with an STM buffer, a hook-enforced "sleep" consolidation cycle, and a session anchor — so context never silently fills and `/sleep` loses no knowledge.

**Architecture:** Basic Memory (existing MCP) owns long-term storage, CRUD, hybrid search and the graph. We ship only the new layer: a small deterministic Python helper package `lts` (sidecar files: STM buffer, anchor, pending snapshots, transcript pressure), three Claude Code hooks that call it, two skills (`/sleep`, `/recall`), templates, and an installer. No custom MCP server.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`, `json`, `pathlib`, `argparse`), pytest, `uv`, Basic Memory (AGPL-3.0, installed separately), Claude Code hooks + skills.

> **Note on `lts`:** the spec (§4) describes STM/anchor as "plain sidecar files managed by skills and hooks via thin scripts." `lts` is the testable implementation of those thin scripts — a helper library + `lts` CLI. It is NOT an MCP server; it never talks to Basic Memory. Skills/hooks invoke it for deterministic file operations.

## Global Constraints

- **Language:** every repo-committed file (code, comments, docs, commit messages) is in **English**. No Russian, no Cyrillic, in repo content.
- **Git author:** name `Gesixt`, email `dmitry.mitin2@gmail.com`. Commit with `git -c user.name=... -c user.email=...` or a repo-local `git config`.
- **Commit messages:** Conventional Commits (`feat:`, `test:`, `chore:`, `docs:`). End the body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Python floor:** 3.11 (uses stdlib `tomllib`).
- **No custom MCP server.** LTM operations are Basic Memory's tools, invoked only from skills (prompts).
- **Sidecar isolation:** the `.ai_memory/` sidecar must live **outside** the Basic Memory note tree so notes never index STM/anchor scratch.
- **Determinism:** STM/anchor/pending/pressure logic lives in `lts` (testable), never in prose-only prompts.
- **Gitignored runtime:** `.ai_memory/`, `.ai_vault/`, `config.toml`, `*.ru.md` are already in `.gitignore` (do not commit them).
- **Spec:** `docs/superpowers/specs/2026-06-23-hybrid-memory-design.md` (v1.1) is the source of truth.

---

## File Structure

```
let-the-ai-sleep/
├── pyproject.toml                  # package "let-the-ai-sleep", module "lts", `lts` CLI entrypoint
├── config.example.toml             # committed template; real config.toml is gitignored
├── README.md
├── lts/
│   ├── __init__.py
│   ├── config.py                   # load config.toml + defaults; resolve project root
│   ├── paths.py                    # sidecar path helpers (.ai_memory/...)
│   ├── stm.py                      # append / read / clear / is_empty on STM buffer
│   ├── anchor.py                   # read / write / render anchor.json
│   ├── pending.py                  # dump / has / list / clear PreCompact snapshots
│   ├── transcript.py               # token estimate + pressure level
│   └── cli.py                      # `lts` argparse entrypoint -> subcommands
├── claude/
│   ├── skills/sleep/SKILL.md       # /sleep consolidation prompt
│   ├── skills/recall/SKILL.md      # /recall retrieval prompt
│   └── hooks/
│       ├── session_start.py        # inject anchor / force completion
│       ├── pre_compact.py          # dump raw transcript snapshot
│       └── user_prompt_submit.py   # escalating sleep pressure
├── templates/
│   ├── session.md                  # session-note template (bootstrapped into the vault)
│   └── knowledge.md                # KB-note template
├── install.py                      # idempotent installer wizard
└── tests/
    ├── test_config.py
    ├── test_paths.py
    ├── test_stm.py
    ├── test_anchor.py
    ├── test_pending.py
    ├── test_transcript.py
    ├── test_cli.py
    ├── test_hooks.py
    └── test_install.py
```

**Dependency order:** Task 1 (scaffold) → Task 2 (config) → Task 3 (paths) → Tasks 4–7 (stm/anchor/pending/transcript, independent of each other) → Task 8 (cli, depends on 2–7) → Tasks 9–11 (hooks, depend on cli/modules) → Tasks 12–13 (skills) → Task 14 (templates) → Task 15 (install) → Task 16 (README + smoke).

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `lts/__init__.py`
- Create: `tests/__init__.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `lts` package; `pytest` runnable; `lts` console entrypoint declared (implemented in Task 8).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "let-the-ai-sleep"
version = "0.1.0"
description = "Claude Code integration layer that gives Claude hybrid memory on top of Basic Memory"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
lts = "lts.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["lts"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package marker `lts/__init__.py`**

```python
"""Let The AI Sleep — Claude Code hybrid-memory integration layer."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `tests/__init__.py`** (empty file)

```python
```

- [ ] **Step 4: Create the dev environment and verify pytest runs**

Run: `uv venv && uv pip install -e '.[dev]'`
Then: `uv run pytest -q`
Expected: `no tests ran` (exit code 5) — confirms package installs and pytest is wired.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml lts/__init__.py tests/__init__.py
git commit -m "chore: scaffold lts package and pytest"
```

---

### Task 2: Config loading

**Files:**
- Create: `lts/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DEFAULTS: dict` with keys `vault_mode`, `project`, `vault_path`, `pressure_warn`, `pressure_force`.
  - `@dataclass(frozen=True) Config` fields: `project_root: Path`, `vault_mode: str`, `project: str`, `vault_path: str | None`, `pressure_warn: float`, `pressure_force: float`.
  - `find_project_root(start: Path) -> Path` — nearest ancestor containing `.git` or `config.toml`, else `start`.
  - `load_config(start: Path | None = None) -> Config` — reads `<root>/config.toml` if present, overlays onto `DEFAULTS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
from lts.config import load_config, find_project_root, DEFAULTS


def test_defaults_when_no_config(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.vault_mode == DEFAULTS["vault_mode"]
    assert cfg.pressure_warn == 0.60
    assert cfg.pressure_force == 0.80
    assert cfg.project_root == tmp_path


def test_reads_config_toml(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[vault]\nmode = "global"\nproject = "lts-demo"\n'
        '[sleep]\npressure_warn = 0.5\npressure_force = 0.75\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.vault_mode == "global"
    assert cfg.project == "lts-demo"
    assert cfg.pressure_warn == 0.5
    assert cfg.pressure_force == 0.75


def test_find_project_root_walks_up(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lts.config'`.

- [ ] **Step 3: Write the implementation**

```python
# lts/config.py
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {
    "vault_mode": "per_project",   # per_project | global
    "project": "lts-default",
    "vault_path": None,
    "pressure_warn": 0.60,
    "pressure_force": 0.80,
}


@dataclass(frozen=True)
class Config:
    project_root: Path
    vault_mode: str
    project: str
    vault_path: str | None
    pressure_warn: float
    pressure_force: float


def find_project_root(start: Path) -> Path:
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists() or (candidate / "config.toml").exists():
            return candidate
    return start


def load_config(start: Path | None = None) -> Config:
    root = find_project_root(Path(start) if start else Path.cwd())
    values = dict(DEFAULTS)
    cfg_file = root / "config.toml"
    if cfg_file.exists():
        data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
        vault = data.get("vault", {})
        sleep = data.get("sleep", {})
        if "mode" in vault:
            values["vault_mode"] = vault["mode"]
        if "project" in vault:
            values["project"] = vault["project"]
        if "path" in vault:
            values["vault_path"] = vault["path"]
        if "pressure_warn" in sleep:
            values["pressure_warn"] = float(sleep["pressure_warn"])
        if "pressure_force" in sleep:
            values["pressure_force"] = float(sleep["pressure_force"])
    return Config(project_root=root, **values)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add lts/config.py tests/test_config.py
git commit -m "feat: config loading with defaults and config.toml overlay"
```

---

### Task 3: Sidecar path helpers

**Files:**
- Create: `lts/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: `lts.config.Config`.
- Produces:
  - `sidecar_root(cfg: Config) -> Path` → `<project_root>/.ai_memory`.
  - `stm_file(cfg: Config, session_id: str) -> Path` → `<sidecar>/stm/<safe_session>.md`.
  - `anchor_file(cfg: Config) -> Path` → `<sidecar>/anchor.json`.
  - `pending_dir(cfg: Config) -> Path` → `<sidecar>/pending_consolidation`.
  - `ensure_sidecar(cfg: Config) -> None` — creates `stm/` and `pending_consolidation/` dirs.
  - `safe_session_id(session_id: str) -> str` — strips path separators / unsafe chars; empty → `"default"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py
from pathlib import Path
from lts.config import load_config
from lts import paths


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_sidecar_paths(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert paths.sidecar_root(cfg) == tmp_path / ".ai_memory"
    assert paths.anchor_file(cfg) == tmp_path / ".ai_memory" / "anchor.json"
    assert paths.pending_dir(cfg) == tmp_path / ".ai_memory" / "pending_consolidation"
    assert paths.stm_file(cfg, "sess1") == tmp_path / ".ai_memory" / "stm" / "sess1.md"


def test_safe_session_id():
    assert paths.safe_session_id("a/b c") == "a_b_c"
    assert paths.safe_session_id("") == "default"
    assert paths.safe_session_id("../evil") == "__evil"


def test_ensure_sidecar_creates_dirs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    paths.ensure_sidecar(cfg)
    assert (tmp_path / ".ai_memory" / "stm").is_dir()
    assert (tmp_path / ".ai_memory" / "pending_consolidation").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lts.paths'`.

- [ ] **Step 3: Write the implementation**

```python
# lts/paths.py
from __future__ import annotations

import re
from pathlib import Path

from lts.config import Config

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def safe_session_id(session_id: str) -> str:
    cleaned = _UNSAFE.sub("_", session_id or "")
    return cleaned or "default"


def sidecar_root(cfg: Config) -> Path:
    return cfg.project_root / ".ai_memory"


def stm_file(cfg: Config, session_id: str) -> Path:
    return sidecar_root(cfg) / "stm" / f"{safe_session_id(session_id)}.md"


def anchor_file(cfg: Config) -> Path:
    return sidecar_root(cfg) / "anchor.json"


def pending_dir(cfg: Config) -> Path:
    return sidecar_root(cfg) / "pending_consolidation"


def ensure_sidecar(cfg: Config) -> None:
    (sidecar_root(cfg) / "stm").mkdir(parents=True, exist_ok=True)
    pending_dir(cfg).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add lts/paths.py tests/test_paths.py
git commit -m "feat: sidecar path helpers with safe session ids"
```

---

### Task 4: STM buffer

**Files:**
- Create: `lts/stm.py`
- Test: `tests/test_stm.py`

**Interfaces:**
- Consumes: nothing (operates on an explicit `Path`).
- Produces:
  - `append(stm_path: Path, text: str) -> None` — creates parent dirs; appends `text` plus a trailing newline.
  - `read(stm_path: Path) -> str` — returns `""` if missing.
  - `clear(stm_path: Path) -> None` — removes the file if present (idempotent).
  - `is_empty(stm_path: Path) -> bool` — True if missing or only whitespace.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stm.py
from pathlib import Path
from lts import stm


def test_append_then_read(tmp_path: Path):
    f = tmp_path / "stm" / "s.md"
    stm.append(f, "decision: use PostgreSQL")
    stm.append(f, "number: 50% budget")
    assert stm.read(f) == "decision: use PostgreSQL\nnumber: 50% budget\n"


def test_read_missing_returns_empty(tmp_path: Path):
    assert stm.read(tmp_path / "nope.md") == ""


def test_is_empty(tmp_path: Path):
    f = tmp_path / "s.md"
    assert stm.is_empty(f) is True
    stm.append(f, "x")
    assert stm.is_empty(f) is False


def test_clear_is_idempotent(tmp_path: Path):
    f = tmp_path / "s.md"
    stm.append(f, "x")
    stm.clear(f)
    assert stm.is_empty(f) is True
    stm.clear(f)  # no error on second clear
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lts.stm'`.

- [ ] **Step 3: Write the implementation**

```python
# lts/stm.py
from __future__ import annotations

from pathlib import Path


def append(stm_path: Path, text: str) -> None:
    stm_path.parent.mkdir(parents=True, exist_ok=True)
    with stm_path.open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip("\n") + "\n")


def read(stm_path: Path) -> str:
    if not stm_path.exists():
        return ""
    return stm_path.read_text(encoding="utf-8")


def is_empty(stm_path: Path) -> bool:
    return read(stm_path).strip() == ""


def clear(stm_path: Path) -> None:
    stm_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_stm.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add lts/stm.py tests/test_stm.py
git commit -m "feat: append-only STM buffer file operations"
```

---

### Task 5: Anchor

**Files:**
- Create: `lts/anchor.py`
- Test: `tests/test_anchor.py`

**Interfaces:**
- Consumes: nothing (explicit `Path`).
- Produces:
  - `read_anchor(anchor_path: Path) -> dict` — `{}` if missing or invalid JSON.
  - `write_anchor(anchor_path: Path, *, updated: str, last_session: str, active_topics: list[str], active_notes: list[str]) -> None`.
  - `render_anchor(anchor: dict) -> str` — compact human/LLM-readable block for hook injection; returns `""` for `{}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_anchor.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_anchor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lts.anchor'`.

- [ ] **Step 3: Write the implementation**

```python
# lts/anchor.py
from __future__ import annotations

import json
from pathlib import Path


def read_anchor(anchor_path: Path) -> dict:
    if not anchor_path.exists():
        return {}
    try:
        return json.loads(anchor_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def write_anchor(
    anchor_path: Path,
    *,
    updated: str,
    last_session: str,
    active_topics: list[str],
    active_notes: list[str],
) -> None:
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": updated,
        "last_session": last_session,
        "active_topics": active_topics,
        "active_notes": active_notes,
    }
    anchor_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_anchor(anchor: dict) -> str:
    if not anchor:
        return ""
    topics = ", ".join(anchor.get("active_topics", []))
    notes = ", ".join(anchor.get("active_notes", []))
    return (
        "## Session Anchor (memory entry points)\n"
        f"- Last session: {anchor.get('last_session', '')}\n"
        f"- Active topics: {topics}\n"
        f"- Active notes: {notes}\n"
        "Pull bodies on demand via Basic Memory `read_note`/`search`; do not load everything."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_anchor.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add lts/anchor.py tests/test_anchor.py
git commit -m "feat: session anchor read/write/render"
```

---

### Task 6: Pending snapshots

**Files:**
- Create: `lts/pending.py`
- Test: `tests/test_pending.py`

**Interfaces:**
- Consumes: nothing (explicit `Path`).
- Produces:
  - `dump_snapshot(pending_dir: Path, session_id: str, content: str) -> Path` — writes `<pending_dir>/<safe_session>-<n>.md`, where `<n>` is the next free integer (deterministic, no timestamp/random). Returns the path.
  - `has_pending(pending_dir: Path) -> bool`.
  - `list_snapshots(pending_dir: Path) -> list[Path]` — sorted.
  - `clear_all(pending_dir: Path) -> None`.

> Determinism note: snapshot file names use an incrementing counter, not a timestamp, so behavior is reproducible in tests and across resumes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pending.py
from pathlib import Path
from lts import pending


def test_dump_creates_incrementing_files(tmp_path: Path):
    p1 = pending.dump_snapshot(tmp_path, "sess1", "raw transcript A")
    p2 = pending.dump_snapshot(tmp_path, "sess1", "raw transcript B")
    assert p1.name == "sess1-1.md"
    assert p2.name == "sess1-2.md"
    assert p1.read_text(encoding="utf-8") == "raw transcript A"


def test_has_pending(tmp_path: Path):
    assert pending.has_pending(tmp_path) is False
    pending.dump_snapshot(tmp_path, "s", "x")
    assert pending.has_pending(tmp_path) is True


def test_list_and_clear(tmp_path: Path):
    pending.dump_snapshot(tmp_path, "s", "a")
    pending.dump_snapshot(tmp_path, "s", "b")
    assert len(pending.list_snapshots(tmp_path)) == 2
    pending.clear_all(tmp_path)
    assert pending.has_pending(tmp_path) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pending.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lts.pending'`.

- [ ] **Step 3: Write the implementation**

```python
# lts/pending.py
from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe(session_id: str) -> str:
    return _UNSAFE.sub("_", session_id or "") or "default"


def dump_snapshot(pending_dir: Path, session_id: str, content: str) -> Path:
    pending_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe(session_id)
    n = 1
    while (pending_dir / f"{safe}-{n}.md").exists():
        n += 1
    target = pending_dir / f"{safe}-{n}.md"
    target.write_text(content, encoding="utf-8")
    return target


def list_snapshots(pending_dir: Path) -> list[Path]:
    if not pending_dir.exists():
        return []
    return sorted(pending_dir.glob("*.md"))


def has_pending(pending_dir: Path) -> bool:
    return len(list_snapshots(pending_dir)) > 0


def clear_all(pending_dir: Path) -> None:
    for snap in list_snapshots(pending_dir):
        snap.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pending.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add lts/pending.py tests/test_pending.py
git commit -m "feat: pending consolidation snapshot store"
```

---

### Task 7: Transcript pressure

**Files:**
- Create: `lts/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Consumes: nothing (explicit `Path` + numbers).
- Produces:
  - `DEFAULT_WINDOW: int = 200_000`.
  - `estimate_tokens(transcript_path: Path) -> int` — approx `len(text) // 4`; `0` if missing.
  - `pressure_level(tokens: int, window: int, warn: float, force: float) -> str` — returns `"force"` if `tokens/window >= force`, else `"warn"` if `>= warn`, else `"none"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transcript.py
from pathlib import Path
from lts import transcript


def test_estimate_tokens_missing(tmp_path: Path):
    assert transcript.estimate_tokens(tmp_path / "nope.jsonl") == 0


def test_estimate_tokens_approx(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    f.write_text("x" * 400, encoding="utf-8")
    assert transcript.estimate_tokens(f) == 100  # 400 chars / 4


def test_pressure_levels():
    w = 1000
    assert transcript.pressure_level(100, w, 0.6, 0.8) == "none"
    assert transcript.pressure_level(600, w, 0.6, 0.8) == "warn"
    assert transcript.pressure_level(799, w, 0.6, 0.8) == "warn"
    assert transcript.pressure_level(800, w, 0.6, 0.8) == "force"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lts.transcript'`.

- [ ] **Step 3: Write the implementation**

```python
# lts/transcript.py
from __future__ import annotations

from pathlib import Path

DEFAULT_WINDOW = 200_000


def estimate_tokens(transcript_path: Path) -> int:
    if not transcript_path.exists():
        return 0
    return len(transcript_path.read_text(encoding="utf-8", errors="ignore")) // 4


def pressure_level(tokens: int, window: int, warn: float, force: float) -> str:
    if window <= 0:
        return "none"
    ratio = tokens / window
    if ratio >= force:
        return "force"
    if ratio >= warn:
        return "warn"
    return "none"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add lts/transcript.py tests/test_transcript.py
git commit -m "feat: transcript token estimate and sleep-pressure level"
```

---

### Task 8: `lts` CLI

**Files:**
- Create: `lts/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `config`, `paths`, `stm`, `anchor`, `pending`, `transcript`.
- Produces: `main(argv: list[str] | None = None) -> int`. Subcommands (each resolves config from `--root` or cwd):
  - `lts stm append --root R --session S --text T` → appends, prints `ok`.
  - `lts stm read --root R --session S` → prints buffer.
  - `lts stm clear --root R --session S` → clears, prints `ok`.
  - `lts anchor render --root R` → prints `render_anchor(read_anchor(...))`.
  - `lts pending dump --root R --session S --text T` → prints created path.
  - `lts pending has --root R` → prints `yes`/`no`.
  - `lts pressure --root R --transcript P` → prints `none`/`warn`/`force`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from pathlib import Path
from lts.cli import main


def test_stm_append_read_clear(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    assert main(["stm", "append", "--root", str(tmp_path), "--session", "s", "--text", "fact one"]) == 0
    main(["stm", "read", "--root", str(tmp_path), "--session", "s"])
    assert "fact one" in capsys.readouterr().out
    main(["stm", "clear", "--root", str(tmp_path), "--session", "s"])
    main(["stm", "read", "--root", str(tmp_path), "--session", "s"])
    assert capsys.readouterr().out.strip() == ""


def test_pressure(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    t = tmp_path / "t.jsonl"
    t.write_text("x" * (4 * 170_000), encoding="utf-8")  # ~170k tokens of 200k
    main(["pressure", "--root", str(tmp_path), "--transcript", str(t)])
    assert capsys.readouterr().out.strip() == "force"


def test_pending_has(tmp_path: Path, capsys):
    (tmp_path / ".git").mkdir()
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "no"
    main(["pending", "dump", "--root", str(tmp_path), "--session", "s", "--text", "raw"])
    main(["pending", "has", "--root", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "yes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lts.cli'`.

- [ ] **Step 3: Write the implementation**

```python
# lts/cli.py
from __future__ import annotations

import argparse
from pathlib import Path

from lts import anchor, paths, pending, stm, transcript
from lts.config import load_config


def _cfg(root: str | None):
    return load_config(Path(root) if root else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lts")
    parser.add_argument("--root", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stm = sub.add_parser("stm")
    stm_sub = p_stm.add_subparsers(dest="op", required=True)
    for op in ("append", "read", "clear"):
        sp = stm_sub.add_parser(op)
        sp.add_argument("--root", default=None)
        sp.add_argument("--session", required=True)
        if op == "append":
            sp.add_argument("--text", required=True)

    p_anchor = sub.add_parser("anchor")
    anchor_sub = p_anchor.add_subparsers(dest="op", required=True)
    sp = anchor_sub.add_parser("render")
    sp.add_argument("--root", default=None)

    p_pending = sub.add_parser("pending")
    pending_sub = p_pending.add_subparsers(dest="op", required=True)
    for op in ("dump", "has"):
        sp = pending_sub.add_parser(op)
        sp.add_argument("--root", default=None)
        if op == "dump":
            sp.add_argument("--session", required=True)
            sp.add_argument("--text", required=True)

    p_pressure = sub.add_parser("pressure")
    p_pressure.add_argument("--root", default=None)
    p_pressure.add_argument("--transcript", required=True)

    args = parser.parse_args(argv)
    root = getattr(args, "root", None)
    cfg = _cfg(root)

    if args.cmd == "stm":
        f = paths.stm_file(cfg, args.session)
        if args.op == "append":
            stm.append(f, args.text)
            print("ok")
        elif args.op == "read":
            print(stm.read(f), end="")
        elif args.op == "clear":
            stm.clear(f)
            print("ok")
    elif args.cmd == "anchor":
        print(anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg))))
    elif args.cmd == "pending":
        d = paths.pending_dir(cfg)
        if args.op == "dump":
            print(pending.dump_snapshot(d, args.session, args.text))
        elif args.op == "has":
            print("yes" if pending.has_pending(d) else "no")
    elif args.cmd == "pressure":
        tokens = transcript.estimate_tokens(Path(args.transcript))
        print(transcript.pressure_level(
            tokens, transcript.DEFAULT_WINDOW, cfg.pressure_warn, cfg.pressure_force
        ))
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (all tasks 2–8 green).

- [ ] **Step 6: Commit**

```bash
git add lts/cli.py tests/test_cli.py
git commit -m "feat: lts CLI over config/stm/anchor/pending/transcript"
```

---

### Task 9: PreCompact hook

**Files:**
- Create: `claude/hooks/pre_compact.py`
- Test: `tests/test_hooks.py` (shared file; this task adds the PreCompact test)

**Interfaces:**
- Consumes: `lts.config.load_config`, `lts.paths`, `lts.pending`.
- Produces:
  - `run(event: dict, *, root: Path | None = None) -> dict` — reads `event["transcript_path"]`, dumps its content (or `""` if missing) into `pending_consolidation/`, returns `{}`. Pure function (testable).
  - `main()` — reads JSON from stdin, calls `run`, prints `json.dumps(result)`.

> Claude Code hook contract: hooks receive a JSON object on stdin with fields including `session_id`, `transcript_path`, `cwd`, `hook_event_name`. PreCompact also gets `trigger` (`"manual"`/`"auto"`). Exit 0. This hook's job is the hard backstop: snapshot raw material before anything else.

- [ ] **Step 1: Write the failing test (append to `tests/test_hooks.py`)**

```python
# tests/test_hooks.py
from pathlib import Path
import importlib.util


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOOKS = Path(__file__).resolve().parents[1] / "claude" / "hooks"


def test_pre_compact_dumps_transcript(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("conversation raw text", encoding="utf-8")
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == "conversation raw text"


def test_pre_compact_missing_transcript_still_writes(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(tmp_path / "nope.jsonl")}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: FAIL — file `claude/hooks/pre_compact.py` not found.

- [ ] **Step 3: Write the implementation**

```python
# claude/hooks/pre_compact.py
from __future__ import annotations

import json
import sys
from pathlib import Path

from lts import paths, pending
from lts.config import load_config


def run(event: dict, *, root: Path | None = None) -> dict:
    cfg = load_config(root)
    paths.ensure_sidecar(cfg)
    transcript_path = Path(event.get("transcript_path", ""))
    content = ""
    if transcript_path.exists():
        content = transcript_path.read_text(encoding="utf-8", errors="ignore")
    pending.dump_snapshot(paths.pending_dir(cfg), event.get("session_id", "default"), content)
    return {}


def main() -> None:
    event = json.load(sys.stdin)
    print(json.dumps(run(event)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add claude/hooks/pre_compact.py tests/test_hooks.py
git commit -m "feat: PreCompact hook dumps raw transcript snapshot"
```

---

### Task 10: SessionStart hook

**Files:**
- Create: `claude/hooks/session_start.py`
- Test: `tests/test_hooks.py` (add SessionStart tests)

**Interfaces:**
- Consumes: `lts.config`, `lts.paths`, `lts.anchor`, `lts.pending`, `lts.stm`.
- Produces:
  - `build_context(event: dict, *, root: Path | None = None) -> str` — returns the text to inject:
    - If pending snapshots exist OR the session's STM is non-empty → a "finish sleeping first" instruction.
    - Else → `render_anchor(...)` (may be `""` if no anchor yet).
  - `run(event, *, root=None) -> dict` — wraps the text as `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <text>}}`; returns `{}` if text is empty.
  - `main()` — stdin JSON → `run` → stdout JSON.

> SessionStart input includes `source` (`"startup"`/`"resume"`/`"clear"`) and `session_id`. `hookSpecificOutput.additionalContext` is how SessionStart injects text into the session. Verify the exact key against the installed Claude Code version during Task 16 smoke test; if it differs, adjust `run()` only.

- [ ] **Step 1: Write the failing test (append to `tests/test_hooks.py`)**

```python
def test_session_start_injects_anchor_when_clean(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    from lts.config import load_config
    from lts import anchor, paths
    cfg = load_config(tmp_path)
    paths.ensure_sidecar(cfg)
    anchor.write_anchor(
        paths.anchor_file(cfg),
        updated="2026-06-23 15:30",
        last_session="[[S]]",
        active_topics=["t"],
        active_notes=["[[N1]]"],
    )
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "[[N1]]" in text
    assert "finish sleeping" not in text.lower()


def test_session_start_forces_completion_when_pending(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    from lts.config import load_config
    from lts import paths, pending
    cfg = load_config(tmp_path)
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    ss = _load("session_start", HOOKS / "session_start.py")
    text = ss.build_context({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert "finish sleeping" in text.lower()


def test_session_start_run_wraps_context(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    from lts import paths, pending
    from lts.config import load_config
    cfg = load_config(tmp_path)
    pending.dump_snapshot(paths.pending_dir(cfg), "s", "raw")
    ss = _load("session_start", HOOKS / "session_start.py")
    out = ss.run({"session_id": "s", "source": "startup"}, root=tmp_path)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "finish sleeping" in out["hookSpecificOutput"]["additionalContext"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: FAIL — `claude/hooks/session_start.py` not found.

- [ ] **Step 3: Write the implementation**

```python
# claude/hooks/session_start.py
from __future__ import annotations

import json
import sys
from pathlib import Path

from lts import anchor, paths, pending, stm
from lts.config import load_config

_FORCE_MSG = (
    "## Unfinished sleep detected\n"
    "There is un-consolidated memory from a previous session (STM buffer or a "
    "PreCompact snapshot). Before doing anything else, run the `/sleep` skill to "
    "finish sleeping this material into long-term notes, then continue."
)


def build_context(event: dict, *, root: Path | None = None) -> str:
    cfg = load_config(root)
    paths.ensure_sidecar(cfg)
    session_id = event.get("session_id", "default")
    pending_present = pending.has_pending(paths.pending_dir(cfg))
    stm_present = not stm.is_empty(paths.stm_file(cfg, session_id))
    if pending_present or stm_present:
        return _FORCE_MSG
    return anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg)))


def run(event: dict, *, root: Path | None = None) -> dict:
    text = build_context(event, root=root)
    if not text:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }


def main() -> None:
    event = json.load(sys.stdin)
    print(json.dumps(run(event)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: PASS (5 passed total in this file).

- [ ] **Step 5: Commit**

```bash
git add claude/hooks/session_start.py tests/test_hooks.py
git commit -m "feat: SessionStart hook injects anchor or forces sleep completion"
```

---

### Task 11: UserPromptSubmit hook

**Files:**
- Create: `claude/hooks/user_prompt_submit.py`
- Test: `tests/test_hooks.py` (add pressure tests)

**Interfaces:**
- Consumes: `lts.config`, `lts.transcript`.
- Produces:
  - `build_context(event: dict, *, root: Path | None = None) -> str` — computes pressure from `event["transcript_path"]`; returns a soft nudge for `"warn"`, a hard nudge for `"force"`, `""` for `"none"`.
  - `run(event, *, root=None) -> dict` — wraps non-empty text as `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": <text>}}`.
  - `main()` — stdin JSON → `run` → stdout JSON.

- [ ] **Step 1: Write the failing test (append to `tests/test_hooks.py`)**

```python
def test_user_prompt_submit_force_nudge(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    t = tmp_path / "t.jsonl"
    t.write_text("x" * (4 * 170_000), encoding="utf-8")  # ~85% of 200k window
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    text = ups.build_context({"transcript_path": str(t)}, root=tmp_path)
    assert "/sleep" in text
    assert "now" in text.lower()


def test_user_prompt_submit_silent_when_low(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    t = tmp_path / "t.jsonl"
    t.write_text("x" * 4000, encoding="utf-8")  # ~1k tokens
    ups = _load("user_prompt_submit", HOOKS / "user_prompt_submit.py")
    assert ups.build_context({"transcript_path": str(t)}, root=tmp_path) == ""
    assert ups.run({"transcript_path": str(t)}, root=tmp_path) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: FAIL — `claude/hooks/user_prompt_submit.py` not found.

- [ ] **Step 3: Write the implementation**

```python
# claude/hooks/user_prompt_submit.py
from __future__ import annotations

import json
import sys
from pathlib import Path

from lts import transcript
from lts.config import load_config

_WARN = (
    "Context is filling up (~60%+). Consider running `/sleep` soon, or at the next "
    "natural break, so nothing is lost."
)
_FORCE = (
    "Context is nearly full (~80%+). You should run `/sleep` now to consolidate this "
    "session into long-term notes before context is compacted."
)


def build_context(event: dict, *, root: Path | None = None) -> str:
    cfg = load_config(root)
    tokens = transcript.estimate_tokens(Path(event.get("transcript_path", "")))
    level = transcript.pressure_level(
        tokens, transcript.DEFAULT_WINDOW, cfg.pressure_warn, cfg.pressure_force
    )
    if level == "force":
        return _FORCE
    if level == "warn":
        return _WARN
    return ""


def run(event: dict, *, root: Path | None = None) -> dict:
    text = build_context(event, root=root)
    if not text:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }


def main() -> None:
    event = json.load(sys.stdin)
    print(json.dumps(run(event)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hooks.py -v`
Expected: PASS (7 passed total in this file).

- [ ] **Step 5: Commit**

```bash
git add claude/hooks/user_prompt_submit.py tests/test_hooks.py
git commit -m "feat: UserPromptSubmit hook injects escalating sleep pressure"
```

---

### Task 12: `/sleep` skill

**Files:**
- Create: `claude/skills/sleep/SKILL.md`

**Interfaces:**
- Consumes: `lts` CLI (`lts stm read/clear`, `lts pending has`), Basic Memory tools (`write_note`, `edit_note`, `search`), `lts anchor`.
- Produces: a skill that performs Flow A (consolidation) from the spec.

> This is a prompt artifact, not code — no TDD cycle. The deliverable is the file; verification is a content review against spec §7 Flow A.

- [ ] **Step 1: Create the skill file**

```markdown
---
name: sleep
description: Consolidate the current session into long-term memory (Basic Memory) without losing any detail, then clear the STM buffer. Use when context is filling up, at the end of a logical chapter, or when prompted by sleep pressure.
---

# /sleep — Consolidate memory (the AI's sleep)

Goal: move everything worth keeping from this session into long-term notes with explicit `[[links]]`, preserving every number, name, and rationale — then free the STM buffer. This is **not** a lossy summary.

Run these steps in order.

## 1. Collect
- Read the curated STM buffer:
  `lts stm read --session "$CLAUDE_SESSION_ID"`
- If a previous session left raw material, also account for it:
  `lts pending has` → if `yes`, read the snapshots under `.ai_memory/pending_consolidation/` with your file tools.
- Add the significant remaining conversation context (exclude noise).

## 2. Extract (with preservation, never compress)
Pull out: key decisions, **ALL numbers**, names, architectural choices, contentious points, open questions. Rule: every number, name, and rationale is carried over **verbatim**. When in doubt, carry it over.

## 3. Write the session note
Use Basic Memory `write_note` (or `edit_note` if today's session note exists) in the `_Session_Memory/` folder, named `Session_<YYYY-MM-DD_HHMM>`. Frontmatter: `type: session`, `date`, `topics`, `kb_refs`, `status: consolidated`. Body is coherent prose under: `## Context`, `## Key decisions`, `## Open questions`, `## Links`.

## 4. Link into the knowledge base
For each distinct topic: `search` the vault. If a `_Knowledge_Base/` note exists, `edit_note` to augment it; otherwise `write_note` a new one. Place `[[links]]` both ways — in the note body **and** in the session note's frontmatter `kb_refs`. Build coherent linked prose, not a bullet list.

## 5. Self-check (anti-loss)
Re-scan the dialogue and STM for key entities (numbers, proper nouns). For each, confirm it appears in a written note. Append anything missing via `edit_note`. Only proceed once nothing is missing.

## 6. Free STM, update the anchor, then clear context
- Update the anchor file `.ai_memory/anchor.json` with the latest `last_session`, `active_topics`, `active_notes` (the notes you just wrote/touched).
- Clear the STM buffer **only after** the writes above succeeded:
  `lts stm clear --session "$CLAUDE_SESSION_ID"`
- If you consumed pending snapshots, delete them after a successful write.
- Then run `/compact` to clear context.

If interrupted before step 6, leave the session note `status: pending` and do **not** clear STM — the next session's SessionStart hook will resume this.
```

- [ ] **Step 2: Verify the skill is well-formed**

Run: `head -5 claude/skills/sleep/SKILL.md`
Expected: shows YAML frontmatter with `name: sleep` and a `description:`.

- [ ] **Step 3: Commit**

```bash
git add claude/skills/sleep/SKILL.md
git commit -m "feat: /sleep skill for lossless consolidation"
```

---

### Task 13: `/recall` skill

**Files:**
- Create: `claude/skills/recall/SKILL.md`

**Interfaces:**
- Consumes: Basic Memory tools (`search`, `build_context`, `read_note`), `lts anchor render`.
- Produces: a skill that performs graph-priority hybrid retrieval (spec §6 / §8).

- [ ] **Step 1: Create the skill file**

```markdown
---
name: recall
description: Retrieve relevant long-term memory for the current topic, prioritizing notes linked from the session anchor over purely semantic matches. Use when you need to remember prior decisions or context.
---

# /recall — Retrieve memory (graph-priority hybrid)

Goal: load only what is relevant, cheaply. Prefer notes directly linked from the anchor/last session over purely semantic hits; traverse links one level only (no recursion); pull full bodies on demand.

## Steps
1. **Anchor first.** Read the anchor entry points:
   `lts anchor render`
   These `active_notes` are the highest-priority candidates.
2. **Semantic candidates.** Run Basic Memory `search` with the user's topic to get semantically similar notes (snippets).
3. **Graph expansion (1 level).** For the anchor notes and the last session note, use `build_context` to pull directly linked neighbors — one level only.
4. **Rank with graph priority.** Order results so notes reachable via `[[links]]` from the anchor/last session rank above purely semantic hits of similar score. De-duplicate.
5. **Lazy bodies.** Present snippets. Only `read_note` the full body for the few notes you actually need to answer — do not load everything (keep memory tokens well under half the window).

If `search` returns nothing (e.g., semantic index degraded), fall back to the graph path (anchor + `build_context`) alone.
```

- [ ] **Step 2: Verify the skill is well-formed**

Run: `head -5 claude/skills/recall/SKILL.md`
Expected: shows YAML frontmatter with `name: recall`.

- [ ] **Step 3: Commit**

```bash
git add claude/skills/recall/SKILL.md
git commit -m "feat: /recall skill for graph-priority hybrid retrieval"
```

---

### Task 14: Vault templates

**Files:**
- Create: `templates/session.md`
- Create: `templates/knowledge.md`

**Interfaces:**
- Consumes: nothing.
- Produces: bootstrap note templates copied into the vault's `_Templates/` by the installer (Task 15).

- [ ] **Step 1: Create `templates/session.md`**

```markdown
---
type: session
date: YYYY-MM-DD HH:MM
topics: []
kb_refs: []
status: pending
project: ""
---

## Context

## Key decisions

## Open questions

## Links
```

- [ ] **Step 2: Create `templates/knowledge.md`**

```markdown
---
tags: []
created: YYYY-MM-DD
last_accessed: YYYY-MM-DD
session_refs: []
---

# Title

## Summary

## Details

## Related
```

- [ ] **Step 3: Commit**

```bash
git add templates/session.md templates/knowledge.md
git commit -m "feat: session and knowledge note templates"
```

---

### Task 15: Installer

**Files:**
- Create: `install.py`
- Create: `config.example.toml`
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: `lts.config`, standard library (`shutil`, `json`).
- Produces:
  - `render_hook_settings(repo_root: Path) -> dict` — returns the `.claude/settings.json` `hooks` block wiring `SessionStart`, `PreCompact`, `UserPromptSubmit` to `python3 <repo>/claude/hooks/<name>.py`. **Pure, tested.**
  - `merge_settings(existing: dict, hooks: dict) -> dict` — merges the hooks block into an existing settings dict without dropping other keys. **Pure, tested.**
  - `basic_memory_available() -> bool` — `shutil.which("basic-memory") is not None`.
  - `main(argv=None) -> int` — orchestration (smoke-level): ensure config, write `.claude/settings.json`, copy skills, create sidecar, print next steps. Not unit-tested beyond smoke.

> The installer wires hooks and copies our skills; creating the Basic Memory project and registering its MCP are printed as explicit next-step commands the user runs (they touch external state). Keep `main()` idempotent.

- [ ] **Step 1: Write `config.example.toml`**

```toml
[vault]
mode = "per_project"        # per_project | global
project = "lts-default"     # Basic Memory project name
# path = "~/ai_memory_vault" # used when creating the project

[sleep]
pressure_warn = 0.60        # UserPromptSubmit: soft reminder threshold (fraction of window)
pressure_force = 0.80       # hard "time to /sleep" threshold
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_install.py
import importlib.util
from pathlib import Path


def _load_install():
    path = Path(__file__).resolve().parents[1] / "install.py"
    spec = importlib.util.spec_from_file_location("install", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_hook_settings_points_to_hooks(tmp_path: Path):
    inst = _load_install()
    settings = inst.render_hook_settings(tmp_path)
    blob = str(settings)
    assert "SessionStart" in settings["hooks"]
    assert "PreCompact" in settings["hooks"]
    assert "UserPromptSubmit" in settings["hooks"]
    assert "session_start.py" in blob
    assert "pre_compact.py" in blob
    assert "user_prompt_submit.py" in blob


def test_merge_settings_preserves_existing(tmp_path: Path):
    inst = _load_install()
    hooks = inst.render_hook_settings(tmp_path)
    merged = inst.merge_settings({"model": "opus", "hooks": {"Stop": []}}, hooks)
    assert merged["model"] == "opus"
    assert "SessionStart" in merged["hooks"]
    assert "Stop" in merged["hooks"]  # existing hook entries kept
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_install.py -v`
Expected: FAIL — `install.py` not found / attributes missing.

- [ ] **Step 4: Write the implementation**

```python
# install.py
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from lts import paths
from lts.config import load_config

HOOK_EVENTS = {
    "SessionStart": "session_start.py",
    "PreCompact": "pre_compact.py",
    "UserPromptSubmit": "user_prompt_submit.py",
}


def render_hook_settings(repo_root: Path) -> dict:
    hooks_dir = repo_root / "claude" / "hooks"
    hooks: dict = {}
    for event, script in HOOK_EVENTS.items():
        hooks[event] = [
            {
                "hooks": [
                    {"type": "command", "command": f"python3 {hooks_dir / script}"}
                ]
            }
        ]
    return {"hooks": hooks}


def merge_settings(existing: dict, hooks: dict) -> dict:
    merged = dict(existing)
    merged_hooks = dict(existing.get("hooks", {}))
    merged_hooks.update(hooks["hooks"])
    merged["hooks"] = merged_hooks
    return merged


def basic_memory_available() -> bool:
    return shutil.which("basic-memory") is not None


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent
    cfg = load_config(repo_root)

    # 1. config.toml
    cfg_file = repo_root / "config.toml"
    if not cfg_file.exists():
        shutil.copy(repo_root / "config.example.toml", cfg_file)
        print(f"created {cfg_file} (edit it to set your vault mode/project)")

    # 2. sidecar
    paths.ensure_sidecar(cfg)
    print(f"sidecar ready at {paths.sidecar_root(cfg)}")

    # 3. .claude/settings.json hooks
    claude_dir = repo_root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_file = claude_dir / "settings.json"
    existing = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    settings = merge_settings(existing, render_hook_settings(repo_root))
    settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"wrote hooks into {settings_file}")

    # 4. skills
    skills_src = repo_root / "claude" / "skills"
    skills_dst = claude_dir / "skills"
    skills_dst.mkdir(exist_ok=True)
    for skill in skills_src.iterdir():
        shutil.copytree(skill, skills_dst / skill.name, dirs_exist_ok=True)
    print(f"installed skills into {skills_dst}")

    # 5. Basic Memory next steps
    if not basic_memory_available():
        print("\n[!] basic-memory not found. Install it:\n    uv tool install basic-memory")
    print(
        "\nNext steps (run manually):\n"
        f"  basic-memory project add {cfg.project} <vault-path>\n"
        "  claude plugin marketplace add basicmachines-co/basic-memory\n"
        "  claude plugin install basic-memory@basicmachines-co\n"
        "Then restart Claude Code and try a /sleep at the end of a session."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_install.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add install.py config.example.toml tests/test_install.py
git commit -m "feat: idempotent installer wiring hooks and skills"
```

---

### Task 16: README + full-suite + Basic Memory smoke

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: user-facing docs; a verified green suite; a manual smoke checklist for the Basic Memory integration (which is out of unit-test scope).

- [ ] **Step 1: Run the full unit suite**

Run: `uv run pytest -q`
Expected: PASS (all tasks green).

- [ ] **Step 2: Write `README.md`**

```markdown
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
```

- [ ] **Step 3: Manual Basic Memory smoke test (record results in the PR description)**

Run these against a real install:
```
1. python install.py
2. basic-memory project add lts-smoke /tmp/lts-smoke-vault
3. In Claude Code: write a note via Basic Memory `write_note`, then `search` for it — confirm it returns.
4. lts stm append --session test --text "smoke fact 12345"
5. lts stm read --session test  -> shows the fact
6. /sleep  -> a Session_* note is created; `search` finds "12345"; lts stm read is now empty
```
Expected: each step succeeds; the number `12345` survives into a note and STM is cleared.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README with install and smoke checklist"
```

---

## Self-Review

**1. Spec coverage**

| Spec element | Task |
|---|---|
| Three tiers / STM buffer (§3) | Tasks 4 (stm), 8 (cli), 12 (sleep uses it) |
| Architecture: layer over Basic Memory, no MCP (§4) | All tasks; consumed via skills |
| Sidecar isolation `.ai_memory/` outside note tree (§4) | Task 3 (paths under project root, gitignored) |
| Vault layout / note format / anchor (§5) | Tasks 5 (anchor), 14 (templates), 12 (session note format) |
| Integration contract / `/recall` ranking (§6) | Task 13 (recall) |
| Flow A SLEEP (§7) | Task 12 |
| Flow B WAKE-UP (§7) | Task 10 (SessionStart) |
| Flow C WORK / on-the-fly linking (§7) | Task 12/13 prompts; STM append via Task 8 |
| Retrieval delegated to Basic Memory (§8) | Tasks 12/13 use `search`/`build_context` |
| Distribution / install / config (§9) | Tasks 1, 15, 16 |
| Error handling: PreCompact backstop, STM-not-cleared-until-success, pending fallback (§10) | Tasks 9, 12 (prompt), 6 |
| Sleep homeostasis triggers (§2, §7) | Tasks 9, 10, 11 |
| Testing by layers (§11) | Tasks 2–11, 15 (pytest); 16 (smoke) |

No uncovered spec requirement found.

**2. Placeholder scan**

The `YYYY-MM-DD` strings in `templates/session.md`/`knowledge.md` (Task 14) are intentional template fill-ins for the note author, not plan placeholders. No `TBD`/`TODO`/"implement later" in any step. All code steps include full code.

**3. Type consistency**

- `load_config(start)` returns `Config(project_root, vault_mode, project, vault_path, pressure_warn, pressure_force)` — used consistently in paths (Task 3), cli (Task 8), hooks (Tasks 9–11), install (Task 15).
- Path helpers `stm_file`/`anchor_file`/`pending_dir`/`sidecar_root`/`ensure_sidecar` — same signatures across Tasks 3, 8, 9, 10, 11, 15.
- `stm.append/read/clear/is_empty`, `anchor.read_anchor/write_anchor/render_anchor`, `pending.dump_snapshot/has_pending/list_snapshots/clear_all`, `transcript.estimate_tokens/pressure_level/DEFAULT_WINDOW` — defined in Tasks 4–7, consumed identically later.
- Hook output shape `{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}` — identical in Tasks 10 and 11.

No inconsistencies found.
```