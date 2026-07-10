from pathlib import Path

from lts import doctor, paths
from lts.config import load_config
from tests.helpers import make_project


def test_healthy_project_reports_ok(tmp_path: Path):
    cfg = load_config(make_project(tmp_path))
    paths.ensure_sidecar(cfg)
    report = doctor.collect(cfg)
    assert report["ok"] is True
    assert report["stray_sidecars"] == []
    assert "no stray sidecars" in doctor.render(report)


def test_unconfigured_root_is_not_ok(tmp_path: Path):
    report = doctor.collect(load_config(tmp_path))
    assert report["ok"] is False
    assert report["configured"] is False
    assert "no config.toml" in doctor.render(report)


def test_finds_stray_sidecar_in_subdirectory(tmp_path: Path):
    cfg = load_config(make_project(tmp_path))
    paths.ensure_sidecar(cfg)
    stray = tmp_path / "services" / "cart" / ".ai_memory" / "stm"
    stray.mkdir(parents=True)
    report = doctor.collect(cfg)
    assert report["ok"] is False
    assert report["stray_sidecars"] == [str(tmp_path / "services" / "cart" / ".ai_memory")]
    assert "orphaned memory" in doctor.render(report)


def test_nested_project_root_is_reported_but_not_a_stray(tmp_path: Path):
    cfg = load_config(make_project(tmp_path))
    paths.ensure_sidecar(cfg)
    nested = make_project(tmp_path / "sub")
    (nested / ".ai_memory").mkdir()
    report = doctor.collect(cfg)
    assert report["stray_sidecars"] == []
    assert report["nested_project_roots"] == [str(nested)]
    assert report["ok"] is True


def test_find_sidecars_skips_noise_dirs(tmp_path: Path):
    (tmp_path / "node_modules" / "pkg" / ".ai_memory").mkdir(parents=True)
    (tmp_path / ".git" / ".ai_memory").mkdir(parents=True)
    assert doctor.find_sidecars(tmp_path) == []