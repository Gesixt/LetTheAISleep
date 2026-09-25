import pytest

from pathlib import Path
from lts.config import (
    DEFAULTS,
    NotAnLtsProject,
    find_project_root,
    is_lts_config,
    load_config,
    require_project,
)
from tests.helpers import make_project


def test_defaults_when_no_config(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.configured is False
    assert cfg.vault_mode == DEFAULTS["vault_mode"]
    assert cfg.pressure_warn == 0.60
    assert cfg.pressure_force == 0.80
    assert cfg.project_root == tmp_path
    assert cfg.project == DEFAULTS["project"]
    assert cfg.vault_path is None


def test_context_window_default_and_override(tmp_path: Path):
    assert load_config(tmp_path).context_window == 1_000_000
    make_project(tmp_path, "[sleep]\ncontext_window = 200000\n")
    assert load_config(tmp_path).context_window == 200_000


def test_reads_config_toml(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[vault]\nmode = "global"\nproject = "lts-demo"\npath = "/some/vault"\n'
        '[sleep]\npressure_warn = 0.5\npressure_force = 0.75\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.configured is True
    assert cfg.vault_mode == "global"
    assert cfg.project == "lts-demo"
    assert cfg.vault_path == "/some/vault"
    assert cfg.pressure_warn == 0.5
    assert cfg.pressure_force == 0.75


def test_find_project_root_walks_up_to_config(tmp_path: Path):
    make_project(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path


def test_find_project_root_ignores_nested_git(tmp_path: Path):
    # multi-repo layout: project root has config.toml; a nested service is its own git repo
    make_project(tmp_path)
    service = tmp_path / "services" / "cart"
    service.mkdir(parents=True)
    (service / ".git").mkdir()
    # .git in the sub-repo must NOT stop resolution short of the config.toml root
    assert find_project_root(service) == tmp_path


def test_find_project_root_no_marker_returns_none(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    # no config.toml anywhere -> no root at all (never silently fall back to the cwd)
    assert find_project_root(nested) is None


def test_foreign_config_toml_does_not_mark_a_root(tmp_path: Path):
    # a Hugo/Zola/mdBook/.cargo config.toml in a subdirectory must not hijack the root
    make_project(tmp_path)
    service = tmp_path / "site"
    service.mkdir()
    (service / "config.toml").write_text('baseURL = "https://example.com"\n', encoding="utf-8")
    assert find_project_root(service) == tmp_path


def test_is_lts_config_rejects_unparsable_and_foreign(tmp_path: Path):
    broken = tmp_path / "broken.toml"
    broken.write_text("this is ] not [ toml", encoding="utf-8")
    assert is_lts_config(broken) is False
    assert is_lts_config(tmp_path / "missing.toml") is False
    foreign = tmp_path / "foreign.toml"
    foreign.write_text('[server]\nport = 8080\n', encoding="utf-8")
    assert is_lts_config(foreign) is False


def test_require_project_raises_outside_a_project(tmp_path: Path):
    with pytest.raises(NotAnLtsProject) as exc:
        require_project(load_config(tmp_path))
    assert str(tmp_path) in str(exc.value)


def test_require_project_passes_inside_a_project(tmp_path: Path):
    require_project(load_config(make_project(tmp_path)))  # must not raise


def test_author_absent_means_single_developer(tmp_path: Path):
    make_project(tmp_path)
    assert load_config(tmp_path).author is None


def test_author_is_read_from_config(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[vault]\nproject = "p"\nauthor = "dmitrii"\n', encoding="utf-8"
    )
    assert load_config(tmp_path).author == "dmitrii"

def test_map_budget_defaults_and_can_be_overridden(tmp_path: Path):
    (tmp_path / "config.toml").write_text('[vault]\nproject = "p"\n', encoding="utf-8")
    assert load_config(tmp_path).map_budget == 1500

    other = tmp_path / "other"
    other.mkdir()
    (other / "config.toml").write_text(
        '[vault]\nproject = "p"\n\n[memory]\nmap_budget = 0\n', encoding="utf-8")
    assert load_config(other).map_budget == 0
