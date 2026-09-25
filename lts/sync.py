"""Refresh what an install copied into a project, without re-running the install.

Two different things live under the name "installed". The **hook scripts and the `lts` CLI**
stay in the cloned repo and are only *referenced* from a project, so updating them is a
`git pull` in that clone. The **skills, the hook wiring in `.claude/settings.json` and the
memory block in `CLAUDE.md`** are *copies*, and a copy goes stale silently — which is how a
project can keep running last month's `/sleep` long after the clone was updated.

This module owns those copies, for both `install.py` (first time) and `lts update` (every
time after). It deliberately never touches `config.toml`, `.ai_memory/` or the vault: those
hold the developer's own state, and an update must be safe to run at any moment.
"""

from __future__ import annotations

import filecmp
import json
import shutil
from pathlib import Path

from lts import vaultgit
from lts.config import Config, require_project

HOOK_EVENTS = {
    "SessionStart": "session_start.py",
    "PreCompact": "pre_compact.py",
    "UserPromptSubmit": "user_prompt_submit.py",
    "Stop": "stop.py",
}


class NotASource(RuntimeError):
    """Raised when a directory does not hold the hook scripts an install copies from."""

    def __init__(self, root: Path) -> None:
        super().__init__(
            f"{root} is not a Let The AI Sleep clone (no claude/hooks/stop.py). "
            "Run `lts update --source <path-to-clone>`, or run install.py from the clone."
        )


def source_root(explicit: Path | str | None = None) -> Path:
    """The clone to copy from: `explicit`, else wherever the running `lts` was imported from.

    Deriving it from the package is what makes `lts update` honest — a machine can hold both a
    development clone and a deployed one, and the answer to "which one is wired in?" is
    whichever one this process is running out of, never whichever one you are standing in.
    """
    root = Path(explicit).expanduser().resolve() if explicit else Path(__file__).resolve().parents[1]
    if not (root / "claude" / "hooks" / "stop.py").is_file():
        raise NotASource(root)
    return root


def render_hook_settings(repo_root: Path) -> dict:
    hooks_dir = Path(repo_root) / "claude" / "hooks"
    hooks: dict = {}
    for event, script in HOOK_EVENTS.items():
        hooks[event] = [
            {
                "hooks": [
                    {"type": "command", "command": f'python3 "{hooks_dir / script}"'}
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


_MEM_START = "<!-- lts:memory-instructions:start -->"
_MEM_END = "<!-- lts:memory-instructions:end -->"


def memory_instruction_block() -> str:
    return f"""{_MEM_START}
## Memory (Let The AI Sleep)

This project has hybrid memory via the `lts` CLI + the Basic Memory MCP. Use it proactively —
do not let findings evaporate:

- **STM fills automatically.** A `Stop` hook captures each exchange into the short-term buffer
  after every turn — you do not need to remember to record things. (To add a deliberate highlight
  you may still `lts stm append --text "..."`, but it is optional.)
- **Sleep at the end of a chapter.** When a task or investigation wraps up (or context is
  filling), run the `/sleep` skill to consolidate the STM buffer + the conversation into linked
  long-term notes; it then clears STM. Offer `/sleep` before the user moves on.
- **Recall before re-deriving.** Every turn carries a **memory map** — the titles of every note
  in the vault. If a question touches anything the map names, read that note instead of working
  it out again; `/recall` when you need ranking and linked neighbours, `read_note` when a title
  is an obvious match. Re-deriving something already written down is the failure this exists to
  prevent.
- **Check load** any time with `/memory-status`.

The Basic Memory project name is in `config.toml` (`[vault] project`); the skills pass it to
Basic Memory automatically.
{_MEM_END}"""


def upsert_memory_instructions(existing_text: str) -> str:
    """Insert or refresh the memory-instructions block (between markers) without duplicating it."""
    block = memory_instruction_block()
    if _MEM_START in existing_text and _MEM_END in existing_text:
        start = existing_text.index(_MEM_START)
        end = existing_text.index(_MEM_END) + len(_MEM_END)
        return existing_text[:start] + block + existing_text[end:]
    sep = "" if existing_text == "" else ("\n" if existing_text.endswith("\n") else "\n\n")
    return existing_text + sep + block + "\n"


def _tree_differs(src: Path, dst: Path) -> bool:
    """True when any file under `src` is missing from `dst` or differs from it.

    One-directional on purpose: files the project added of its own are not our business.
    """
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        mirror = dst / item.relative_to(src)
        if not mirror.is_file() or not filecmp.cmp(item, mirror, shallow=False):
            return True
    return False


def _source_git(source: Path) -> dict | None:
    """Read-only git state of the clone, so a stale checkout is visible without a network call."""
    if not vaultgit.is_git_repo(source):
        return None
    counts = vaultgit.ahead_behind(source)
    return {
        "branch": vaultgit.head_ref(source),
        "sha": vaultgit.head_sha(source),
        "upstream": vaultgit.upstream_name(source),
        "behind": counts[1] if counts else None,
        "dirty": vaultgit.dirty_count(source),
    }


def sync(source: Path, cfg: Config, *, check: bool = False) -> dict:
    """Bring the target project's copies back in step with `source`.

    With `check=True` nothing is written and the report says what would change.
    """
    require_project(cfg)
    source = Path(source)
    target = cfg.project_root
    claude_dir = target / ".claude"
    artifacts: list[dict] = []

    # 1. hook wiring — the commands point back at the source clone, so a moved or replaced
    #    clone is repaired here rather than failing silently at the next turn.
    settings_file = claude_dir / "settings.json"
    existing = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    merged = merge_settings(existing, render_hook_settings(source))
    artifacts.append({
        "name": "hooks", "path": str(settings_file), "stale": merged != existing,
    })

    # 2. skills — one entry per skill the source ships, so the report says which one moved.
    skills_src = source / "claude" / "skills"
    skills_dst = claude_dir / "skills"
    skills = sorted(p for p in skills_src.iterdir() if p.is_dir()) if skills_src.is_dir() else []
    for skill in skills:
        artifacts.append({
            "name": f"skills/{skill.name}",
            "path": str(skills_dst / skill.name),
            "stale": _tree_differs(skill, skills_dst / skill.name),
        })

    # 3. the memory block in CLAUDE.md, between its markers; the rest of the file is the user's.
    claude_md = target / "CLAUDE.md"
    md_before = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    md_after = upsert_memory_instructions(md_before)
    artifacts.append({
        "name": "CLAUDE.md", "path": str(claude_md), "stale": md_after != md_before,
    })

    if not check:
        claude_dir.mkdir(exist_ok=True)
        settings_file.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        if skills:
            skills_dst.mkdir(parents=True, exist_ok=True)
            for skill in skills:
                shutil.copytree(skill, skills_dst / skill.name, dirs_exist_ok=True)
        claude_md.write_text(md_after, encoding="utf-8")

    return {
        "source": str(source),
        "target": str(target),
        "check": check,
        "artifacts": artifacts,
        "stale": any(a["stale"] for a in artifacts),
        "source_git": _source_git(source),
    }


def render(report: dict) -> str:
    verb = "would update" if report["check"] else "updated"
    lines = [
        f"source: {report['source']}",
        f"target: {report['target']}",
    ]
    git = report.get("source_git")
    if git:
        head = f"  {git['branch'] or 'detached'} @ {git['sha'] or '?'}"
        if git.get("dirty"):
            head += f", {git['dirty']} uncommitted file(s)"
        lines.append(head)
        if git.get("behind"):
            lines.append(
                f"  ⚠ {git['behind']} commit(s) behind {git['upstream']} as of the last fetch — "
                f"the hook scripts and the `lts` CLI live here, so update the clone first:\n"
                f"    git -C {report['source']} pull"
            )
    for a in report["artifacts"]:
        lines.append(f"  {verb if a['stale'] else 'up to date':>12}  {a['name']}")
    if not report["stale"]:
        lines.append("Everything is in step with the source clone.")
    return "\n".join(lines)
