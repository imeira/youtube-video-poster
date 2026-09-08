"""TEST-only subprocess adapter. No provider imports, network or production evidence."""
import argparse
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    os.replace(temp, path)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def binding(path):
    return {'path': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def read_json_retry(path, timeout=2):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return json.loads(Path(path).read_text())
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.005)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--receipt', required=True)
    args = parser.parse_args()
    task = json.loads(Path(args.input).read_text())
    assert task['test_only'] is True
    stage, req = task['stage'], task['request']
    root = Path(task['episode_root'])
    out = Path(args.receipt).parent
    started = now()
    write(task['start_claim_path'], {'schema': 'throughput-start-claim/1',
        'operation_id': task['operation_id'], 'pid': os.getpid(), 'claimed_at': started})
    gate_path = Path(task['start_gate_path'])
    deadline = time.monotonic() + 5
    while not gate_path.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    gate = read_json_retry(gate_path)
    assert gate['operation_id'] == task['operation_id']
    assert gate['child_pid'] == os.getpid()
    lease = read_json_retry(task['controller_lease_path'])
    active = lease.get('active_operation', {})
    with (root / 'executions.jsonl').open('a') as f:
        f.write(json.dumps({'stage': stage, 'operation_id': task['operation_id'], 'frame': req['frame'],
            'lease_child_pid_bound': active.get('pid') == os.getpid(),
            'lease_operation_id_bound': active.get('operation_id') == task['operation_id']}) + '\n')
    result = {}
    if stage == 'action' and (root / 'crash-action').exists():
        os._exit(9)
    if stage == 'action' and (root / 'unknown.json').exists():
        result = {'status': 'UNKNOWN_RECONCILIATION_REQUIRED', 'request_id': json.loads((root / 'unknown.json').read_text())['request_id']}
    elif stage == 'action':
        target = out / 'candidate.png'
        shutil.copyfile(req['source']['path'], target)
        result = {'candidate': binding(target)}
    elif stage == 'internal_qa':
        from PIL import Image
        with Image.open(task['candidate']['path']) as im:
            im.load()
            assert list(im.size) == [req['width'], req['height']]
        result = {'decision': 'PASS', 'source_sha256': task['candidate']['sha256']}
    elif stage == 'review':
        if task['review_binding']['phase'] == 'qa' and req['frame'] in ('SC001', 'THUMB') and req['revision'] == 1:
            proof = dict(task['review_binding'], decision='PASS', timestamp=now(), test_only=True)
            origin = dict(schema='reviewer-origin/1', reviewer_id='fixture-reviewer',
                          execution_context='isolated-test-review', result_path=str(out / 'proof.json'),
                          result_sha256=write(out / 'proof.json', proof)['sha256'])
            write(task['verdict_path'], dict(proof, reviewer_origin=origin))
        result = {'launched': True}
    elif stage in ('successor', 'remediation'):
        fresh = dict(req)
        fresh['frame'] = 'SC002' if stage == 'successor' else req['frame']
        fresh['revision'] = 1 if stage == 'successor' else req['revision'] + 1
        fresh['request_id'] = req['request_id'] + '-' + stage
        fresh['created_at'] = now()
        fresh['verdict_path'] = str(out / 'fresh-verdict.json')
        fresh['predecessor'] = req['request_id'] if stage == 'successor' else req.get('predecessor')
        result = {'request': write(out / 'fresh-request.json', fresh)}
    elif stage == 'promote':
        result = {'manifest': write(out / 'manifest.json', {'schema': 'throughput-manifest/1',
            'test_only': True, 'request_id': req['request_id'], 'candidate': task['candidate'],
            'qa': task['qa'], 'frame': req['frame']})}
    elif stage == 'transport':
        result = {'available': True}
    elif stage == 'planning':
        result = {'plan': write(out / 'plan.json', {'test_only': True, 'frame': req['frame']})}
    elif stage == 'reconcile':
        write(root / 'reconciled.json', {'request_id': task['opaque_request_id']})
        result = {'request_id': task['opaque_request_id'], 'status': 'PENDING'}
    receipt = {'schema': 'throughput-command/1', 'test_only': True,
        'operation_id': task['operation_id'], 'input_sha256': hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
        'stage': stage, 'started_at': started, 'finished_at': now(), 'result': result}
    bad = root / 'bad-receipt.json'
    if bad.exists():
        corruption = json.loads(bad.read_text())
        if stage == corruption['stage']:
            receipt.update(corruption['fields'])
    write(args.receipt, receipt)

if __name__ == '__main__':
    main()
