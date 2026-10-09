from datetime import timedelta
from decimal import Decimal

from common.db.models import Position

from risk_engine.app.account_state import _count_consecutive_losses

from .factories import NOW

# Production default (`RiskConfig.consecutive_loss_cluster_minutes`).
WINDOW = timedelta(minutes=60)
NO_CLUSTERING = timedelta(0)


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

    count, last_loss_closed_at = _count_consecutive_losses(
        positions, reset_at=None, cluster_window=NO_CLUSTERING
    )

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
        cluster_window=NO_CLUSTERING,
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
        cluster_window=NO_CLUSTERING,
    )

    assert count == 2
    assert last_loss_closed_at == NOW - timedelta(minutes=1)


def test_simultaneous_losses_within_window_count_as_one_event():
    # 2026-10-07 01:57 → 02:00: four correlated pairs stopped in 3 minutes.
    positions = [
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=1, pnl_usdt="-1"),
        _closed_position(minutes_ago=2, pnl_usdt="-1"),
        _closed_position(minutes_ago=3, pnl_usdt="-1"),
    ]

    count, last_loss_closed_at = _count_consecutive_losses(
        positions, reset_at=None, cluster_window=WINDOW
    )

    assert count == 1
    assert last_loss_closed_at == NOW


def test_losses_exactly_at_window_edge_share_a_cluster():
    positions = [
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=60, pnl_usdt="-1"),
    ]

    count, _ = _count_consecutive_losses(positions, reset_at=None, cluster_window=WINDOW)

    assert count == 1


def test_spread_out_losses_each_count_as_separate_events():
    positions = [
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=180, pnl_usdt="-1"),
        _closed_position(minutes_ago=360, pnl_usdt="-1"),
    ]

    count, last_loss_closed_at = _count_consecutive_losses(
        positions, reset_at=None, cluster_window=WINDOW
    )

    assert count == 3
    assert last_loss_closed_at == NOW


def test_mixed_clusters_and_isolated_losses():
    positions = [
        # cluster C: two losses 5 minutes apart
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=5, pnl_usdt="-1"),
        # isolated loss B
        _closed_position(minutes_ago=200, pnl_usdt="-1"),
        # cluster A: three losses within 30 minutes (2026-09-29 15:28 → 16:00)
        _closed_position(minutes_ago=400, pnl_usdt="-1"),
        _closed_position(minutes_ago=415, pnl_usdt="-1"),
        _closed_position(minutes_ago=430, pnl_usdt="-1"),
    ]

    count, _ = _count_consecutive_losses(positions, reset_at=None, cluster_window=WINDOW)

    assert count == 3


def test_cluster_is_anchored_on_first_loss_not_chained():
    # Losses every 40 minutes: each is within the window of its neighbour,
    # but a slow bleed must not collapse into one endless event. Oldest-first
    # clusters: {0, 40}, {80, 120}, {160}.
    positions = [
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=40, pnl_usdt="-1"),
        _closed_position(minutes_ago=80, pnl_usdt="-1"),
        _closed_position(minutes_ago=120, pnl_usdt="-1"),
        _closed_position(minutes_ago=160, pnl_usdt="-1"),
    ]

    count, _ = _count_consecutive_losses(positions, reset_at=None, cluster_window=WINDOW)

    assert count == 3


def test_win_between_losses_ends_the_streak_even_inside_a_window():
    positions = [
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=2, pnl_usdt="1"),
        _closed_position(minutes_ago=4, pnl_usdt="-1"),
        _closed_position(minutes_ago=200, pnl_usdt="-1"),
    ]

    count, last_loss_closed_at = _count_consecutive_losses(
        positions, reset_at=None, cluster_window=WINDOW
    )

    assert count == 1
    assert last_loss_closed_at == NOW


def test_reset_boundary_splits_a_cluster():
    # Operator acknowledged the first half of the dip; only the losses after
    # the reset form the new streak, still clustered together.
    positions = [
        _closed_position(minutes_ago=1, pnl_usdt="-1"),
        _closed_position(minutes_ago=2, pnl_usdt="-1"),
        _closed_position(minutes_ago=10, pnl_usdt="-1"),
        _closed_position(minutes_ago=11, pnl_usdt="-1"),
    ]

    count, last_loss_closed_at = _count_consecutive_losses(
        positions,
        reset_at=NOW - timedelta(minutes=5),
        cluster_window=WINDOW,
    )

    assert count == 1
    assert last_loss_closed_at == NOW - timedelta(minutes=1)


def test_zero_window_counts_every_losing_position():
    positions = [
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=1, pnl_usdt="-1"),
        _closed_position(minutes_ago=2, pnl_usdt="-1"),
    ]

    count, _ = _count_consecutive_losses(
        positions, reset_at=None, cluster_window=NO_CLUSTERING
    )

    assert count == 3


def test_breakeven_position_ends_the_streak():
    positions = [
        _closed_position(minutes_ago=0, pnl_usdt="-1"),
        _closed_position(minutes_ago=100, pnl_usdt="0"),
        _closed_position(minutes_ago=200, pnl_usdt="-1"),
    ]

    count, _ = _count_consecutive_losses(positions, reset_at=None, cluster_window=WINDOW)

    assert count == 1


def test_no_closed_positions():
    count, last_loss_closed_at = _count_consecutive_losses(
        [], reset_at=None, cluster_window=WINDOW
    )

    assert count == 0
    assert last_loss_closed_at is None
