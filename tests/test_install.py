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
