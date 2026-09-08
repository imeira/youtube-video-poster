"""Executable vertical tests: all simulated evidence is explicitly TEST-only."""
from __future__ import annotations
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from PIL import Image
from src.throughput.controller import ThroughputController, _atomic_json


def now():
    return datetime.now(timezone.utc).isoformat()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return bind(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def bind(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verdict(req, path, decision='PASS', phase='preflight', source=None, after=None):
    proof = {'request_id': req['request_id'], 'request_sha256': req['_sha256'],
             'phase': phase, 'source_sha256': source or req['source']['sha256'],
             'contract_sha256': req['contract']['sha256'], 'decision': decision,
             'timestamp': after or now(), 'test_only': True}
    proof_binding = put(path.with_suffix('.proof.json'), proof)
    return put(path, dict(proof, reviewer_origin={'schema': 'reviewer-origin/1',
        'reviewer_id': 'fixture-reviewer', 'execution_context': 'isolated-test-review',
        'result_path': proof_binding['path'], 'result_sha256': proof_binding['sha256']}))


def episode(tmp_path, decision='PASS'):
    root = tmp_path / 'TEST_EPISODE'
    root.mkdir()
    image = root / 'images' / 'source.png'
    image.parent.mkdir()
    Image.new('RGB', (16, 9), 'blue').save(image)
    contract = put(root / 'contract.json', {'schema': 'fixture-contract/1', 'required': ['blue'], 'width': 16, 'height': 9})
    put(root / 'state.json', {'episode_id': root.name, 'current_state': 'GENERATING_IMAGES',
        'checkpoint': {'approved_assets': []}, 'state_history': [], 'test_only': True})
    put(root / 'costs.json', {'spent': 0, 'projected': 0, 'hard_limit': 6, 'test_only': True})
    auth = root / 'approval' / 'auth.json'
    req = {'schema': 'throughput-request/1', 'test_only': True, 'episode_id': root.name,
        'request_id': 'SC001-r1', 'track': 'frames', 'frame': 'SC001', 'revision': 1,
        'source': bind(image), 'contract': contract, 'width': 16, 'height': 9,
        'created_at': now(), 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'authorization_path': str(auth), 'estimate': 0, 'predecessor': None,
        'verdict_path': str(root / 'qa' / 'verdict.json')}
    req_path = root / 'qa' / 'request.json'
    req_binding = put(req_path, req)
    put(auth, {'schema': 'throughput-authorization/1', 'episode_id': root.name,
        'request_sha256': req_binding['sha256'], 'allowed': True, 'expires_at': req['expires_at'], 'test_only': True})
    req['_sha256'] = req_binding['sha256']
    if decision:
        verdict(req, Path(req['verdict_path']), decision)
    script = Path(__file__).parents[1] / 'throughput_fixture_worker.py'
    commands = {stage: dict(bind(script), test_only=True, idempotent=True, start_gate_protocol=True,
        reviewer_id='fixture-reviewer', execution_context='isolated-test-review')
        for stage in ('action', 'internal_qa', 'review', 'transport', 'promote', 'successor', 'remediation', 'planning', 'reconcile')}
    config = {'schema': 'throughput-deployment/1', 'episode_root': str(root), 'test_only': True,
        'lock_name': '.episode_writer.lock', 'requests': [str(req_path)], 'commands': commands,
        'command_timeout': 10, 'review_timeout': 60, 'max_review_retries': 1, 'max_remediations': 2}
    config_path = tmp_path / 'deployment.json'
    put(config_path, config)
    return root, config_path, req


def controller(root, config):
    return ThroughputController(root, config_path=config, test_mode=True)


def test_atomic_queue_write_retries_bounded_windows_sharing_violation(tmp_path, monkeypatch):
    """A concurrent JSON reader may temporarily deny replace on Windows."""
    import src.throughput.controller as throughput
    path = tmp_path / 'qa' / 'throughput' / 'queue.json'
    real_replace = throughput.os.replace
    attempts = []

    def sharing_replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            error = PermissionError(5, 'Access is denied', str(destination))
            error.winerror = 5
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(throughput.os, 'replace', sharing_replace)
    monkeypatch.setattr(throughput, 'IS_WINDOWS', True)
    monkeypatch.setattr(throughput.time, 'sleep', lambda _: None)
    _atomic_json(path, {'revision': 1})
    assert read(path) == {'revision': 1}
    assert len(attempts) == 3


def test_atomic_queue_write_does_not_hide_persistent_access_denied(tmp_path, monkeypatch):
    import src.throughput.controller as throughput
    path = tmp_path / 'qa' / 'throughput' / 'queue.json'
    attempts = []

    def denied_replace(source, destination):
        attempts.append((source, destination))
        error = PermissionError(5, 'Access is denied', str(destination))
        error.winerror = 5
        raise error

    monkeypatch.setattr(throughput.os, 'replace', denied_replace)
    monkeypatch.setattr(throughput.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        _atomic_json(path, {'revision': 1})
    assert 1 < len(attempts) <= 5
    assert not list(path.parent.glob('queue.json*.tmp'))


def test_episode_root_lock_excludes_second_writer_and_recovers_dead_owner(tmp_path):
    root = tmp_path / 'EPTEST'
    root.mkdir()
    first = ThroughputController(root, test_mode=True)
    second = ThroughputController(root, test_mode=True)
    assert first._acquire()
    lock = root / '.episode_writer.lock'
    assert lock.is_file()
    assert read(lock)['pid'] == os.getpid()
    assert not second._acquire()
    second._release()
    assert lock.exists()
    first._release()
    put(lock, {'pid': 99999999, 'run_id': 'dead'})
    assert second._acquire()
    second._release()


def test_reclaim_guard_is_crash_released_os_lock_not_stale_exclusive_file(tmp_path):
    root = tmp_path / 'TEST_EPISODE'
    root.mkdir()
    (root / '.episode_writer.lock.reclaim').write_text('stale bytes from dead process')
    controller = ThroughputController(root, test_mode=True)
    assert controller._acquire()
    controller._release()


def test_stale_controller_lease_is_not_reclaimed_while_adapter_child_is_live(tmp_path):
    import src.throughput.controller as throughput
    root = tmp_path / 'TEST_EPISODE'
    root.mkdir()
    put(root / '.episode_writer.lock', {'pid': 99999999, 'pid_identity': 'dead-controller',
        'run_id': 'dead', 'active_operation': {'status': 'RUNNING', 'pid': os.getpid(),
            'pid_identity': throughput._pid_identity(os.getpid()), 'operation_id': 'op'}})
    controller = ThroughputController(root, test_mode=True)
    assert not controller._acquire()
    assert read(root / '.episode_writer.lock')['run_id'] == 'dead'


def test_ambiguous_adapter_start_fences_stale_lease_recovery(tmp_path):
    root = tmp_path / 'TEST_EPISODE'
    root.mkdir()
    put(root / '.episode_writer.lock', {'pid': 99999999, 'pid_identity': 'dead-controller',
        'run_id': 'dead', 'active_operation': {'status': 'STARTING', 'operation_id': 'op'}})
    assert not ThroughputController(root, test_mode=True)._acquire()


def test_lease_rejects_reused_live_pid_with_different_process_identity(monkeypatch):
    import src.throughput.controller as throughput
    monkeypatch.setattr(throughput, '_is_live_pid', lambda pid: pid == 123)
    monkeypatch.setattr(throughput, '_pid_identity', lambda pid: 'current-process')
    assert not throughput._lease_matches_process({'pid': 123, 'pid_identity': 'old-process'})
    assert throughput._lease_matches_process({'pid': 123, 'pid_identity': 'current-process'})
    assert not throughput._lease_matches_process({'pid': 123})


def test_adapter_executes_only_while_lease_binds_the_exact_child_process(tmp_path):
    root, config, req = episode(tmp_path)
    result = controller(root, config).run_once()
    assert result['status'] == 'WAITING_REVIEW', result
    events = [json.loads(line) for line in (root / 'executions.jsonl').read_text().splitlines()]
    assert events
    assert all(event['lease_child_pid_bound'] is True for event in events)
    assert all(event['lease_operation_id_bound'] is True for event in events)


def test_pass_executes_full_chain_and_starts_successor_in_same_run(tmp_path):
    root, config, req = episode(tmp_path)
    result = controller(root, config).run_once()
    events = [json.loads(line) for line in (root / 'executions.jsonl').read_text().splitlines()]
    stages = [e['stage'] for e in events]
    assert stages == ['action', 'internal_qa', 'transport', 'review', 'promote', 'successor', 'transport', 'review']
    jobs = read(root / 'qa/throughput/queue.json')['jobs']
    assert jobs[0]['status'] == 'DONE'
    assert jobs[1]['status'] == 'WAITING_REVIEW'
    assert read(root / 'state.json')['checkpoint']['approved_assets'] == ['SC001']
    assert result['last_completed_action'] == 'review'
    assert controller(root, config).run_once()['status'] == 'WAITING_REVIEW'
    assert len((root / 'executions.jsonl').read_text().splitlines()) == len(events)

def test_fail_launches_bounded_remediation_and_fresh_preflight(tmp_path):
    root, config, req = episode(tmp_path, 'FAIL')
    c = controller(root, config)
    assert c.run_once()['status'] == 'WAITING_REVIEW'
    jobs = read(c.queue_path)['jobs']
    assert jobs[0]['status'] == 'DONE'
    assert jobs[1]['request']['revision'] == 2
    assert jobs[1]['status'] == 'WAITING_REVIEW'
    for revision in (2, 3):
        fresh = dict(jobs[-1]['request'], _sha256=jobs[-1]['id'])
        verdict(fresh, Path(fresh['verdict_path']), 'FAIL')
        result = controller(root, config).run_once()
        jobs = read(c.queue_path)['jobs']
    assert result['status'] == 'HUMAN_GATE'
    stages = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert stages.count('remediation') == 2
    assert stages.count('review') == 2
    assert 'action' not in stages

@pytest.mark.parametrize('mutation', ['live_test', 'script_tamper', 'shell_path', 'bool_timeout', 'wrong_root', 'ep8_lock'])
def test_deployment_rejected_before_any_execution(tmp_path, mutation):
    root, config, req = episode(tmp_path)
    cfg = read(config)
    live = False
    if mutation == 'live_test':
        live = True
    elif mutation == 'script_tamper':
        cfg['commands']['action']['sha256'] = '0' * 64
    elif mutation == 'shell_path':
        cfg['commands']['action']['path'] = 'cmd.exe /c echo unsafe'
    elif mutation == 'bool_timeout':
        cfg['command_timeout'] = True
    elif mutation == 'wrong_root':
        cfg['episode_root'] = str(tmp_path)
    elif mutation == 'ep8_lock':
        cfg['lock_name'] = '.other.lock'
        root.rename(tmp_path / 'EP8_FIXTURE')
        root = tmp_path / 'EP8_FIXTURE'
        cfg['episode_root'] = str(root)
    put(config, cfg)
    result = ThroughputController(root, config_path=config, test_mode=not live).run_once()
    assert result['status'] == 'BLOCKED_PREREQUISITE'
    assert not (root / 'executions.jsonl').exists()
    assert not (root / 'qa/throughput/queue.json').exists()


def test_test_mode_requires_marked_isolated_authority_before_any_write(tmp_path):
    root, config, req = episode(tmp_path)
    state = read(root / 'state.json')
    state.pop('test_only')
    put(root / 'state.json', state)
    result = controller(root, config).run_once()
    assert result['status'] == 'BLOCKED_PREREQUISITE'
    assert 'TEST isolation' in result['reason']
    assert not (root / 'qa/throughput/queue.json').exists()
    assert not (root / 'executions.jsonl').exists()


@pytest.mark.parametrize('mutation', ['outside_verdict', 'unknown_track', 'extra_request_key'])
def test_request_closed_schema_and_destinations_are_rejected_before_execution(tmp_path, mutation):
    root, config, req = episode(tmp_path)
    path = root / 'qa/request.json'
    value = read(path)
    if mutation == 'outside_verdict':
        value['verdict_path'] = str(tmp_path / 'outside-verdict.json')
    elif mutation == 'unknown_track':
        value['track'] = 'arbitrary'
    else:
        value['command'] = 'not allowed'
    put(path, value)
    result = controller(root, config).run_once()
    assert result['status'] == 'BLOCKED_PREREQUISITE'
    assert not (tmp_path / 'outside-verdict.json').exists()
    assert not (root / 'executions.jsonl').exists()
    assert not (root / 'qa/throughput/queue.json').exists()


@pytest.mark.parametrize('field,value', [('revision', True), ('width', True), ('height', 8), ('estimate', False), ('estimate', -1), ('estimate', float('inf'))])
def test_mechanical_validator_rejects_invalid_numbers_and_geometry(tmp_path, field, value):
    root, config, req = episode(tmp_path)
    path = root / 'qa/request.json'
    value_req = read(path)
    value_req[field] = value
    put(path, value_req)
    c = controller(root, config)
    with pytest.raises(ValueError):
        c.validate_evidence(value_req)


def test_evidence_cache_rehashes_content_and_effective_contract(tmp_path):
    root, config, req = episode(tmp_path)
    c = controller(root, config)
    request = read(root / 'qa/request.json')
    first = c.validate_evidence(request)
    assert c.validate_evidence(request)['cache_hit'] is True
    contract = read(root / 'contract.json')
    contract['required'].append('closed mouth')
    request['contract'] = put(root / 'contract.json', contract)
    second = c.validate_evidence(request)
    assert second['cache_key'] != first['cache_key']
    assert second['cache_hit'] is False
    Image.new('RGB', (16, 9), 'red').save(Path(req['source']['path']))
    with pytest.raises(ValueError, match='binding'):
        c.validate_evidence(request)

@pytest.mark.parametrize('mutation,status', [('budget', 'HUMAN_GATE'), ('bool_spent', 'BLOCKED_PREREQUISITE'), ('revoked', 'BLOCKED_PREREQUISITE'), ('expired', 'BLOCKED_PREREQUISITE'), ('paused', 'BLOCKED_PREREQUISITE')])
def test_mutable_authority_is_fresh_even_with_cached_evidence(tmp_path, mutation, status):
    root, config, req = episode(tmp_path)
    c = controller(root, config)
    c.validate_evidence(read(root / 'qa/request.json'))
    if mutation in ('budget', 'bool_spent'):
        put(root / 'costs.json', {'spent': 5 if mutation == 'budget' else True, 'projected': 2, 'hard_limit': 6, 'test_only': True})
    elif mutation in ('revoked', 'expired'):
        auth_path = Path(req['authorization_path'])
        auth = read(auth_path)
        auth['allowed'] = mutation != 'revoked'
        if mutation == 'expired':
            auth['expires_at'] = '2000-01-01T00:00:00+00:00'
        put(auth_path, auth)
    else:
        state = read(root / 'state.json')
        state['current_state'] = 'PAUSED'
        put(root / 'state.json', state)
    result = c.run_once()
    assert result['status'] == status
    assert not (root / 'executions.jsonl').exists()

@pytest.mark.parametrize('mutation', ['literal', 'missing_origin_field', 'stale', 'future', 'unbound', 'malformed', 'wrong_mode', 'extra_command'])
def test_review_requires_structured_bound_temporal_proof(tmp_path, mutation):
    root, config, req = episode(tmp_path)
    path = Path(req['verdict_path'])
    data = read(path)
    if mutation == 'literal':
        data.pop('reviewer_origin')
        data['reviewer'] = 'independent'
    elif mutation == 'missing_origin_field':
        del data['reviewer_origin']['result_sha256']
    elif mutation == 'stale':
        data['timestamp'] = '2000-01-01T00:00:00+00:00'
    elif mutation == 'future':
        data['timestamp'] = '2099-01-01T00:00:00+00:00'
    elif mutation == 'unbound':
        data['request_sha256'] = '0' * 64
    elif mutation == 'wrong_mode':
        data['test_only'] = False
    elif mutation == 'extra_command':
        data['command'] = 'arbitrary invocation'
    if mutation != 'malformed':
        origin = data.get('reviewer_origin')
        if origin and 'result_sha256' in origin:
            origin['result_sha256'] = put(Path(origin['result_path']), {k: v for k, v in data.items() if k != 'reviewer_origin'})['sha256']
        put(path, data)
    else:
        path.write_text('{')
    result = controller(root, config).run_once()
    assert result['status'] == 'WAITING_REVIEW'
    assert not (root / 'executions.jsonl').exists()

def test_restart_between_receipt_and_queue_commit_keeps_exactly_once_action_and_timing(tmp_path, monkeypatch):
    root, config, req = episode(tmp_path)
    c = controller(root, config)
    save = c._save
    def crash():
        if c.queue['jobs'][0]['stage'] == 'internal_qa':
            raise RuntimeError('simulated crash before queue commit')
        save()
    monkeypatch.setattr(c, '_save', crash)
    with pytest.raises(RuntimeError, match='simulated crash'):
        c.run_once()
    q = read(c.queue_path)
    assert q['jobs'][0]['stage'] == 'action'
    result = controller(root, config).run_once()
    assert result['status'] == 'WAITING_REVIEW'
    events = read(c.queue_path)['events']
    action = [e for e in events if e['stage'] == 'action']
    assert len(action) == 1
    receipt_path = Path(read(c.queue_path)['jobs'][0]['receipts']['action']['path'])
    receipt = read(receipt_path)
    assert action[0]['started_at'] <= receipt['started_at']
    assert action[0]['finished_at'] >= receipt['finished_at']
    executions = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert executions.count('action') == 1

def test_unknown_submission_reconciles_opaque_id_without_resubmit(tmp_path):
    root, config, req = episode(tmp_path)
    opaque = 'opaque/request ID ?a=$()&x=1'
    put(root / 'unknown.json', {'request_id': opaque})
    for _ in range(2):
        assert controller(root, config).run_once()['status'] == 'RETRY_SCHEDULED'
    assert read(root / 'reconciled.json')['request_id'] == opaque
    stages = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert stages.count('action') == 1
    assert 'reconcile' in stages
    assert 'promote' not in stages

def test_ambiguous_nonidempotent_action_is_write_ahead_consumed(tmp_path):
    root, config, req = episode(tmp_path)
    cfg = read(config)
    cfg['commands']['action']['idempotent'] = False
    put(config, cfg)
    (root / 'crash-action').touch()
    for _ in range(2):
        assert controller(root, config).run_once()['status'] == 'BLOCKED_PREREQUISITE'
    stages = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert stages.count('action') == 1
    intents = list((root / 'qa/throughput/operations').glob('*/intent.json'))
    assert intents and read(intents[0])['operation_id']


def test_conflicting_duplicate_artifact_is_blocked_before_execution(tmp_path):
    root, config, req = episode(tmp_path)
    original_path = root / 'qa/request.json'
    duplicate = read(original_path)
    duplicate['verdict_path'] = str(root / 'qa/duplicate-verdict.json')
    duplicate['authorization_path'] = str(root / 'approval/duplicate-auth.json')
    duplicate_path = root / 'qa/duplicate-request.json'
    duplicate_binding = put(duplicate_path, duplicate)
    put(Path(duplicate['authorization_path']), {
        'schema': 'throughput-authorization/1', 'episode_id': root.name,
        'request_sha256': duplicate_binding['sha256'], 'allowed': True,
        'expires_at': duplicate['expires_at'], 'test_only': True,
    })
    duplicate['_sha256'] = duplicate_binding['sha256']
    verdict(duplicate, Path(duplicate['verdict_path']))
    cfg = read(config)
    cfg['requests'].append(str(duplicate_path))
    put(config, cfg)

    result = controller(root, config).run_once()

    assert result['status'] == 'BLOCKED_PREREQUISITE'
    assert 'conflicting duplicate artifact' in result['reason']
    assert len(read(root / 'qa/throughput/queue.json')['jobs']) == 1
    assert not (root / 'executions.jsonl').exists()


def add_request(root, config, req, track, frame, decision='PASS', predecessor=None):
    fresh = {k: v for k, v in req.items() if k != '_sha256'}
    fresh.update(request_id=frame + '-r1', track=track, frame=frame,
        verdict_path=str(root / 'qa' / (frame + '-verdict.json')), predecessor=predecessor,
        authorization_path=str(root / 'approval' / (frame + '-auth.json')))
    path = root / 'qa' / (frame + '-request.json')
    binding = put(path, fresh)
    put(Path(fresh['authorization_path']), {'schema': 'throughput-authorization/1', 'episode_id': root.name,
        'request_sha256': binding['sha256'], 'allowed': True, 'expires_at': fresh['expires_at'], 'test_only': True})
    fresh['_sha256'] = binding['sha256']
    verdict(fresh, Path(fresh['verdict_path']), decision)
    cfg = read(config)
    cfg['requests'].insert(0, str(path))
    put(config, cfg)
    return fresh


@pytest.mark.parametrize('track', ['thumbnail', 'final_video', 'publication', 'planning'])
def test_tracks_and_final_human_gates_do_not_block_frames(tmp_path, track):
    root, config, req = episode(tmp_path)
    add_request(root, config, req, track, 'THUMB')
    c = controller(root, config)
    c.run_once()
    queue = read(c.queue_path)
    assert next(j for j in queue['jobs'] if j['request']['request_id'] == req['request_id'])['status'] == 'DONE'
    other = queue['jobs'][0]
    assert other['status'] == ('DONE' if track == 'planning' else 'HUMAN_GATE')
    assert read(root / 'state.json')['checkpoint']['approved_assets'] == ['SC001']
    other_actions = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines() if json.loads(x)['frame'] == 'THUMB']
    assert 'promote' not in other_actions
    if track == 'planning':
        assert other_actions == ['planning']

def test_review_timeout_launches_bounded_fresh_route_then_blocks(tmp_path):
    root, config, req = episode(tmp_path, None)
    cfg = read(config)
    cfg['review_timeout'] = 0
    put(config, cfg)
    # SC002 intentionally emits a launch receipt without a verdict.
    req2 = add_request(root, config, req, 'frames', 'SC002')
    Path(req2['verdict_path']).unlink()
    cfg = read(config)
    cfg['requests'] = cfg['requests'][:1]
    put(config, cfg)
    result = controller(root, config).run_once()
    assert result['status'] == 'RETRY_SCHEDULED'
    result = controller(root, config).run_once()
    assert result['status'] == 'BLOCKED_PREREQUISITE'
    events = [json.loads(x) for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert [e['stage'] for e in events].count('review') == 2
    assert len({e['operation_id'] for e in events if e['stage'] == 'review'}) == 2

@pytest.mark.parametrize('decision', ['PASS', 'FAIL'])
def test_true_subprocess_multipass_cli_restart_and_verdict_arrival(tmp_path, decision):
    import subprocess
    import time
    root, config, req = episode(tmp_path, None)
    env = dict(os.environ, PYTHONPATH='')
    argv = [sys.executable, '-m', 'src.cli.main', 'throughput']
    common = ['--episode', str(root), '--config', str(config), '--test-mode']
    run = argv + ['run', *common, '--poll-interval', '0.05', '--max-seconds', '15']
    queue_path = root / 'qa/throughput/queue.json'
    def wait_for(process, predicate):
        end = time.monotonic() + 12
        while time.monotonic() < end:
            if process.poll() is not None:
                pytest.fail('controller exited before requested progress: ' + (root / 'cli.log').read_text())
            # A Windows reader can briefly lose delete sharing while the writer
            # atomically replaces the durable queue. That is not a stage failure.
            try:
                queue = read(queue_path) if queue_path.exists() else None
            except PermissionError:
                queue = None
            if queue and predicate(queue):
                return
            time.sleep(0.05)
        pytest.fail('controller did not autonomously progress')
    with (root / 'cli.log').open('w') as log:
        first = subprocess.Popen(run, env=env, stdout=log, stderr=log)
        try:
            wait_for(first, lambda q: q['jobs'][0]['status'] == 'WAITING_REVIEW')
        finally:
            first.terminate()
            first.wait(timeout=5)
        second = subprocess.Popen(run, env=env, stdout=log, stderr=log)
        try:
            verdict(req, Path(req['verdict_path']), decision)
            wait_for(second, lambda q: len(q['jobs']) == 2 and q['jobs'][0]['status'] == 'DONE' and q['jobs'][1]['status'] == 'WAITING_REVIEW')
        finally:
            second.terminate()
            second.wait(timeout=5)
    report = subprocess.run(argv + ['report', *common], env=env, capture_output=True, text=True, timeout=10)
    assert report.returncode == 0, report.stderr
    data = json.loads(report.stdout)
    assert set(data['durations_seconds']) == {'generation', 'review', 'remediation', 'idle', 'promotion'}
    assert data['durations_seconds']['generation' if decision == 'PASS' else 'remediation'] > 0
    assert data['durations_seconds']['review'] > 0
    assert 'oldest_ready_age_seconds' in data
    events = [json.loads(x) for x in (root / 'executions.jsonl').read_text().splitlines()]
    stages = [e['stage'] for e in events]
    assert stages.count('action') == (1 if decision == 'PASS' else 0)
    assert stages.count('remediation') == (1 if decision == 'FAIL' else 0)
    assert ('promote' in stages) is (decision == 'PASS')

def test_discover_ep8_binds_real_legacy_artifacts_and_reports_handler_blockers_without_writes(tmp_path):
    from src.throughput.discovery import discover
    root = tmp_path / 'EP8_FIXTURE'
    (root / 'qa').mkdir(parents=True)
    scripts = root / 'scripts'
    scripts.mkdir()
    request = put(root / 'qa/R027_zero_cost_R025_lineage_source_crop_parent_independent_preflight_review_request_v1.json',
        {'episode_id': root.name, 'frame_id': 'R027', 'allowed_decisions': ['PASS_PREFLIGHT_ONLY', 'FAIL_PREFLIGHT'],
         'bindings': {'R025_source_path': 'frames_v2/R025/source.png', 'R025_source_sha256': '0' * 64}})
    handoff = put(root / 'qa/R027_parent_hermes_independent_preflight_transport_recovery_v2.json',
        {'episode_id': root.name, 'frame_id': 'R027', 'decision': 'FAIL_PREFLIGHT',
         'record_type': 'PARENT_PERSISTED_EXTRACT_OF_INDEPENDENT_FAIL_NOT_FULL_VERDICT',
         'bindings': {'review_request': request}, 'blocking_findings': [{'code': 'MOUTH_AMBIGUOUS'}]})
    (scripts / 'r027_verdict_waiter_v2.py').write_text(
        "LOCK = ROOT / '.ep8_episode_writer.lock'\n"
        "while not VERDICT.exists():\n    pass\n"
        "while True:\n    pass\n")
    (scripts / 'r026_promote_r027_zero_cost_preflight_v1.py').write_text(
        "LOCK = ROOT / '.ep8_episode_writer.lock'\n")
    (scripts / 'r027_fail_recovery_acquire_lease_v1.py').write_text(
        "ROOT = r'C:\\\\HermesStudio\\\\episodes\\\\EP8_FIXTURE'\n"
        "LOCK = ROOT / '.ep8_episode_writer.lock'\n"
        "fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)\n"
        "replace(STATE, state)\n")
    before = {str(p): bind(p)['sha256'] for p in root.rglob('*') if p.is_file()}
    result = discover(root)
    assert result['request'] == request
    assert result['handoff'] == handoff
    assert result['decision'] == 'FAIL_PREFLIGHT'
    assert result['lock_path'] == str(root / '.ep8_episode_writer.lock')
    assert {Path(x['path']).name for x in result['selected_legacy_scripts']} == {
        'r027_verdict_waiter_v2.py', 'r026_promote_r027_zero_cost_preflight_v1.py',
        'r027_fail_recovery_acquire_lease_v1.py'}
    assert result['runnable'] is False
    assert result['waiter']['consumes_terminal_verdict'] is False
    assert result['handlers']['PASS']['status'] == 'MISSING'
    assert result['handlers']['FAIL']['status'] == 'UNSAFE_INCOMPLETE_LEGACY_HANDLER'
    assert 'reacquires the episode lock' in result['handlers']['FAIL']['blockers']
    assert 'does not materialize the promised remediation preflight' in result['handlers']['FAIL']['blockers']
    assert result['blocking_findings'] == [{'code': 'MOUTH_AMBIGUOUS'}]
    assert before == {str(p): bind(p)['sha256'] for p in root.rglob('*') if p.is_file()}


def test_ep8_fail_handoff_runs_end_to_end_only_in_explicit_test_clone(tmp_path, capsys):
    from src.throughput.discovery import run_test_clone
    source = tmp_path / 'EP8_SOURCE'
    (source / 'qa').mkdir(parents=True)
    (source / 'scripts').mkdir()
    image = source / 'frames_v2/R025/source.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (32, 18), 'green').save(image)
    source_binding = bind(image)
    request_path = source / 'qa/R027_zero_cost_R025_lineage_source_crop_parent_independent_preflight_review_request_v1.json'
    request = put(request_path, {'episode_id': source.name, 'frame_id': 'R027',
        'allowed_decisions': ['PASS_PREFLIGHT_ONLY', 'FAIL_PREFLIGHT'],
        'bindings': {'R025_source_path': 'frames_v2/R025/source.png',
                     'R025_source_sha256': source_binding['sha256']}})
    put(source / 'qa/R027_parent_hermes_independent_preflight_transport_recovery_v2.json',
        {'episode_id': source.name, 'frame_id': 'R027', 'decision': 'FAIL_PREFLIGHT',
         'record_type': 'PARENT_PERSISTED_EXTRACT_OF_INDEPENDENT_FAIL_NOT_FULL_VERDICT',
         'bindings': {'review_request': request},
         'blocking_findings': [{'code': 'SARAH_CLOSED_MOUTH_AMBIGUOUS_IN_EXACT_NATIVE_CROP'}]})
    (source / 'scripts/r027_verdict_waiter_v2.py').write_text(
        "LOCK = ROOT / '.ep8_episode_writer.lock'\nwhile True:\n    pass\n")
    (source / 'scripts/r026_promote_r027_zero_cost_preflight_v1.py').write_text(
        "LOCK = ROOT / '.ep8_episode_writer.lock'\n")
    (source / 'scripts/r027_fail_recovery_acquire_lease_v1.py').write_text(
        "LOCK = ROOT / '.ep8_episode_writer.lock'\n"
        "fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)\nreplace(STATE, state)\n")
    put(source / 'state.json', {'episode_id': source.name, 'current_state': 'PAUSED',
        'checkpoint': {'approved_assets': []}, 'state_history': [],
        'lease': {'status': 'STALE_LEGACY_WAITER', 'pid': 99999999}})
    put(source / 'costs.json', {'spent': 7.9, 'projected': 1.44,
        'hard_limit': 11.3, 'approved_maximum': 11.3})
    before = {str(p): bind(p)['sha256'] for p in source.rglob('*') if p.is_file()}
    destination = tmp_path / 'TEST_EP8_R027_CLONE'
    fixture_worker = Path(__file__).parents[1] / 'throughput_fixture_worker.py'

    result = run_test_clone(source, destination, fixture_worker=fixture_worker)

    assert result['status'] == 'WAITING_REVIEW'
    assert result['test_only'] is True
    assert result['source_decision'] == 'FAIL_PREFLIGHT'
    assert result['normalized_decision'] == 'FAIL'
    assert result['lease']['live'] is False
    assert Path(result['clone_root']) == destination.resolve()
    assert not (destination / '.episode_writer.lock').exists()
    assert before == {str(p): bind(p)['sha256'] for p in source.rglob('*') if p.is_file()}
    deployment = read(destination / 'TEST_ONLY/deployment.json')
    assert deployment['test_only'] is True
    normalized = read(destination / 'TEST_ONLY/normalized-verdict.json')
    assert normalized['test_only'] is True and normalized['decision'] == 'FAIL'
    stages = [json.loads(line)['stage'] for line in (destination / 'executions.jsonl').read_text().splitlines()]
    assert stages == ['remediation', 'transport', 'review']
    assert not (destination / 'frames_v2/R027').exists()
    assert read(destination / 'state.json')['current_state'] == 'PAUSED'
    assert read(destination / 'costs.json')['spent'] == 7.9

    from src.throughput.cli import main as throughput_main
    cli_destination = tmp_path / 'TEST_EP8_R027_CLI_CLONE'
    assert throughput_main(['clone-test', '--episode', str(source), '--output-dir',
                            str(cli_destination), '--test-mode']) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result['status'] == 'WAITING_REVIEW'
    assert cli_result['test_only'] is True
    assert cli_result['production_activated'] is False


@pytest.mark.parametrize('name', ['EP8_R027_CLONE', 'TEST_EP8_R027_CLONE'])
def test_ep8_test_clone_refuses_ambiguous_or_existing_destination(tmp_path, name):
    from src.throughput.discovery import run_test_clone
    source = tmp_path / 'source'
    source.mkdir()
    destination = tmp_path / name
    if name.startswith('TEST_'):
        destination.mkdir()
    with pytest.raises(ValueError):
        run_test_clone(source, destination, fixture_worker=Path(__file__))

def test_frame_dependency_is_revisited_after_predecessor_promotion_in_same_run(tmp_path):
    root, config, req = episode(tmp_path)
    add_request(root, config, req, 'frames', 'SC003', predecessor=req['request_id'])
    controller(root, config).run_once()
    actions = [json.loads(x)['frame'] for x in (root / 'executions.jsonl').read_text().splitlines() if json.loads(x)['stage'] == 'action']
    assert actions == ['SC001', 'SC003']

@pytest.mark.parametrize('mutation', ['candidate', 'action_receipt', 'request_snapshot', 'deployment', 'preflight'])
def test_restart_revalidates_chain_before_next_command(tmp_path, monkeypatch, mutation):
    root, config, req = episode(tmp_path)
    c = controller(root, config)
    original = c._step
    def stop(job):
        if job['stage'] == 'internal_qa':
            raise RuntimeError('restart boundary')
        return original(job)
    monkeypatch.setattr(c, '_step', stop)
    with pytest.raises(RuntimeError):
        c.run_once()
    queue = read(c.queue_path)
    job = queue['jobs'][0]
    if mutation == 'candidate':
        Image.new('RGB', (16, 9), 'red').save(Path(job['candidate']['path']))
    elif mutation == 'action_receipt':
        path = Path(job['receipts']['action']['path'])
        value = read(path)
        value['test_only'] = False
        put(path, value)
    elif mutation == 'request_snapshot':
        job['request']['estimate'] = 100
        put(c.queue_path, queue)
    elif mutation == 'deployment':
        cfg = read(config)
        cfg['max_remediations'] += 1
        put(config, cfg)
    else:
        verdict(req, Path(req['verdict_path']), 'FAIL')
    result = controller(root, config).run_once()
    assert result['status'] == 'BLOCKED_PREREQUISITE'
    stages = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert stages == ['action']

@pytest.mark.parametrize('stage,fields', [('action', {'test_only': False}), ('action', {'started_at': '2000-01-01T00:00:00+00:00'}), ('internal_qa', {'started_at': '2000-01-01T00:00:00+00:00'}), ('action', {'unexpected': True})])
def test_command_receipts_require_mode_closed_schema_and_temporal_order(tmp_path, stage, fields):
    root, config, req = episode(tmp_path)
    put(root / 'bad-receipt.json', {'stage': stage, 'fields': fields})
    result = controller(root, config).run_once()
    assert result['status'] == 'BLOCKED_PREREQUISITE'
    stages = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert stages == (['action'] if stage == 'action' else ['action', 'internal_qa'])

def test_internal_qa_fail_executes_remediation_instead_of_stopping(tmp_path):
    root, config, req = episode(tmp_path)
    put(root / 'bad-receipt.json', {'stage': 'internal_qa', 'fields': {'result': {'decision': 'FAIL', 'source_sha256': req['source']['sha256']}}})
    result = controller(root, config).run_once()
    assert result['status'] == 'WAITING_REVIEW'
    stages = [json.loads(x)['stage'] for x in (root / 'executions.jsonl').read_text().splitlines()]
    assert stages == ['action', 'internal_qa', 'remediation', 'transport', 'review']
