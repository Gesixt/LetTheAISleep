import pytest

from pathlib import Path
from lts.config import NotAnLtsProject, load_config
from lts import paths, turnstate, watermark
from tests.helpers import flat_state_file, make_project


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


def test_vault_root_defaults_beside_the_project(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert paths.vault_root(cfg) == tmp_path / ".ai_vault"


def test_vault_root_honours_explicit_path(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[vault]\nproject = "p"\npath = "/srv/shared-vault"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert paths.vault_root(cfg) == Path("/srv/shared-vault")


def test_vault_root_expands_user(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '[vault]\nproject = "p"\npath = "~/vaults/mine"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert paths.vault_root(cfg) == Path.home() / "vaults" / "mine"


def test_transcript_bound_state_is_per_client(tmp_path: Path):
    cfg = _cfg(tmp_path)
    a = paths.capture_mark_file(cfg, "claude_code")
    b = paths.capture_mark_file(cfg, "second")
    assert a != b
    assert a.parent.name == "claude_code" and b.parent.name == "second"
    for fn in (paths.turn_state_file, paths.health_journal_file, paths.snapshot_mark_file):
        assert fn(cfg, "claude_code") != fn(cfg, "second")


def test_project_level_memory_stays_shared(tmp_path: Path):
    cfg = _cfg(tmp_path)
    # One project, one memory: these are the files the "both clients, shared memory" decision is
    # about, and a client argument on any of them would quietly undo it.
    for fn in (paths.stm_file, paths.anchor_file, paths.sleep_mark_file,
               paths.sleep_flag_file, paths.pending_dir):
        assert "clients" not in fn(cfg).parts


def _flat(cfg, name: str) -> Path:
    """The pre-split location of `name`, directory created, ready to be written.

    Composed from `paths._FLAT` rather than spelled out: that dict is the only record of where
    these files used to live, and a test that types the old path by hand stops tracking it.
    """
    path = flat_state_file(cfg, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_the_pre_split_files_are_adopted_once_by_the_default_client(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _flat(cfg, "capture-mark.json").write_text('{"uuid": "u9"}', encoding="utf-8")
    _flat(cfg, "turn-state.json").write_text('{"misses": 2}', encoding="utf-8")
    _flat(cfg, "health.jsonl").write_text('{"event": "session_start"}\n', encoding="utf-8")

    paths.ensure_sidecar(cfg)

    assert paths.capture_mark_file(cfg).read_text(encoding="utf-8") == '{"uuid": "u9"}'
    assert paths.turn_state_file(cfg).read_text(encoding="utf-8") == '{"misses": 2}'
    assert paths.health_journal_file(cfg).read_text(encoding="utf-8").strip() == \
        '{"event": "session_start"}'
    for name in paths._FLAT:
        assert not flat_state_file(cfg, name).exists()


def test_a_second_client_never_inherits_the_first_clients_position(tmp_path: Path):
    # The whole reason this state splits: a capture mark names an exchange by uuid inside ONE
    # transcript. Handed to another client it resolves against a file that has never contained
    # that uuid, and `watermark.exchanges_after` falls through to dropping everything older.
    cfg = _cfg(tmp_path)
    _flat(cfg, "capture-mark.json").write_text('{"uuid": "u9"}', encoding="utf-8")

    paths.ensure_sidecar(cfg, "second")

    assert not paths.capture_mark_file(cfg, "second").exists()
    assert flat_state_file(cfg, "capture-mark.json").exists()


def test_adoption_still_happens_when_a_hook_created_the_directory_first(tmp_path: Path):
    # The production sequence on a live project: `SessionStart`'s `journal.append` and
    # `UserPromptSubmit`'s `turnstate.write` each mkdir their own parent, so the client directory
    # exists before any hook calls `ensure_sidecar`. Gating adoption on the directory meant it
    # never ran: the flat capture mark stayed orphaned and the next Stop replayed the whole
    # transcript into the shared buffer.
    cfg = _cfg(tmp_path)
    _flat(cfg, "capture-mark.json").write_text('{"uuid": "u9"}', encoding="utf-8")
    turnstate.write(paths.turn_state_file(cfg), {"misses": 0})

    paths.ensure_sidecar(cfg)

    assert paths.capture_mark_file(cfg).read_text(encoding="utf-8") == '{"uuid": "u9"}'
    assert not flat_state_file(cfg, "capture-mark.json").exists()


def test_adoption_does_not_run_a_second_time(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _flat(cfg, "capture-mark.json").write_text('{"uuid": "old"}', encoding="utf-8")
    paths.ensure_sidecar(cfg)
    paths.capture_mark_file(cfg).write_text('{"uuid": "new"}', encoding="utf-8")

    # A flat file reappearing later (a stale clone, a restored backup) must not overwrite the
    # position the hooks have been maintaining since.
    _flat(cfg, "capture-mark.json").write_text('{"uuid": "stale"}', encoding="utf-8")
    paths.ensure_sidecar(cfg)

    assert paths.capture_mark_file(cfg).read_text(encoding="utf-8") == '{"uuid": "new"}'


def test_a_flat_file_that_reappears_after_the_target_was_deleted_is_refused(tmp_path: Path):
    # The one property only the marker has, and the reason the per-file guard is not enough on its
    # own: `lts doctor`'s remedy for a mark that cannot be read is "delete the capture mark file".
    # Without the marker the next `ensure_sidecar` would adopt a flat file into that gap and
    # resurrect the position the operator had just removed.
    cfg = _cfg(tmp_path)
    _flat(cfg, "capture-mark.json").write_text('{"uuid": "u9"}', encoding="utf-8")
    paths.ensure_sidecar(cfg)
    paths.capture_mark_file(cfg).unlink()

    _flat(cfg, "capture-mark.json").write_text('{"uuid": "stale"}', encoding="utf-8")
    paths.ensure_sidecar(cfg)

    assert not paths.capture_mark_file(cfg).exists()


def test_a_move_that_failed_withholds_the_marker_so_the_next_run_retries(tmp_path: Path,
                                                                        monkeypatch):
    """A migration that did not happen must not be recorded as one.

    The OSError is swallowed on purpose — five projects run this inside hooks, and a hook that
    raises captures nothing at all — so the only report of it is the `False` that withholds the
    marker. Drop that report and the marker is written over a flat file still sitting at its old
    path; `adoption_marker_file`'s one-chance rule then keeps it there for good, `stop.py` reads
    `{}` and the next Stop replays the whole transcript into the shared buffer.
    """
    cfg = _cfg(tmp_path)
    for name, body in (("capture-mark.json", '{"uuid": "u9"}'),
                       ("turn-state.json", '{"misses": 2}'),
                       ("health.jsonl", '{"event": "session_start"}\n')):
        _flat(cfg, name).write_text(body, encoding="utf-8")
    stuck = flat_state_file(cfg, "health.jsonl")
    real_replace = Path.replace

    def failing_replace(self, target):
        if self == stuck:
            raise OSError("cross-device link")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    paths.ensure_sidecar(cfg)

    assert not paths.adoption_marker_file(cfg).exists()
    assert stuck.exists(), "the file that could not move must stay where it is"
    # The two that could move did: a partial migration is still progress, and re-running the
    # adoption over an already-moved file is a no-op (the per-file `not target.exists()` guard).
    assert paths.capture_mark_file(cfg).read_text(encoding="utf-8") == '{"uuid": "u9"}'

    monkeypatch.undo()
    paths.ensure_sidecar(cfg)

    assert paths.adoption_marker_file(cfg).exists()
    assert paths.health_journal_file(cfg).read_text(encoding="utf-8").strip() == \
        '{"event": "session_start"}'
    assert not stuck.exists()


def test_adoption_starts_the_snapshot_mark_where_the_old_sleep_mark_stood(tmp_path: Path):
    cfg = _cfg(tmp_path)
    sleep_mark = paths.sleep_mark_file(cfg)
    sleep_mark.parent.mkdir(parents=True, exist_ok=True)
    sleep_mark.write_text(
        '{"uuid": "u9", "timestamp": "2026-10-19T06:09:51.626Z"}', encoding="utf-8")

    paths.ensure_sidecar(cfg)

    assert watermark.read_mark(paths.snapshot_mark_file(cfg))["uuid"] == "u9"
    # Untouched: clearing it would manufacture a "nothing is consolidated" demand.
    assert watermark.read_mark(paths.sleep_mark_file(cfg))["uuid"] == "u9"
