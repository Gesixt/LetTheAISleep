import json
from pathlib import Path

import pytest

from lts import sync
from lts.config import NotAnLtsProject, load_config
from tests.helpers import make_project

REPO = Path(__file__).resolve().parents[1]


def _fake_source(root: Path, *, skill_body: str = "current sleep skill\n") -> Path:
    """A stand-in for the cloned repo: the files an install copies out of it."""
    (root / "claude" / "hooks").mkdir(parents=True)
    for script in sync.HOOK_EVENTS.values():
        (root / "claude" / "hooks" / script).write_text("# hook\n", encoding="utf-8")
    (root / "claude" / "skills" / "sleep").mkdir(parents=True)
    (root / "claude" / "skills" / "sleep" / "SKILL.md").write_text(skill_body, encoding="utf-8")
    return root


def _installed_target(root: Path, *, old_source: Path) -> Path:
    """A project installed earlier, against an older copy of the source."""
    make_project(root)
    claude = root / ".claude"
    (claude / "skills" / "sleep").mkdir(parents=True)
    (claude / "skills" / "sleep" / "SKILL.md").write_text("stale sleep skill\n", encoding="utf-8")
    (claude / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(git add *)"]},
        "hooks": sync.render_hook_settings(old_source)["hooks"],
    }, indent=2), encoding="utf-8")
    (root / "CLAUDE.md").write_text(
        "# Project\n\nHand-written notes.\n\n" + sync.memory_instruction_block() + "\n",
        encoding="utf-8",
    )
    return root


def test_sync_refreshes_the_skill_copies(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    sync.sync(source, load_config(target))
    body = (target / ".claude" / "skills" / "sleep" / "SKILL.md").read_text(encoding="utf-8")
    assert body == "current sleep skill\n"


def test_sync_rewires_the_hooks_at_the_current_source(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old-clone")
    sync.sync(source, load_config(target))
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert str(source) in command
    assert "old-clone" not in command


def test_sync_keeps_settings_it_does_not_own(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    sync.sync(source, load_config(target))
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"] == {"allow": ["Bash(git add *)"]}


def test_sync_refreshes_the_memory_block_and_keeps_the_rest_of_claude_md(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    md = target / "CLAUDE.md"
    md.write_text(md.read_text(encoding="utf-8").replace("Recall before re-deriving", "OUTDATED"),
                  encoding="utf-8")
    sync.sync(source, load_config(target))
    text = md.read_text(encoding="utf-8")
    assert "Hand-written notes." in text
    assert "Recall before re-deriving" in text
    assert "OUTDATED" not in text
    assert text.count(sync._MEM_START) == 1


def test_sync_never_touches_config_or_the_sidecar(tmp_path: Path):
    # An update refreshes what the source ships. The project name, the author, the buffer and
    # the vault are the developer's state and a re-install is the only thing allowed near them.
    from lts import paths, stm
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    cfg = load_config(target)
    paths.ensure_sidecar(cfg)
    stm.append(paths.stm_file(cfg), "[user] a fact worth keeping")
    before = (target / "config.toml").read_text(encoding="utf-8")

    sync.sync(source, cfg)

    assert (target / "config.toml").read_text(encoding="utf-8") == before
    assert "a fact worth keeping" in stm.read(paths.stm_file(cfg))


def test_sync_refuses_a_target_that_was_never_installed(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    stray = tmp_path / "not-a-project"
    stray.mkdir()
    with pytest.raises(NotAnLtsProject):
        sync.sync(source, load_config(stray))
    assert not (stray / ".claude").exists()


def test_check_reports_what_is_stale_without_writing(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    report = sync.sync(source, load_config(target), check=True)
    assert report["stale"] is True
    assert {a["name"] for a in report["artifacts"] if a["stale"]} >= {"skills/sleep", "hooks"}
    assert (target / ".claude" / "skills" / "sleep" / "SKILL.md").read_text(
        encoding="utf-8") == "stale sleep skill\n"


def test_sync_reports_nothing_stale_once_it_has_run(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    sync.sync(source, load_config(target))
    report = sync.sync(source, load_config(target), check=True)
    assert report["stale"] is False
    assert all(not a["stale"] for a in report["artifacts"])


def test_sync_leaves_a_projects_own_extra_skill_alone(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    mine = target / ".claude" / "skills" / "deploy"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my own skill\n", encoding="utf-8")
    sync.sync(source, load_config(target))
    assert (mine / "SKILL.md").read_text(encoding="utf-8") == "my own skill\n"


def test_source_root_is_the_repo_the_running_lts_came_from():
    assert sync.source_root() == REPO
    assert (sync.source_root() / "claude" / "hooks" / "stop.py").exists()


def test_source_root_rejects_a_directory_without_the_hook_scripts(tmp_path: Path):
    with pytest.raises(sync.NotASource):
        sync.source_root(tmp_path)


def test_report_names_the_source_so_the_clone_in_use_is_never_a_guess(tmp_path: Path):
    source = _fake_source(tmp_path / "source")
    target = _installed_target(tmp_path / "target", old_source=tmp_path / "old")
    text = sync.render(sync.sync(source, load_config(target), check=True))
    assert str(source) in text
    assert str(target) in text
