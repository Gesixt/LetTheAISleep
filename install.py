from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from lts import naming, paths
from lts.config import load_config
from lts.naming import InvalidAuthor
from lts.slug import slugify_project

HOOK_EVENTS = {
    "SessionStart": "session_start.py",
    "PreCompact": "pre_compact.py",
    "UserPromptSubmit": "user_prompt_submit.py",
    "Stop": "stop.py",
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


_VAULT_HEADER = re.compile(r"(?m)^\s*\[vault\]\s*$")


def set_vault_key(toml_text: str, key: str, value: str) -> str:
    """Set `key = "value"` inside the [vault] table, replacing a live or commented-out line.

    The template ships `# path = "~/ai_memory_vault"` commented out, so a plain
    "append if missing" would leave a stale comment next to the real value.
    """
    lines = toml_text.splitlines()
    start = next((i for i, ln in enumerate(lines) if _VAULT_HEADER.match(ln)), None)
    if start is None:
        return toml_text.rstrip("\n") + f'\n\n[vault]\n{key} = "{value}"\n'

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            break

    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            lines[i] = f'{key} = "{value}"'
            return "\n".join(lines) + "\n"

    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f'{key} = "{value}"')
    return "\n".join(lines) + "\n"


def git_identity_suggestion() -> str | None:
    """A slug-stable namespace guess from `git config user.name`, or None."""
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return slugify_project(proc.stdout.strip()) or None


def resolve_author(args: argparse.Namespace, existing: str | None) -> str | None:
    """Personal namespace from --author, else an interactive prompt, else what is already set.

    An empty answer means single-developer mode when nothing is configured yet, and "keep what
    you have" when it is. The git-derived value is only ever a *suggestion*: pressing Enter must
    not silently opt a solo developer into team mode.
    """
    if getattr(args, "author", None):
        return naming.validate_author(args.author)
    if not sys.stdin.isatty():
        return existing

    if existing:
        hint = f"[{existing}]"
    else:
        suggestion = git_identity_suggestion()
        hint = f"suggested: {suggestion}; " if suggestion else ""
        hint = f"({hint}empty = single developer)"
    entered = input(f"Personal namespace for team mode {hint}: ").strip()
    if not entered:
        return existing
    return naming.validate_author(entered)


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
    parser.add_argument(
        "--author",
        default=None,
        help="Personal namespace for team mode (slug-stable, e.g. dmitrii). Omit for single-developer mode.",
    )
    parser.add_argument(
        "--vault-path",
        default=None,
        help="Vault directory (default: <target>/.ai_vault). Never created or modified by install.",
    )
    args = parser.parse_args(argv)

    # The SOURCE is this cloned repo (where the hook scripts and skills live);
    # the TARGET is the project that gets memory wired into it. The target directory
    # becomes the root verbatim — we never walk up looking for an existing config.toml,
    # or `--target` would silently attach memory to some ancestor instead.
    source_root = Path(__file__).resolve().parent
    target_root = Path(args.target).resolve() if args.target else Path.cwd()
    print(f"source: {source_root}\ntarget: {target_root}")

    # 1. config.toml in the target project — create from example, then set project/author/path
    cfg_file = target_root / "config.toml"
    if not cfg_file.exists():
        shutil.copy(source_root / "config.example.toml", cfg_file)
        print(f"created {cfg_file}")

    existing = load_config(target_root)
    project = resolve_project(args, existing.project)
    try:
        author = resolve_author(args, existing.author)
    except InvalidAuthor as exc:
        print(f"install: {exc}", file=sys.stderr)
        return 2

    text = set_config_project(cfg_file.read_text(encoding="utf-8"), project)
    if args.vault_path:
        text = set_vault_key(text, "path", str(Path(args.vault_path).expanduser()))
    if author:
        text = set_vault_key(text, "author", author)
    cfg_file.write_text(text, encoding="utf-8")
    print(f"config.toml project set to '{project}'")
    if author:
        print(f"team mode: personal namespace '{author}'")

    # 2. sidecar in the target project (config.toml now exists, so this root resolves)
    cfg = load_config(target_root)
    paths.ensure_sidecar(cfg)
    print(f"sidecar ready at {paths.sidecar_root(cfg)}")

    # 2b. the vault is never created or modified here — it is cloned by the developer
    vault = paths.vault_root(cfg)
    if vault.is_dir():
        print(f"vault found at {vault} (left untouched)")
    elif author:
        print(f"\n[!] no vault at {vault}. In team mode, clone it before use:\n"
              f"    git clone <vault-repo> {vault}")

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
        "Tip: add .claude/settings.json, .ai_memory/ and config.toml to the project's .gitignore.\n"
        "Note: config.toml marks the project root — moving it detaches memory. Check with `lts doctor`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
