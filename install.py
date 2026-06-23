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
    if skills_src.exists():
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
