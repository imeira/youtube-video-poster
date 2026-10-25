"""Shared, exact-money admission ledger. This is not a grant of LIVE authority."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


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
                  experiment INTEGER NOT NULL, state TEXT NOT NULL, actual INTEGER);
            ''')
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
                'experiment': bool(row['experiment'])}

    def reserve(self, request_id, episode_id, provider, quoted_cap_usd, *, experiment=False):
        if not all(isinstance(x, str) and x.strip() for x in (request_id, episode_id, provider)):
            raise BudgetDenied('request, episode and provider required')
        if type(experiment) is not bool:
            raise BudgetDenied('experiment flag must be boolean')
        cap = _units(quoted_cap_usd)
        month = self._month()
        with self._transaction() as db:
            previous = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if (previous['episode_id'], previous['provider'], previous['cap'], bool(previous['experiment'])) != (episode_id, provider, cap, experiment):
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
            db.execute('INSERT INTO claims VALUES(?,?,?,?,?,?,?,NULL)',
                       (request_id, episode_id, provider, month, cap, int(experiment), 'RESERVED'))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def mark_unknown(self, request_id):
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetDenied('reservation does not exist')
            if row['actual'] is not None:
                raise BudgetDenied('settled actual is immutable')
            db.execute("UPDATE claims SET state='UNKNOWN' WHERE request_id=?", (request_id,))
            return self._claim(db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone())

    def settle(self, request_id, actual_usd):
        actual = _units(actual_usd)
        with self._transaction() as db:
            row = db.execute('SELECT * FROM claims WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise BudgetDenied('reservation does not exist')
            if row['actual'] is not None:
                if row['actual'] != actual:
                    raise BudgetDenied('settled actual is immutable')
                return self._claim(row)
            db.execute("UPDATE claims SET state='SETTLED', actual=? WHERE request_id=?", (actual, request_id))
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
