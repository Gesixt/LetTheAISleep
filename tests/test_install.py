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


def test_set_config_project_replaces_line():
    inst = _load_install()
    template = (
        '[vault]\n'
        'mode = "per_project"\n'
        'project = "lts-default"\n'
        '# path = "~/ai_memory_vault"\n'
        '[sleep]\n'
        'pressure_warn = 0.60\n'
    )
    out = inst.set_config_project(template, "my-memory")
    assert 'project = "my-memory"' in out
    assert "lts-default" not in out
    # only the project line changes; other lines preserved
    assert 'mode = "per_project"' in out
    assert "pressure_warn = 0.60" in out


def test_resolve_project_prefers_arg():
    inst = _load_install()
    import argparse
    args = argparse.Namespace(project="explicit-name")
    assert inst.resolve_project(args, "fallback") == "explicit-name"


def test_resolve_project_falls_back_when_no_arg_no_tty(monkeypatch):
    inst = _load_install()
    import argparse
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    args = argparse.Namespace(project=None)
    assert inst.resolve_project(args, "fallback") == "fallback"
