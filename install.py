from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from lts import naming, paths, sync
from lts.config import load_config
from lts.naming import InvalidAuthor
from lts.slug import slugify_project

# The copies an install lays down are owned by `lts.sync`, so `lts update` can refresh them
# later without re-running this script. Re-exported here: install.py is the documented entry
# point and its callers address these names directly.
HOOK_EVENTS = sync.HOOK_EVENTS
render_hook_settings = sync.render_hook_settings
merge_settings = sync.merge_settings


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


_MEM_START = sync._MEM_START
_MEM_END = sync._MEM_END
memory_instruction_block = sync.memory_instruction_block
upsert_memory_instructions = sync.upsert_memory_instructions


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

    # 3. the copies: hook wiring in .claude/settings.json, the skills, and the CLAUDE.md
    #    memory block. `lts update` refreshes exactly these later, from the same code.
    print(sync.render(sync.sync(source_root, cfg)))

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
