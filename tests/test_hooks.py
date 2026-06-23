from pathlib import Path
import importlib.util


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOOKS = Path(__file__).resolve().parents[1] / "claude" / "hooks"


def test_pre_compact_dumps_transcript(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("conversation raw text", encoding="utf-8")
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(transcript)}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == "conversation raw text"


def test_pre_compact_missing_transcript_still_writes(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    pre = _load("pre_compact", HOOKS / "pre_compact.py")
    pre.run({"session_id": "s", "transcript_path": str(tmp_path / "nope.jsonl")}, root=tmp_path)
    snaps = list((tmp_path / ".ai_memory" / "pending_consolidation").glob("*.md"))
    assert len(snaps) == 1
