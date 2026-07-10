import pytest

from pathlib import Path
from lts.config import NotAnLtsProject, load_config
from lts import paths
from tests.helpers import make_project


def _cfg(tmp_path: Path):
    return load_config(make_project(tmp_path))


def test_sidecar_paths(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert paths.sidecar_root(cfg) == tmp_path / ".ai_memory"
    assert paths.anchor_file(cfg) == tmp_path / ".ai_memory" / "anchor.json"
    assert paths.pending_dir(cfg) == tmp_path / ".ai_memory" / "pending_consolidation"
    assert paths.stm_file(cfg) == tmp_path / ".ai_memory" / "stm" / "buffer.md"


def test_safe_session_id():
    assert paths.safe_session_id("a/b c") == "a_b_c"
    assert paths.safe_session_id("") == "default"
    assert paths.safe_session_id("../evil") == "___evil"


def test_ensure_sidecar_creates_dirs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    paths.ensure_sidecar(cfg)
    assert (tmp_path / ".ai_memory" / "stm").is_dir()
    assert (tmp_path / ".ai_memory" / "pending_consolidation").is_dir()


def test_ensure_sidecar_refuses_outside_a_project(tmp_path: Path):
    # the bug this guards: a lost/moved config.toml used to make .ai_memory sprout
    # in whatever directory we happened to run from, silently splitting memory in two
    stray = tmp_path / "some" / "subdir"
    stray.mkdir(parents=True)
    with pytest.raises(NotAnLtsProject):
        paths.ensure_sidecar(load_config(stray))
    assert not (stray / ".ai_memory").exists()
