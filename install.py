from __future__ import annotations

import argparse
import json
import re
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


def set_config_project(toml_text: str, project: str) -> str:
    """Replace the first `project = "..."` line in a config.toml with the given project name."""
    return re.sub(
        r'(?m)^(\s*project\s*=\s*).*$',
        lambda m: f'{m.group(1)}"{project}"',
        toml_text,
        count=1,
    )


def resolve_project(args: argparse.Namespace, fallback: str) -> str:
    """Project name from --project, else an interactive prompt (tty), else the fallback."""
    if getattr(args, "project", None):
        return args.project
    if sys.stdin.isatty():
        entered = input(f"Basic Memory project name [{fallback}]: ").strip()
        return entered or fallback
    return fallback


_MEM_START = "<!-- lts:memory-instructions:start -->"
_MEM_END = "<!-- lts:memory-instructions:end -->"


def memory_instruction_block() -> str:
    return f"""{_MEM_START}
## Memory (Let The AI Sleep)

This project has hybrid memory via the `lts` CLI + the Basic Memory MCP. Use it proactively —
do not let findings evaporate:

- **Capture as you work.** When a notable fact, decision, number, component/file name, or
  architectural insight comes up, append a one-line note to the STM buffer:
  `lts stm append --text "..."`. It is cheap and survives `/compact`.
- **Sleep at the end of a chapter.** When a task or investigation wraps up (or context is
  filling), run the `/sleep` skill to consolidate the STM buffer + the conversation into linked
  long-term notes; it then clears STM. Offer `/sleep` before the user moves on.
- **Recall before re-deriving.** When a question touches earlier work, use `/recall` first.
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


def basic_memory_available() -> bool:
    return shutil.which("basic-memory") is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Let The AI Sleep into a project.")
    parser.add_argument(
        "--project",
        default=None,
        help="Basic Memory project name to write into config.toml (otherwise prompted).",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Project directory to attach memory to (default: current directory).",
    )
    args = parser.parse_args(argv)

    # The SOURCE is this cloned repo (where the hook scripts and skills live);
    # the TARGET is the project that gets memory wired into it.
    source_root = Path(__file__).resolve().parent
    target_start = Path(args.target).resolve() if args.target else Path.cwd()
    cfg = load_config(target_start)
    target_root = cfg.project_root
    print(f"source: {source_root}\ntarget: {target_root}")

    # 1. config.toml in the target project — create from example, then set the project name
    cfg_file = target_root / "config.toml"
    if not cfg_file.exists():
        shutil.copy(source_root / "config.example.toml", cfg_file)
        print(f"created {cfg_file}")
    project = resolve_project(args, cfg.project)
    cfg_file.write_text(set_config_project(cfg_file.read_text(encoding="utf-8"), project), encoding="utf-8")
    print(f"config.toml project set to '{project}'")

    # 2. sidecar in the target project
    paths.ensure_sidecar(cfg)
    print(f"sidecar ready at {paths.sidecar_root(cfg)}")

    # 3. .claude/settings.json hooks in the target — commands point back at the SOURCE scripts
    claude_dir = target_root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_file = claude_dir / "settings.json"
    existing = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    settings = merge_settings(existing, render_hook_settings(source_root))
    settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"wrote hooks into {settings_file}")

    # 4. skills copied from the SOURCE into the target's .claude/skills
    skills_src = source_root / "claude" / "skills"
    skills_dst = claude_dir / "skills"
    skills_dst.mkdir(exist_ok=True)
    if skills_src.exists():
        for skill in skills_src.iterdir():
            shutil.copytree(skill, skills_dst / skill.name, dirs_exist_ok=True)
    print(f"installed skills into {skills_dst}")

    # 4b. CLAUDE.md memory instructions in the target (so Claude captures memory proactively)
    claude_md = target_root / "CLAUDE.md"
    existing_md = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    claude_md.write_text(upsert_memory_instructions(existing_md), encoding="utf-8")
    print(f"memory instructions written into {claude_md}")

    # 5. Basic Memory next steps (project name already written to config.toml)
    if not basic_memory_available():
        print("\n[!] basic-memory not found. Install it:\n    uv tool install basic-memory")
    print(
        "\nNext steps (run from the target project directory):\n"
        f"  cd {target_root}\n"
        f"  basic-memory project add {project} <vault-path>\n"
        f"  basic-memory reindex --embeddings -p {project}   # build vector index (writes don't auto-embed)\n"
        "  claude mcp add basic-memory -- basic-memory mcp\n"
        "Then restart Claude Code in this project and try a /sleep at the end of a session.\n"
        "Tip: add .claude/settings.json, .ai_memory/ and config.toml to the project's .gitignore."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
