"""Admission tests use no providers and cannot authorize actual media spend."""
from datetime import UTC
from decimal import Decimal

import pytest


def ledger(tmp_path, **kwargs):
    from src.hybrid.standing_budget import StandingBudgetLedger
    return StandingBudgetLedger(
        tmp_path / 'shared.sqlite3', policy_sha256='a' * 64,
        episode_limit_usd='1', month_limit_usd='1.50',
        experiment_limit_usd='0.25', mode='TEST', **kwargs,
    )


def test_shared_ledger_reserves_before_spend_and_blocks_cumulative_episode(tmp_path):
    from src.hybrid.standing_budget import BudgetDenied
    book = ledger(tmp_path)
    claim = book.reserve('req1', 'ep1', 'provider', Decimal('0.60'))
    assert claim['state'] == 'RESERVED'
    with pytest.raises(BudgetDenied, match='episode'):
        book.reserve('req2', 'ep1', 'provider', Decimal('0.50'))
    assert book.exposure()['episode_usd']['ep1'] == '0.600000'


def test_unknown_keeps_reservation_until_immutable_settlement(tmp_path):
    from src.hybrid.standing_budget import BudgetDenied
    book = ledger(tmp_path)
    book.reserve('req1', 'ep1', 'provider', '0.80')
    unknown = book.mark_unknown('req1')
    assert unknown['actual_usd'] is None and unknown['state'] == 'UNKNOWN'
    with pytest.raises(BudgetDenied):
        book.reserve('req2', 'ep1', 'provider', '0.30')
    actual = book.settle('req1', '0.20')
    assert actual['actual_usd'] == '0.200000'
    assert book.settle('req1', '0.20') == actual
    with pytest.raises(BudgetDenied, match='immutable'):
        book.settle('req1', '0.10')
    book.reserve('req2', 'ep1', 'provider', '0.30')
    assert book.exposure()['episode_usd']['ep1'] == '0.500000'


def test_provider_overrun_is_recorded_and_freezes_new_reservations(tmp_path):
    from src.hybrid.standing_budget import BudgetDenied
    book = ledger(tmp_path)
    book.reserve('req1', 'ep1', 'provider', '0.10')
    result = book.settle('req1', '0.15')
    assert result['actual_usd'] == '0.150000'
    restarted = ledger(tmp_path)
    with pytest.raises(BudgetDenied, match='overrun'):
        restarted.reserve('req2', 'ep2', 'provider', '0.01')
    assert restarted.exposure()['frozen'] is True


@pytest.mark.parametrize('amount', [None, 'NaN', 'Infinity', '-0.01', '0.0000001', True])
def test_invalid_or_unknown_price_never_creates_claim(tmp_path, amount):
    from src.hybrid.standing_budget import BudgetDenied
    book = ledger(tmp_path)
    with pytest.raises(BudgetDenied):
        book.reserve('bad', 'ep', 'provider', amount)
    assert book.exposure()['episode_usd'] == {}


def test_monthly_budget_is_shared_across_episodes(tmp_path):
    from src.hybrid.standing_budget import BudgetDenied
    first = ledger(tmp_path)
    second = ledger(tmp_path)
    first.reserve('a', 'ep1', 'p', '0.90')
    with pytest.raises(BudgetDenied, match='month'):
        second.reserve('b', 'ep2', 'p', '0.70')
    second.reserve('c', 'ep2', 'p', '0.60')


def test_request_identity_and_policy_cannot_be_reinterpreted(tmp_path):
    from src.hybrid.standing_budget import BudgetDenied, StandingBudgetLedger
    book = ledger(tmp_path)
    first = book.reserve('a', 'ep1', 'p', '0.20')
    assert book.reserve('a', 'ep1', 'p', '0.20') == first
    with pytest.raises(BudgetDenied, match='collision'):
        book.reserve('a', 'ep2', 'p', '0.20')
    with pytest.raises(BudgetDenied, match='policy'):
        StandingBudgetLedger(tmp_path / 'shared.sqlite3', policy_sha256='b' * 64,
                             episode_limit_usd='5', month_limit_usd='5',
                             experiment_limit_usd='5', mode='TEST')


def test_experiment_ceiling_and_calendar_rollover(tmp_path):
    from datetime import datetime

    from src.hybrid.standing_budget import BudgetDenied
    now = [datetime(2026, 10, 31, 23, 59, tzinfo=UTC)]
    book = ledger(tmp_path, clock=lambda: now[0])
    book.reserve('a', 'ep1', 'p', '0.20', experiment=True)
    with pytest.raises(BudgetDenied, match='experiment'):
        book.reserve('b', 'ep2', 'p', '0.10', experiment=True)
    book.reserve('c', 'ep1', 'p', '0.70')
    book.settle('a', '0.20')
    book.settle('c', '0.70')
    now[0] = datetime(2026, 11, 1, tzinfo=UTC)
    book.reserve('d', 'ep2', 'p', '0.20', experiment=True)
    with pytest.raises(BudgetDenied, match='episode'):
        book.reserve('e', 'ep1', 'p', '0.20')
    assert book.exposure()['month_usd'] == {'2026-10': '0.900000', '2026-11': '0.200000'}


def test_two_workers_cannot_double_reserve_last_capacity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from src.hybrid.standing_budget import BudgetDenied
    workers = [ledger(tmp_path), ledger(tmp_path)]
    ready = Barrier(2)
    def claim(index):
        ready.wait(timeout=10)
        try:
            workers[index].reserve(f'req-{index}', 'shared-ep', 'provider', '0.60')
            return 'reserved'
        except BudgetDenied:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(claim, [0, 1]))
    assert sorted(result) == ['blocked', 'reserved']
    assert workers[0].exposure()['episode_usd']['shared-ep'] == '0.600000'


def test_unresolved_exposure_carries_into_next_month(tmp_path):
    from datetime import datetime

    from src.hybrid.standing_budget import BudgetDenied
    now = [datetime(2026, 10, 31, 23, 59, tzinfo=UTC)]
    book = ledger(tmp_path, clock=lambda: now[0])
    book.reserve('a', 'ep1', 'p', '0.90')
    book.mark_unknown('a')
    now[0] = datetime(2026, 11, 1, tzinfo=UTC)
    with pytest.raises(BudgetDenied, match='month'):
        book.reserve('b', 'ep2', 'p', '0.70')


def test_rollover_exposure_reports_carried_liability_until_reconciliation(tmp_path):
    from datetime import datetime

    now = [datetime(2026, 10, 31, 23, 59, tzinfo=UTC)]
    book = ledger(tmp_path, clock=lambda: now[0])
    book.reserve('a', 'ep1', 'p', '0.90')
    book.mark_unknown('a')
    now[0] = datetime(2026, 11, 1, tzinfo=UTC)

    before = book.exposure()
    assert before['month_usd'] == {'2026-10': '0.900000'}
    assert before['carried_liability_usd'] == '0.900000'
    assert before['current_month_effective_usd'] == '0.900000'

    book.settle('a', '0.20')

    after = book.exposure()
    assert after['month_usd'] == {'2026-10': '0.200000'}
    assert after['carried_liability_usd'] == '0.000000'
    assert after['current_month_effective_usd'] == '0.000000'
