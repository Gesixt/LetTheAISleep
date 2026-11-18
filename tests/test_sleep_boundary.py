"""A sleep in the middle of a turn cannot consolidate the work that follows it."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from lts import anchor, paths, stm, sync, watermark
from lts.config import load_config
from lts.hookimpl import pre_compact, session_start, stop
from tests.helpers import make_project


@pytest.fixture(params=['claude_code', 'codex'])
def chapter(tmp_path, request):
    client = request.param
    cfg = load_config(make_project(tmp_path, f'\n[clients]\nenabled = ["{client}"]\n'))
    paths.ensure_sidecar(cfg, client)
    transcript = tmp_path / 'session.jsonl'
    boundary = datetime(2026, 11, 16, 6, 7, 38, tzinfo=timezone.utc)
    watermark.arm(paths.sleep_flag_file(cfg, client), at=boundary)

    def write(*messages):
        records = []
        for seconds, text in messages:
            stamp = watermark.mark_at(boundary + timedelta(seconds=seconds))['timestamp']
            record = {'timestamp': stamp}
            if client == 'codex':
                record.update(type='response_item', payload={
                    'id': text, 'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': text}]})
            else:
                record.update(type='assistant', uuid=text,
                              message={'role': 'assistant', 'content': text})
            records.append(record)
        transcript.write_text('\n'.join(map(json.dumps, records)))

    event = {'cwd': str(tmp_path), 'transcript_path': str(transcript), 'session_id': 'review'}
    return cfg, client, boundary, write, event


def test_stop_keeps_review_after_sleep_and_does_not_capture_it_twice(chapter):
    cfg, client, boundary, write, event = chapter
    write((-10, 'already saved'), (4, 'review started'), (170, 'review result'))
    now = boundary + timedelta(minutes=3)
    assert stop.capture(event, now=now, client=client) == 2
    assert stm.read(paths.stm_file(cfg)) == '[assistant] review started\n[assistant] review result\n'
    assert watermark.read_mark(paths.sleep_mark_file(cfg)) == watermark.mark_at(boundary)
    assert watermark.read_mark(paths.consolidation_mark_file(cfg, client)) == watermark.mark_at(boundary)
    assert not watermark.is_armed(paths.sleep_flag_file(cfg, client))
    assert stop.capture(event, now=now, client=client) == 0
    # Captured is not consolidated: compact must still preserve this new review verbatim.
    pre_compact.run(event, now=now, client=client)
    pre_compact.run(event, now=now, client=client)
    snapshots = list(paths.pending_dir(cfg).glob('*.md'))
    assert len(snapshots) == 1
    assert snapshots[0].read_text() == '[assistant] review started\n\n[assistant] review result'


def test_compact_before_stop_preserves_new_work_without_moving_sleep_boundary(chapter):
    cfg, client, boundary, write, event = chapter
    write((-10, 'already saved'), (4, 'first finding'))
    for seconds in [30, 60]:
        pre_compact.run(event, now=boundary + timedelta(seconds=seconds), client=client)
    assert watermark.is_armed(paths.sleep_flag_file(cfg, client))
    assert watermark.read_mark(paths.sleep_mark_file(cfg)) == watermark.mark_at(boundary)
    assert len(list(paths.pending_dir(cfg).glob('*.md'))) == 1
    write((-10, 'already saved'), (4, 'first finding'), (90, 'second finding'))
    assert stop.capture(event, now=boundary + timedelta(seconds=120), client=client) == 2
    pre_compact.run(event, now=boundary + timedelta(seconds=130), client=client)
    bodies = sorted(p.read_text() for p in paths.pending_dir(cfg).glob('*.md'))
    assert bodies == ['[assistant] first finding', '[assistant] second finding']
    assert watermark.read_mark(paths.consolidation_mark_file(cfg, client)) == watermark.mark_at(boundary)


@pytest.mark.parametrize('missing', [False, True])
def test_sleep_boundary_survives_empty_or_missing_transcript_and_late_flush(chapter, missing):
    cfg, client, boundary, write, event = chapter
    if not missing:
        write((-10, 'already saved'))
    assert stop.capture(event, now=boundary + timedelta(seconds=30), client=client) == 0
    assert watermark.read_mark(paths.capture_mark_file(cfg, client)) == watermark.mark_at(boundary)
    # A later Stop with no newly flushed exchange must not rewind to pre-sleep messages.
    assert stop.capture(event, now=boundary + timedelta(seconds=35), client=client) == 0
    assert watermark.read_mark(paths.capture_mark_file(cfg, client)) == watermark.mark_at(boundary)
    # Produced before Stop, flushed after it. End-of-turn timestamp marks lost this result.
    write((-10, 'already saved'), (20, 'late review result'))
    pre_compact.run(event, now=boundary + timedelta(seconds=40), client=client)
    assert [p.read_text() for p in paths.pending_dir(cfg).glob('*.md')] == [
        '[assistant] late review result']
    assert stop.capture(event, now=boundary + timedelta(seconds=50), client=client) == 1
    assert 'already saved' not in stm.read(paths.stm_file(cfg))


def test_armed_sleep_does_not_rewind_a_later_capture(chapter):
    cfg, client, boundary, write, event = chapter
    write((10, 'captured before retry'))
    later = watermark.mark_at(boundary + timedelta(seconds=15))
    watermark.write_mark(paths.capture_mark_file(cfg, client), later)
    assert stop.capture(event, now=boundary + timedelta(seconds=30), client=client) == 0
    assert watermark.read_mark(paths.capture_mark_file(cfg, client)) == later


@pytest.mark.parametrize('tail', [
    'Saved the chapter; memory and index updated. You can /compact.',
    'New review result: queue and snapshot must commit in one transaction.',
])
def test_compact_after_sleep_keeps_the_tail_without_forcing_another_sleep(chapter, tail):
    """A single buffered reply can be narration OR important work; preserve both safely."""
    from pathlib import Path

    cfg, client, boundary, write, event = chapter
    sync.sync(Path(__file__).resolve().parents[1], cfg)
    note = paths.vault_root(cfg) / 'session-memory' / 'Saved chapter.md'
    note.parent.mkdir(parents=True)
    note.write_text('# Saved chapter\nThe earlier work is consolidated.\n')
    anchor.write_anchor(paths.anchor_file(cfg), updated='2026-11-16 09:07',
                        last_session='Saved chapter', active_topics=['Completed work'],
                        active_notes=['session-memory/Saved chapter'])
    write((-10, 'already saved'), (10, tail))
    now = boundary + timedelta(seconds=20)
    assert stop.capture(event, now=now, client=client) == 1
    pre_compact.run(event, now=now, client=client)
    snapshots = list(paths.pending_dir(cfg).glob('*.md'))
    assert len(snapshots) == 1
    before = (paths.stm_file(cfg).read_bytes(), snapshots[0].read_bytes(),
              paths.capture_mark_file(cfg, client).read_bytes(),
              paths.consolidation_mark_file(cfg, client).read_bytes())

    for _ in range(2):
        out = session_start.run({**event, 'source': 'compact'}, client=client)
        context = out['hookSpecificOutput']['additionalContext']
        assert 'Buffered memory' in context
        assert "Continue the user's current request" in context
        assert 'not evidence of an interrupted sleep' in context
        assert 'Saved chapter' in context  # The anchor still arrives alongside the backlog.
        assert 'Unfinished sleep detected' not in context
        assert 'Before doing anything else, run' not in context
        assert 'not an instruction to sleep now' in out['systemMessage']

    after = (paths.stm_file(cfg).read_bytes(), snapshots[0].read_bytes(),
             paths.capture_mark_file(cfg, client).read_bytes(),
             paths.consolidation_mark_file(cfg, client).read_bytes())
    assert after == before  # No deletion, count threshold, semantic filtering or frontier jump.
    assert tail in snapshots[0].read_text()
