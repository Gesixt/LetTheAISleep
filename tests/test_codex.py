import json
import subprocess
import sys
from pathlib import Path

from lts import clients, paths, sync, status
from lts.config import load_config
from tests.helpers import make_project

FIXTURE = Path(__file__).parent / 'fixtures/codex-0.169.0.jsonl'
ROOT = Path(__file__).resolve().parents[1]


def test_recorded_rollout_excludes_harness_and_reasoning():
    adapter = clients.resolve('codex')
    exchanges = adapter.read_exchanges(FIXTURE)
    assert [e['text'] for e in exchanges] == [
        'Remember the decision: use SQLite.', 'I will inspect the code.',
        'SQLite was selected for local storage.',
    ]
    assert len({e['uuid'] for e in exchanges}) == 3
    assert adapter.newest_exchange_after(FIXTURE, None)
    assert not adapter.newest_exchange_after(FIXTURE, exchanges[-1]['timestamp'])


def test_codex_uses_last_usage_not_cumulative_or_cached_twice(tmp_path):
    cfg = load_config(make_project(tmp_path))
    data = next(json.loads(line)['payload']['info'] for line in FIXTURE.read_text().splitlines()
                if json.loads(line)['type'] == 'event_msg')
    p = status.collect(cfg, transcript_path=FIXTURE, client='codex')['pressure']
    assert p['tokens'] == data['last_token_usage']['input_tokens']
    assert p['window'] == data['model_context_window']
    assert p['source'] == 'usage'


def test_codex_timestamp_and_missing_ids_are_stable(tmp_path):
    path = tmp_path / 'rollout.jsonl'
    records = [{'timestamp': ts, 'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'hello'}]}}
        for ts in ['2026-11-13T14:00:00.123456+03:00', '2026-11-13T11:00:01Z']]
    path.write_text('\n'.join(map(json.dumps, records)) + '\n{incomplete')
    adapter = clients.resolve('codex')
    first = adapter.read_exchanges(path)
    assert [x['timestamp'] for x in first] == ['2026-11-13T11:00:00.123Z', '2026-11-13T11:00:01.000Z']
    assert all(x['uuid'] for x in first)
    assert first == adapter.read_exchanges(path)


def test_codex_sync_preserves_other_hooks_and_is_idempotent(tmp_path):
    root = make_project(tmp_path, '\n[clients]\nenabled = ["codex"]\n')
    settings = root / '.codex/hooks.json'
    settings.parent.mkdir()
    custom = {'hooks': [{'type': 'command', 'command': 'echo custom'}]}
    settings.write_text(json.dumps({'hooks': {'Stop': [custom]}}))
    cfg = load_config(root)
    sync.sync(ROOT, cfg)
    data = json.loads(settings.read_text())
    assert custom in data['hooks']['Stop']
    assert any('--client codex' in h['command'] for group in data['hooks']['Stop'] for h in group['hooks'])
    assert (root / '.agents/skills/sleep/agents/openai.yaml').is_file()
    assert '$sleep' in (root / 'AGENTS.md').read_text()
    assert not (root / '.claude').exists()
    assert not sync.sync(ROOT, cfg, check=True)['stale']


def test_codex_stop_captures_once_even_when_claude_sleep_is_armed(tmp_path):
    from lts import watermark
    cfg = load_config(make_project(tmp_path))
    paths.ensure_sidecar(cfg)
    watermark.arm(paths.sleep_flag_file(cfg))
    event = {'cwd': str(tmp_path), 'transcript_path': str(FIXTURE), 'session_id': 'fixture'}
    command = [sys.executable, str(ROOT / 'codex/hooks/stop.py'), '--client', 'codex']
    for _ in range(2):
        result = subprocess.run(command, input=json.dumps(event), text=True, capture_output=True)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
    text = paths.stm_file(cfg).read_text()
    assert text.count('Remember the decision') == 1
    assert text.count('SQLite was selected') == 1
    assert watermark.is_armed(paths.sleep_flag_file(cfg))


def test_codex_health_checks_its_own_wiring_and_does_not_claim_trust(tmp_path):
    from lts import health
    cfg = load_config(make_project(tmp_path, '\n[clients]\nenabled = ["codex"]\n'))
    sync.sync(ROOT, cfg)
    checks = {c.id: c for c in health.run(cfg, client='codex')}
    assert checks['hooks'].level == 'ok'
    assert checks['hook_trust'].level == 'skip'
    wiring = sync.wiring_file(tmp_path, 'codex')
    wiring.write_text(wiring.read_text().replace('--client codex', '--client claude_code'))
    assert next(c for c in health.run(cfg, client='codex') if c.id == 'hooks').level == 'fail'


def test_other_clients_sleep_cannot_hide_a_codex_precompact_tail(tmp_path):
    from lts import watermark
    cfg = load_config(make_project(tmp_path, '\n[clients]\nenabled = ["claude_code", "codex"]\n'))
    paths.ensure_sidecar(cfg, 'codex')
    watermark.write_mark(paths.sleep_mark_file(cfg), {'timestamp': '2099-01-01T00:00:00.000Z'})
    event = {'cwd': str(tmp_path), 'transcript_path': str(FIXTURE), 'session_id': 'fixture'}
    result = subprocess.run([sys.executable, str(ROOT / 'codex/hooks/pre_compact.py')],
                            input=json.dumps(event), text=True, capture_output=True)
    assert result.returncode == 0
    assert any('Remember the decision' in p.read_text() for p in paths.pending_dir(cfg).glob('*.md'))


def test_cli_client_selection_and_installer(tmp_path, capsys):
    import install
    from lts import cli
    root = make_project(tmp_path)
    assert install.main(['--target', str(root), '--client', 'codex', '--project', 'test-project']) == 0
    assert load_config(root).enabled_clients == ('codex',)
    assert cli.main(['status', '--root', str(root), '--client', 'codex', '--transcript', str(FIXTURE), '--json']) == 0
    assert cli.main(['pressure', '--root', str(root), '--client', 'codex', '--transcript', str(FIXTURE)]) == 0


def test_missing_usage_does_not_invent_a_codex_window(tmp_path):
    from lts import health
    path = tmp_path / 'empty.jsonl'
    path.write_text('')
    cfg = load_config(make_project(tmp_path))
    p = status.collect(cfg, transcript_path=path, client='codex')['pressure']
    assert p['level'] == 'unknown'
    assert next(c for c in health.run(cfg, client='codex', transcript_path=path)
                if c.id == 'pressure').level == 'skip'


def test_all_codex_entry_points_accept_null_transcript(tmp_path):
    make_project(tmp_path)
    for event, script in sync.HOOK_EVENTS.items():
        result = subprocess.run([sys.executable, str(ROOT / 'codex/hooks' / script)],
            input=json.dumps({'cwd': str(tmp_path), 'session_id': 'empty',
                              'hook_event_name': event, 'transcript_path': None}),
            text=True, capture_output=True)
        assert result.returncode == 0
        assert isinstance(json.loads(result.stdout), dict)


def test_codex_sleep_latch_is_owned_by_codex(tmp_path):
    from lts import cli, watermark
    cfg = load_config(make_project(tmp_path))
    vault = paths.vault_root(cfg)
    vault.mkdir(parents=True)
    (vault / 'note.md').write_text('# Saved decision\nSQLite\n')
    assert cli.main(['stm', 'clear', '--root', str(tmp_path), '--client', 'codex']) == 0
    assert watermark.is_armed(paths.sleep_flag_file(cfg, 'codex'))
    assert not watermark.is_armed(paths.sleep_flag_file(cfg))
