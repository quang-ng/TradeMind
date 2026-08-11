"""Unit tests for Ledger.check_static_exit's freqtrade-mirroring logic
(ATR stop, trailing stop, minimal_roi decay table) — added 2026-08-11
alongside the matching ExternalSignalStrategy.py changes, to validate the
new numbers before trusting them live. Positions are constructed directly
(bypassing apply_entry/evaluate()) so each test isolates check_static_exit.
"""

import _bootstrap  # noqa: F401,I001 -- must patch sys.path before the imports below
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ledger import MINIMAL_ROI, Ledger, SimPosition  # noqa: E402

ENTRY_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ledger_with_position(*, entry_price, stop_loss_price, peak_price):
    ledger = Ledger(starting_equity_usdt=Decimal("1000"))
    entry_price = Decimal(str(entry_price))
    ledger.positions["BTC/USDT"] = SimPosition(
        symbol="BTC/USDT",
        entry_time=ENTRY_TIME,
        entry_price=entry_price,
        size_usdt=Decimal("100"),
        size_base=Decimal("100") / entry_price,
        stop_loss_price=Decimal(str(stop_loss_price)),
        peak_price=Decimal(str(peak_price)),
    )
    return ledger


def _candle(o, h, low, c):
    return {"o": o, "h": h, "l": low, "c": c}


def test_minimal_roi_matches_updated_decay_table():
    """Guards against the table silently drifting from
    ExternalSignalStrategy.py's minimal_roi again — they must be edited
    together since the strategy module can't be imported here directly."""
    assert MINIMAL_ROI == {
        0: Decimal("0.06"),
        240: Decimal("0.03"),
        720: Decimal("0.02"),
        1440: Decimal("0.015"),
        2880: Decimal("0.01"),
        5760: Decimal("0.005"),
    }


def test_atr_stop_fires_before_trailing_activates():
    ledger = _ledger_with_position(entry_price=100.0, stop_loss_price=97.0, peak_price=100.0)
    # Profit stays under trailing_activation_pct (2%) before the dip through
    # the ATR stop — must exit at the ATR stop_loss_price, not the old
    # blunt -8% strategy-wide floor.
    candle = _candle(o=100.5, h=100.5, low=96.5, c=100.2)

    trade = ledger.check_static_exit("BTC/USDT", candle, ENTRY_TIME + timedelta(hours=1))

    assert trade is not None
    assert trade.exit_reason == "atr_stoploss"
    assert trade.exit_price == Decimal("97.0")


def test_trailing_stop_locks_in_more_than_the_atr_floor():
    ledger = _ledger_with_position(entry_price=100.0, stop_loss_price=97.0, peak_price=100.0)

    # Candle 1: runs up to a peak of +2.5% — past trailing_activation_pct
    # (2%) but under the 0-4h ROI tier's 6% floor, so ROI doesn't preempt
    # this. Low sits at the open (no intra-candle dip), which stays above
    # this same candle's own trailing level (102.5*0.985=100.9625), so it
    # doesn't self-trigger either — see the intra-candle ordering note in
    # check_static_exit's docstring.
    up_candle = _candle(o=101.8, h=102.5, low=101.8, c=102.0)
    assert ledger.check_static_exit("BTC/USDT", up_candle, ENTRY_TIME + timedelta(hours=1)) is None
    assert ledger.positions["BTC/USDT"].peak_price == Decimal("102.5")

    # Candle 2: pulls back through the trail (still anchored to the 102.5
    # peak, not this candle's lower high of 101.2) while staying well above
    # the original ATR stop of 97 — the whole point of trailing.
    pullback_candle = _candle(o=101.0, h=101.2, low=98.0, c=99.0)
    trade = ledger.check_static_exit(
        "BTC/USDT", pullback_candle, ENTRY_TIME + timedelta(hours=2)
    )

    assert trade is not None
    assert trade.exit_reason == "trailing_stop"
    assert trade.exit_price == Decimal("100.9625")  # touched, not gapped through
    assert trade.exit_price > Decimal("97.0")


def test_minimal_roi_uses_the_720_minute_tiers_new_2pct_floor():
    ledger = _ledger_with_position(entry_price=100.0, stop_loss_price=90.0, peak_price=100.0)
    # 800 minutes elapsed -> the 720' tier, floor raised from 1.5% to 2% in
    # the 2026-08-11 update. High just touches the floor without dipping
    # low enough to trip the (now-active) trailing stop first.
    candle = _candle(o=101.5, h=102.0, low=101.5, c=101.8)

    trade = ledger.check_static_exit(
        "BTC/USDT", candle, ENTRY_TIME + timedelta(minutes=800)
    )

    assert trade is not None
    assert trade.exit_reason == "minimal_roi"
    assert trade.exit_price == Decimal("102.0")
