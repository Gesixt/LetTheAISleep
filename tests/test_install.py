import importlib.util
import json
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


def test_upsert_memory_instructions_appends_then_idempotent():
    inst = _load_install()
    base = "# My Project\n\nExisting guidance.\n"
    once = inst.upsert_memory_instructions(base)
    assert "Let The AI Sleep" in once
    assert "Existing guidance." in once          # original content preserved
    assert "lts stm append" in once
    twice = inst.upsert_memory_instructions(once)
    assert twice == once                          # idempotent
    assert twice.count("lts:memory-instructions:start") == 1


def test_main_installs_into_target_project(tmp_path: Path):
    inst = _load_install()
    target = tmp_path / "proj"
    target.mkdir()
    (target / ".git").mkdir()

    rc = inst.main(["--project", "demo", "--target", str(target)])
    assert rc == 0

    # config + sidecar + skills land in the TARGET project, not the source repo
    assert (target / "config.toml").exists()
    assert 'project = "demo"' in (target / "config.toml").read_text(encoding="utf-8")
    assert (target / ".ai_memory" / "stm").is_dir()
    assert (target / ".claude" / "skills" / "sleep").exists()

    # CLAUDE.md memory instructions written into the target
    claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
    assert "lts:memory-instructions:start" in claude_md
    assert "lts stm append" in claude_md

    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "SessionStart" in settings["hooks"]
    # hook command points at the SOURCE repo's hook script (where install.py lives)
    source_root = Path(inst.__file__).resolve().parent
    cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert str(source_root / "claude" / "hooks" / "session_start.py") in cmd


def test_target_is_the_root_verbatim_even_under_an_existing_project(tmp_path: Path):
    inst = _load_install()
    parent = tmp_path / "monorepo"
    parent.mkdir()
    (parent / "config.toml").write_text('[vault]\nproject = "parent"\n', encoding="utf-8")
    target = parent / "service"
    target.mkdir()

    assert inst.main(["--project", "service-mem", "--target", str(target)]) == 0

    # --target must not walk up and attach memory to the ancestor project
    assert (target / "config.toml").exists()
    assert (target / ".ai_memory" / "stm").is_dir()
    assert 'project = "service-mem"' in (target / "config.toml").read_text(encoding="utf-8")
    assert 'project = "parent"' in (parent / "config.toml").read_text(encoding="utf-8")


def test_set_vault_key_replaces_a_live_line():
    inst = _load_install()
    text = '[vault]\nproject = "p"\nauthor = "old"\n\n[sleep]\npressure_warn = 0.6\n'
    out = inst.set_vault_key(text, "author", "dmitrii")
    assert 'author = "dmitrii"' in out
    assert '"old"' not in out
    assert "pressure_warn = 0.6" in out


def test_set_vault_key_replaces_a_commented_line():
    inst = _load_install()
    text = '[vault]\nproject = "p"\n# path = "~/ai_memory_vault"\n\n[sleep]\n'
    out = inst.set_vault_key(text, "path", "/srv/vault")
    assert 'path = "/srv/vault"' in out
    assert "# path" not in out


def test_set_vault_key_appends_when_absent():
    inst = _load_install()
    text = '[vault]\nproject = "p"\n\n[sleep]\npressure_warn = 0.6\n'
    out = inst.set_vault_key(text, "author", "dmitrii")
    assert 'author = "dmitrii"' in out
    # the key lands inside [vault], not in [sleep]
    assert out.index('author = "dmitrii"') < out.index("[sleep]")


def test_resolve_author_prefers_the_argument():
    inst = _load_install()
    import argparse
    args = argparse.Namespace(author="vincent")
    assert inst.resolve_author(args, None) == "vincent"


def test_resolve_author_rejects_a_non_slug():
    inst = _load_install()
    import argparse
    args = argparse.Namespace(author="Dmitry Mitin")
    import pytest
    from lts.naming import InvalidAuthor
    with pytest.raises(InvalidAuthor):
        inst.resolve_author(args, None)


def test_resolve_author_keeps_existing_when_not_a_tty(monkeypatch):
    inst = _load_install()
    import argparse
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    args = argparse.Namespace(author=None)
    assert inst.resolve_author(args, "dmitrii") == "dmitrii"
    assert inst.resolve_author(args, None) is None


def test_main_writes_the_author_and_leaves_an_existing_vault_untouched(tmp_path: Path):
    inst = _load_install()
    target = tmp_path / "proj"
    target.mkdir()
    vault_note = target / ".ai_vault" / "knowledge-base" / "Cart Service.md"
    vault_note.parent.mkdir(parents=True)
    vault_note.write_text("petr wrote this", encoding="utf-8")

    rc = inst.main(["--project", "demo", "--target", str(target), "--author", "dmitrii"])
    assert rc == 0

    cfg_text = (target / "config.toml").read_text(encoding="utf-8")
    assert 'author = "dmitrii"' in cfg_text
    # installation must never touch a vault a teammate cloned
    assert vault_note.read_text(encoding="utf-8") == "petr wrote this"


def test_main_rejects_a_non_slug_author(tmp_path: Path, capsys):
    inst = _load_install()
    target = tmp_path / "proj"
    target.mkdir()
    rc = inst.main(["--project", "demo", "--target", str(target), "--author", "Dmitry Mitin"])
    assert rc == 2
    assert "dmitry-mitin" in capsys.readouterr().err


def test_main_without_author_stays_single_developer(tmp_path: Path, monkeypatch):
    inst = _load_install()
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    target = tmp_path / "proj"
    target.mkdir()
    assert inst.main(["--project", "demo", "--target", str(target)]) == 0
    # Parse the TOML instead of doing a substring check: the template ships a
    # commented-out `# author = ...` line to document the option for team mode,
    # so the literal substring "author" is present by design. What must be
    # absent is a *live* key in the parsed [vault] table, not the word itself.
    import tomllib
    data = tomllib.loads((target / "config.toml").read_text(encoding="utf-8"))
    assert "author" not in data["vault"]


def test_resolve_author_interactive_empty_answer_with_suggestion_stays_solo(monkeypatch):
    # The regression guard: a git-derived suggestion must be *offered*, never auto-accepted.
    # Without the git_identity_suggestion monkeypatch this test could pass merely because the
    # machine running it has no `git config user.name` configured.
    inst = _load_install()
    import argparse
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(inst, "git_identity_suggestion", lambda: "dmitry-mitin")

    seen_prompts = []

    def fake_input(prompt=""):
        seen_prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    args = argparse.Namespace(author=None)

    result = inst.resolve_author(args, None)

    assert result is None  # NOT the suggestion — empty answer means single developer
    assert len(seen_prompts) == 1
    assert "dmitry-mitin" in seen_prompts[0]
    assert "single developer" in seen_prompts[0]


def test_resolve_author_interactive_empty_answer_keeps_existing(monkeypatch):
    inst = _load_install()
    import argparse
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    args = argparse.Namespace(author=None)

    assert inst.resolve_author(args, "dmitrii") == "dmitrii"


def test_resolve_author_interactive_typed_answer_is_validated_and_returned(monkeypatch):
    inst = _load_install()
    import argparse
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "vincent")
    args = argparse.Namespace(author=None)

    assert inst.resolve_author(args, None) == "vincent"


def test_resolve_author_interactive_typed_non_slug_answer_raises(monkeypatch):
    inst = _load_install()
    import argparse
    import sys
    import pytest
    from lts.naming import InvalidAuthor
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "Dmitry Mitin")
    args = argparse.Namespace(author=None)

    with pytest.raises(InvalidAuthor):
        inst.resolve_author(args, None)


def test_set_vault_key_appends_when_vault_is_the_last_table():
    inst = _load_install()
    import tomllib
    text = '[sleep]\npressure_warn = 0.6\n\n[vault]\nproject = "p"\n'
    out = inst.set_vault_key(text, "author", "dmitrii")
    parsed = tomllib.loads(out)
    assert parsed["vault"]["author"] == "dmitrii"
    assert parsed["sleep"]["pressure_warn"] == 0.6
    # the key must land inside [vault], after the vault header
    assert out.index("[vault]") < out.index('author = "dmitrii"')


def test_set_vault_key_last_table_with_trailing_blank_lines():
    inst = _load_install()
    import tomllib
    text = '[vault]\nproject = "p"\n\n\n'
    out = inst.set_vault_key(text, "author", "dmitrii")
    parsed = tomllib.loads(out)
    assert parsed["vault"]["author"] == "dmitrii"
    # the new key must land before the trailing blank lines, not after them
    author_pos = out.index('author = "dmitrii"')
    trailing = out[author_pos + len('author = "dmitrii"'):]
    assert trailing.strip("\n") == ""  # only blank lines follow the inserted key


def test_set_vault_key_last_table_no_trailing_newline():
    inst = _load_install()
    import tomllib
    text = '[vault]\nproject = "p"'
    out = inst.set_vault_key(text, "author", "dmitrii")
    parsed = tomllib.loads(out)
    assert parsed["vault"]["author"] == "dmitrii"
    assert out.endswith("\n")  # well-formed: file ends with a newline
