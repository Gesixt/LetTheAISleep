from pathlib import Path

_BASE = '[vault]\nproject = "test-project"\n'


def make_project(root: Path, extra: str = "") -> Path:
    """Mark `root` as an lts project root — a config.toml with a [vault] section."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.toml").write_text(_BASE + extra, encoding="utf-8")
    return root