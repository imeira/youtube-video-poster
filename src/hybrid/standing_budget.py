"""Shared, exact-money admission ledger. This is not a grant of LIVE authority.

FAL billing claims are explicitly bound to a durable executor scope and may be
settled only by the authenticated readback bridge; legacy TEST claims retain
separate compatibility semantics."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from src.hybrid._billing_coordinator_guard import _require_coordinator_write


class BudgetDenied(ValueError):
    """An operation cannot reserve funds under the standing policy."""


def _units(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise BudgetDenied('known finite USD amount required') from error
    if not amount.is_finite() or amount < 0 or amount * 1000000 != (amount * 1000000).to_integral_value():
        raise BudgetDenied('nonnegative finite USD with at most six decimals required')
    units = int(amount * 1000000)
    if units > 2**63 - 1:
        raise BudgetDenied('USD amount exceeds ledger range')
    return units


def _usd(units):
    return f'{Decimal(units) / 1000000:.6f}'


def validate_canonical_settlement_receipt(receipt_body: str) -> tuple[dict, int, str]:
    """Validate the one closed FAL settlement representation without mutating.

    Snapshot consumers use this same validator; a recomputed envelope digest is
    not a substitute for the ledger's canonical financial receipt contract.
    """
    if not isinstance(receipt_body, str):
        raise BudgetDenied('canonical receipt body required')
    try:
        receipt = json.loads(receipt_body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BudgetDenied('canonical receipt JSON required') from exc
    required = {
        'schema', 'authority', 'local_request_id', 'provider_request_id',
        'endpoint_id', 'auth_scope', 'authority_mode', 'currency',
        'cost_total_micro_usd', 'evidence_hash', 'provenance',
    }
    if not isinstance(receipt, dict) or set(receipt) != required or receipt['schema'] != 2:
        raise BudgetDenied('closed settlement receipt schema-v2 required')
    if receipt['authority'] not in {'TRANSPORT_AUTHENTICATED_FAL_READBACK', 'TEST_NOT_AUTHORITY'} or receipt['provenance'] != 'FAL_BILLING_READBACK':
        raise BudgetDenied('authenticated FAL receipt provenance required')
    if receipt['currency'] != 'USD' or not isinstance(receipt['cost_total_micro_usd'], str) or not re.fullmatch(r'0|[1-9][0-9]*', receipt['cost_total_micro_usd']):
        raise BudgetDenied('canonical USD micro amount required')
    actual = _units(Decimal(receipt['cost_total_micro_usd']) / Decimal(1_000_000))
    for key in ('local_request_id', 'provider_request_id', 'endpoint_id', 'auth_scope', 'authority_mode'):
        if not isinstance(receipt[key], str) or not receipt[key] or len(receipt[key]) > 512:
            raise BudgetDenied('settlement receipt binding is malformed')
    if receipt['authority_mode'] not in {'TEST', 'LIVE'} or not isinstance(receipt['evidence_hash'], str) or not re.fullmatch('[a-f0-9]{64}', receipt['evidence_hash']):
        raise BudgetDenied('settlement receipt evidence is malformed')
    canonical_body = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if receipt_body != canonical_body:
        raise BudgetDenied('settlement receipt is not canonical')
    return receipt, actual, sha256(canonical_body.encode('utf-8')).hexdigest()


class StandingBudgetLedger:
    @classmethod
    def from_authorized_policy(cls, path, policy=None, *, loader=None, receipt_path=None, clock=None):
        """Construct only from a generic authenticated preflight projection.

        The caller must obtain ``policy`` through an authenticated policy loader;
        this ledger deliberately does not parse arbitrary JSON authority files.
        """
        if policy is not None:
            raise BudgetDenied('authenticated loader is required; policy snapshots are not authority')
        # Import lazily to avoid the ledger/authority module import cycle.
        from src.hybrid.budget_authorization import AuthorizedBudgetPolicyLoader
        if not isinstance(loader, AuthorizedBudgetPolicyLoader) or receipt_path is None:
            raise BudgetDenied('authenticated loader and receipt are required')
        policy = loader.load(receipt_path)
        required = (
            'request_id', 'policy_sha256', 'episode_cap_usd', 'month_cap_usd',
            'experiment_cap_usd', 'mode',
        )
        if any(not hasattr(policy, name) for name in required) or policy.mode != 'TEST':
            raise BudgetDenied('authenticated TEST budget policy is required')
        return cls(
            path,
            policy_sha256=policy.policy_sha256,
            episode_limit_usd=policy.episode_cap_usd,
            month_limit_usd=policy.month_cap_usd,
            experiment_limit_usd=policy.experiment_cap_usd,
            mode=policy.mode, clock=clock, _authenticated_authority=True,
            _authorization_id=policy.request_id,
        )

    def __init__(self, path, *, policy_sha256, episode_limit_usd,
                 month_limit_usd, experiment_limit_usd, mode, clock=None,
                 _authenticated_authority=False, _authorization_id=None):
        if not isinstance(policy_sha256, str) or not re.fullmatch('[a-f0-9]{64}', policy_sha256):
            raise BudgetDenied('policy hash required')
        if mode not in {'TEST', 'LIVE'}:
            raise BudgetDenied('explicit TEST or LIVE mode required')
        if _authenticated_authority and (not isinstance(_authorization_id, str) or not _authorization_id.strip()):
            raise BudgetDenied('authenticated authorization id required')
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.limits = {'episode': _units(episode_limit_usd), 'month': _units(month_limit_usd),
                       'experiment': _units(experiment_limit_usd)}
        self.binding = {'schema': 1, 'policy_sha256': policy_sha256, 'mode': mode,
                        'currency': 'USD',
                        'authority': 'authenticated' if _authenticated_authority else 'direct',
                        **self.limits}
        if _authenticated_authority:
            self.binding['authorization_id'] = _authorization_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS claims (
                  request_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, provider TEXT NOT NULL,
                  month TEXT NOT NULL, cap INTEGER NOT NULL CHECK(cap>=0),
                  experiment INTEGER NOT NULL, state TEXT NOT NULL, actual INTEGER,
                  settlement_mode TEXT NOT NULL DEFAULT 'LEGACY_TEST_UNBOUND',
                  auth_scope TEXT NOT NULL DEFAULT 'TEST_UNBOUND_LEGACY',
                  provider_request_id TEXT NOT NULL DEFAULT 'UNASSIGNED',
                  authority_mode TEXT NOT NULL DEFAULT 'TEST',
                  billing_snapshot_generation INTEGER);
                CREATE TABLE IF NOT EXISTS settlement_receipts (
                  request_id TEXT PRIMARY KEY REFERENCES claims(request_id),
                  receipt_sha256 TEXT NOT NULL UNIQUE, actual INTEGER NOT NULL CHECK(actual>=0),
                  receipt_body TEXT NOT NULL, provenance TEXT NOT NULL);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(claims)')}
            if 'settlement_mode' not in columns:
                db.execute("ALTER TABLE claims ADD COLUMN settlement_mode TEXT NOT NULL DEFAULT 'LEGACY_TEST_UNBOUND'")
            if 'auth_scope' not in columns:
                db.execute("ALTER TABLE claims ADD COLUMN auth_scope TEXT NOT NULL DEFAULT 'TEST_UNBOUND_LEGACY'")
            if 'provider_request_id' not in columns:
                db.execute("ALTER TABLE claims ADD COLUMN provider_request_id TEXT NOT NULL DEFAULT 'UNASSIGNED'")
            if 'authority_mode' not in columns:
                db.execute("ALTER TABLE claims ADD COLUMN authority_mode TEXT NOT NULL DEFAULT 'TEST'")
            if 'billing_snapshot_generation' not in columns:
                db.execute("ALTER TABLE claims ADD COLUMN billing_snapshot_generation INTEGER")
            db.execute('BEGIN IMMEDIATE')
            stored = db.execute('SELECT binding FROM policy WHERE id=1').fetchone()
            if stored and json.loads(stored[0]) != self.binding:
                raise BudgetDenied('immutable policy mismatch')
            db.execute('INSERT OR IGNORE INTO policy VALUES(1,?)', (json.dumps(self.binding, sort_keys=True),))

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            stored = db.execute('SELECT binding FROM policy WHERE id=1').fetchone()
            if not stored or json.loads(stored[0]) != self.binding:
                raise BudgetDenied('immutable policy mismatch')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _month(self):
        now = self.clock()
        if now.tzinfo is None:
            raise BudgetDenied('timezone-aware UTC clock required')
        return now.astimezone(UTC).strftime('%Y-%m')

    @staticmethod
    def _claim(row):
        return {'request_id': row['request_id'], 'episode_id': row['episode_id'],
                'provider': row['provider'], 'month': row['month'],
                'reserved_usd': _usd(row['cap']), 'state': row['state'],
                'actual_usd': None if row['actual'] is None else _usd(row['actual']),
                'experiment': bool(row['experiment']), 'settlement_mode': row['settlement_mode'],
                'auth_scope': row['auth_scope'],
                'provider_request_id': row['provider_request_id'],
                'authority_mode': row['authority_mode'],
                'billing_snapshot_generation': row['billing_snapshot_generation']}

    def reserve(self, request_id, episode_id, provider, quoted_cap_usd, *, experiment=False,
                settlement_mode='LEGACY_TEST_UNBOUND', auth_scope='TEST_UNBOUND_LEGACY',
                provider_request_id='UNASSIGNED', authority_mode=None,
                billing_snapshot_generation=None):
        if not all(isinstance(x, str) and x.strip() for x in (request_id, episode_id, provider)):
            raise BudgetDenied('request, episode and provider required')
        if type(experiment) is not bool:
            raise BudgetDenied('experiment flag must be boolean')
        if billing_snapshot_generation is not None and (type(billing_snapshot_generation) is not int or billing_snapshot_generation < 1):
            raise BudgetDenied('billing snapshot generation must be a positive integer')
        try:
            _require_coordinator_write(billing_snapshot_generation)
        except PermissionError as error:
            raise BudgetDenied(str(error)) from error
        if settlement_mode not in {'LEGACY_TEST_UNBOUND', 'FAL_READBACK_V2'}:
            raise BudgetDenied('explicit settlement mode required')
        if not isinstance(auth_scope, str) or not auth_scope or len(auth_scope) > 512:
            raise BudgetDenied('settlement auth scope required')
        if not isinstance(provider_request_id, str) or not provider_request_id or len(provider_request_id) > 512:
            raise BudgetDenied('provider request id required')
        if authority_mode is None:
            authority_mode = 'TEST' if settlement_mode == 'LEGACY_TEST_UNBOUND' else self.binding['mode']
        if authority_mode not in {'TEST', 'LIVE'} or authority_mode != self.binding['mode']:
            raise BudgetDenied('settlement authority mode mismatch')
        if settlement_mode == 'LEGACY_TEST_UNBOUND' and (
            authority_mode != 'TEST' or auth_scope != 'TEST_UNBOUND_LEGACY'
            or provider_request_id != 'UNASSIGNED'
        ):
            raise BudgetDenied('legacy claims are TEST-only and cannot bind FAL')
        cap = _units(quoted_cap_usd)
        month = self._month()
        with self._transaction() as db:
            previous = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if (previous['episode_id'], previous['provider'], previous['cap'], bool(previous['experiment']), previous['settlement_mode'], previous['auth_scope'], previous['provider_request_id'], previous['authority_mode']) != (episode_id, provider, cap, experiment, settlement_mode, auth_scope, provider_request_id, authority_mode):
                    raise BudgetDenied('request identity collision')
                return self._claim(previous)
            if db.execute('SELECT 1 FROM claims WHERE actual > cap LIMIT 1').fetchone():
                raise BudgetDenied('provider overrun freeze requires explicit reconciliation')
            episode_total = month_total = experiment_total = 0
            for row in db.execute('SELECT * FROM claims'):
                exposure = row['actual'] if row['actual'] is not None else row['cap']
                if row['episode_id'] == episode_id:
                    episode_total += exposure
                # Unreconciled liabilities do not disappear at a calendar rollover.
                if row['month'] == month or row['actual'] is None:
                    month_total += exposure
                    if row['experiment']:
                        experiment_total += exposure
            for kind, total in [('episode', episode_total), ('month', month_total)]:
                if total + cap > self.limits[kind]:
                    raise BudgetDenied(f'{kind} budget exhausted')
            if experiment and experiment_total + cap > self.limits['experiment']:
                raise BudgetDenied('experiment budget exhausted')
            db.execute('INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, episode_id, provider, month, cap, int(experiment), 'RESERVED', None, settlement_mode, auth_scope, provider_request_id, authority_mode, billing_snapshot_generation))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def bind_provider_request_id(self, request_id, provider_request_id, *, billing_snapshot_generation=None):
        """Attach the one remote ID after the executor checkpoint has fsynced."""
        if (not isinstance(request_id, str) or not request_id or not isinstance(provider_request_id, str)
                or not provider_request_id or provider_request_id == 'UNASSIGNED' or len(provider_request_id) > 512):
            raise BudgetDenied('durable provider request id required')
        if billing_snapshot_generation is not None and (
            type(billing_snapshot_generation) is not int or billing_snapshot_generation < 1
        ):
            raise BudgetDenied('billing snapshot generation must be a positive integer')
        try:
            _require_coordinator_write(billing_snapshot_generation)
        except PermissionError as error:
            raise BudgetDenied(str(error)) from error
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None or row['settlement_mode'] != 'FAL_READBACK_V2':
                raise BudgetDenied('FAL-bound reservation required')
            if row['billing_snapshot_generation'] is not None and billing_snapshot_generation is None:
                raise BudgetDenied('generation-pinned FAL claim requires coordinator capability')
            prior = row['provider_request_id']
            if prior == provider_request_id:
                return self._claim(row)
            if prior != 'UNASSIGNED':
                raise BudgetDenied('provider request id is immutable')
            if billing_snapshot_generation is None:
                db.execute('UPDATE claims SET provider_request_id=? WHERE request_id=?', (provider_request_id, request_id))
            else:
                db.execute('UPDATE claims SET provider_request_id=?, billing_snapshot_generation=? WHERE request_id=?',
                           (provider_request_id, billing_snapshot_generation, request_id))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def mark_unknown(self, request_id, *, billing_snapshot_generation=None):
        if billing_snapshot_generation is not None and (
            type(billing_snapshot_generation) is not int or billing_snapshot_generation < 1
        ):
            raise BudgetDenied('billing snapshot generation must be a positive integer')
        try:
            _require_coordinator_write(billing_snapshot_generation)
        except PermissionError as error:
            raise BudgetDenied(str(error)) from error
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetDenied('reservation does not exist')
            if row['billing_snapshot_generation'] is not None and billing_snapshot_generation is None:
                raise BudgetDenied('generation-pinned FAL claim requires coordinator capability')
            if row['actual'] is not None:
                raise BudgetDenied('settled actual is immutable')
            if billing_snapshot_generation is None:
                db.execute("UPDATE claims SET state='UNKNOWN' WHERE request_id=?", (request_id,))
            else:
                db.execute("UPDATE claims SET state='UNKNOWN', billing_snapshot_generation=? WHERE request_id=?",
                           (billing_snapshot_generation, request_id))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def settle(self, request_id, actual_usd):
        actual = _units(actual_usd)
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetDenied('reservation does not exist')
            if row['settlement_mode'] == 'FAL_READBACK_V2':
                raise BudgetDenied('FAL-bound claim requires authenticated settlement')
            if row['actual'] is not None:
                if row['actual'] != actual:
                    raise BudgetDenied('settled actual is immutable')
                return self._claim(row)
            db.execute("UPDATE claims SET state='SETTLED', actual=? WHERE request_id=?", (actual, request_id))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def claim(self, request_id):
        if not isinstance(request_id, str) or not request_id:
            raise BudgetDenied('request id required')
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetDenied('reservation does not exist')
            return self._claim(row)

    def _set_billing_snapshot_generation(self, request_id, generation):
        if type(generation) is not int or generation < 1:
            raise BudgetDenied('billing snapshot generation must be a positive integer')
        try:
            _require_coordinator_write(generation)
        except PermissionError as error:
            raise BudgetDenied(str(error)) from error
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetDenied('reservation does not exist')
            db.execute('UPDATE claims SET billing_snapshot_generation=? WHERE request_id=?',
                       (generation, request_id))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def _settle_verified_receipt(self, receipt_body, *, billing_snapshot_generation=None):
        """Parse and settle the closed readback receipt produced by the bridge.

        The leading underscore is intentional: Python modules are a trusted
        composition boundary, not an object-capability sandbox.  Untrusted
        callers get the CLI/reconciler path; arbitrary in-process code can
        always mutate process memory and is not a financial trust root.
        """
        if not isinstance(receipt_body, str):
            raise BudgetDenied('canonical receipt body required')
        if billing_snapshot_generation is not None and (
            type(billing_snapshot_generation) is not int or billing_snapshot_generation < 1
        ):
            raise BudgetDenied('billing snapshot generation must be a positive integer')
        try:
            _require_coordinator_write(billing_snapshot_generation)
        except PermissionError as error:
            raise BudgetDenied(str(error)) from error
        try:
            receipt = json.loads(receipt_body)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BudgetDenied('canonical receipt JSON required') from exc
        required = {
            'schema', 'authority', 'local_request_id', 'provider_request_id',
            'endpoint_id', 'auth_scope', 'authority_mode', 'currency',
            'cost_total_micro_usd', 'evidence_hash', 'provenance',
        }
        if not isinstance(receipt, dict) or set(receipt) != required or receipt['schema'] != 2:
            raise BudgetDenied('closed settlement receipt schema-v2 required')
        if receipt['authority'] not in {'TRANSPORT_AUTHENTICATED_FAL_READBACK', 'TEST_NOT_AUTHORITY'} or receipt['provenance'] != 'FAL_BILLING_READBACK':
            raise BudgetDenied('authenticated FAL receipt provenance required')
        if receipt['currency'] != 'USD' or not isinstance(receipt['cost_total_micro_usd'], str) or not re.fullmatch(r'0|[1-9][0-9]*', receipt['cost_total_micro_usd']):
            raise BudgetDenied('canonical USD micro amount required')
        actual = _units(Decimal(receipt['cost_total_micro_usd']) / Decimal(1_000_000))
        for key in ('local_request_id', 'provider_request_id', 'endpoint_id', 'auth_scope', 'authority_mode'):
            if not isinstance(receipt[key], str) or not receipt[key] or len(receipt[key]) > 512:
                raise BudgetDenied('settlement receipt binding is malformed')
        if receipt['authority_mode'] not in {'TEST', 'LIVE'} or not isinstance(receipt['evidence_hash'], str) or not re.fullmatch('[a-f0-9]{64}', receipt['evidence_hash']):
            raise BudgetDenied('settlement receipt evidence is malformed')
        canonical_body = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if receipt_body != canonical_body:
            raise BudgetDenied('settlement receipt is not canonical')
        receipt_sha256 = sha256(canonical_body.encode('utf-8')).hexdigest()
        request_id = receipt['local_request_id']
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetDenied('reservation does not exist')
            if row['billing_snapshot_generation'] is not None and billing_snapshot_generation is None:
                raise BudgetDenied('generation-pinned FAL claim requires coordinator capability')
            if row['settlement_mode'] != 'FAL_READBACK_V2':
                raise BudgetDenied('claim is not FAL-bound')
            if (row['provider'], row['provider_request_id'], row['auth_scope'], row['authority_mode']) != (
                receipt['endpoint_id'], receipt['provider_request_id'], receipt['auth_scope'], receipt['authority_mode']
            ):
                raise BudgetDenied('receipt does not bind durable claim')
            if receipt['authority'] == 'TEST_NOT_AUTHORITY' and row['authority_mode'] != 'TEST':
                raise BudgetDenied('TEST billing fixture cannot settle a LIVE claim')
            existing = db.execute(
                'SELECT receipt_sha256, actual, receipt_body, provenance FROM settlement_receipts WHERE request_id=?', (request_id,)
            ).fetchone()
            if existing:
                if (existing['receipt_sha256'] != receipt_sha256 or existing['actual'] != actual
                        or existing['receipt_body'] != canonical_body or existing['provenance'] != receipt['provenance']):
                    raise BudgetDenied('authenticated settlement receipt is immutable')
                if row['actual'] != actual or row['state'] != 'SETTLED':
                    raise BudgetDenied('authenticated settlement state mismatch')
                return self._claim(row)
            if row['actual'] is not None:
                raise BudgetDenied('legacy settlement lacks authenticated receipt')
            db.execute('INSERT INTO settlement_receipts VALUES(?,?,?,?,?)', (request_id, receipt_sha256, actual, canonical_body, receipt['provenance']))
            if billing_snapshot_generation is None:
                db.execute("UPDATE claims SET state='SETTLED', actual=? WHERE request_id=?", (actual, request_id))
            else:
                db.execute("UPDATE claims SET state='SETTLED', actual=?, billing_snapshot_generation=? WHERE request_id=?",
                           (actual, billing_snapshot_generation, request_id))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def exposure(self):
        with self._transaction() as db:
            episodes, months, experiments = defaultdict(int), defaultdict(int), defaultdict(int)
            current_month = self._month()
            current_month_effective = carried_liability = 0
            for row in db.execute('SELECT * FROM claims'):
                value = row['actual'] if row['actual'] is not None else row['cap']
                episodes[row['episode_id']] += value
                months[row['month']] += value
                if row['experiment']:
                    experiments[row['month']] += value
                if row['month'] == current_month:
                    current_month_effective += value
                elif row['actual'] is None:
                    carried_liability += row['cap']
                    current_month_effective += row['cap']
            return {'frozen': bool(db.execute('SELECT 1 FROM claims WHERE actual > cap LIMIT 1').fetchone()),
                    'episode_usd': {k: _usd(v) for k, v in episodes.items()},
                    'month_usd': {k: _usd(v) for k, v in months.items()},
                    'experiment_usd': {k: _usd(v) for k, v in experiments.items()},
                    'carried_liability_usd': _usd(carried_liability),
                    'current_month_effective_usd': _usd(current_month_effective)}
