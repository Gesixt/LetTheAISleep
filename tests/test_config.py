from pathlib import Path
from lts.config import load_config, find_project_root, DEFAULTS


def test_defaults_when_no_config(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.vault_mode == DEFAULTS["vault_mode"]
    assert cfg.pressure_warn == 0.60
    assert cfg.pressure_force == 0.80
    assert cfg.project_root == tmp_path
    assert cfg.project == DEFAULTS["project"]
    assert cfg.vault_path is None


def test_reads_config_toml(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[vault]\nmode = "global"\nproject = "lts-demo"\npath = "/some/vault"\n'
        '[sleep]\npressure_warn = 0.5\npressure_force = 0.75\n',
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.vault_mode == "global"
    assert cfg.project == "lts-demo"
    assert cfg.vault_path == "/some/vault"
    assert cfg.pressure_warn == 0.5
    assert cfg.pressure_force == 0.75


def test_find_project_root_walks_up(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path
