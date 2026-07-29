from datetime import timedelta
from decimal import Decimal

from common.db.models import Position

from risk_engine.app.account_state import _count_consecutive_losses

from .factories import NOW


def _closed_position(*, minutes_ago: int, pnl_usdt: str) -> Position:
    return Position(
        closed_at=NOW - timedelta(minutes=minutes_ago),
        pnl_usdt=Decimal(pnl_usdt),
    )


def test_counts_uninterrupted_losses_without_operator_reset():
    positions = [
        _closed_position(minutes_ago=1, pnl_usdt="-1"),
        _closed_position(minutes_ago=2, pnl_usdt="-2"),
        _closed_position(minutes_ago=3, pnl_usdt="1"),
        _closed_position(minutes_ago=4, pnl_usdt="-4"),
    ]

    count, last_loss_closed_at = _count_consecutive_losses(positions, reset_at=None)

    assert count == 2
    assert last_loss_closed_at == NOW - timedelta(minutes=1)


def test_operator_reset_excludes_acknowledged_loss_streak():
    positions = [
        _closed_position(minutes_ago=10, pnl_usdt="-1"),
        _closed_position(minutes_ago=20, pnl_usdt="-2"),
        _closed_position(minutes_ago=30, pnl_usdt="-3"),
    ]

    count, last_loss_closed_at = _count_consecutive_losses(
        positions,
        reset_at=NOW - timedelta(minutes=5),
    )

    assert count == 0
    assert last_loss_closed_at is None


def test_losses_after_operator_reset_start_a_new_streak():
    positions = [
        _closed_position(minutes_ago=1, pnl_usdt="-1"),
        _closed_position(minutes_ago=2, pnl_usdt="-2"),
        _closed_position(minutes_ago=10, pnl_usdt="-3"),
    ]

    count, last_loss_closed_at = _count_consecutive_losses(
        positions,
        reset_at=NOW - timedelta(minutes=5),
    )

    assert count == 2
    assert last_loss_closed_at == NOW - timedelta(minutes=1)
