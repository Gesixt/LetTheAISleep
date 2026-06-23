from pathlib import Path
from lts.config import load_config
from lts import paths


def _cfg(tmp_path: Path):
    return load_config(tmp_path)


def test_sidecar_paths(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert paths.sidecar_root(cfg) == tmp_path / ".ai_memory"
    assert paths.anchor_file(cfg) == tmp_path / ".ai_memory" / "anchor.json"
    assert paths.pending_dir(cfg) == tmp_path / ".ai_memory" / "pending_consolidation"
    assert paths.stm_file(cfg, "sess1") == tmp_path / ".ai_memory" / "stm" / "sess1.md"


def test_safe_session_id():
    assert paths.safe_session_id("a/b c") == "a_b_c"
    assert paths.safe_session_id("") == "default"
    assert paths.safe_session_id("../evil") == "___evil"


def test_ensure_sidecar_creates_dirs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    paths.ensure_sidecar(cfg)
    assert (tmp_path / ".ai_memory" / "stm").is_dir()
    assert (tmp_path / ".ai_memory" / "pending_consolidation").is_dir()
