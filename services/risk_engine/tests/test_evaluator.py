from datetime import timedelta
from decimal import Decimal

from common.config import RiskConfig
from common.enums import Action, RejectionReason
from factories import NOW, make_account, make_signal
from risk_engine.app.evaluator import evaluate


def test_approves_when_all_rules_pass():
    result = evaluate(
        signal=make_signal(),
        account=make_account(),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
    )
    assert result.approved is True
    assert result.rejection_reason is None
    assert result.position_size_usdt is not None
    assert result.stop_loss_price is not None
    assert result.risk_pct_applied is not None


def test_kill_switch_short_circuits_before_any_other_rule():
    stale_hold_signal = make_signal(action=Action.HOLD, candle_ts=NOW - timedelta(minutes=999))
    result = evaluate(
        signal=stale_hold_signal,
        account=make_account(),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=True,
        is_duplicate_decision=False,
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.KILLSWITCH_ACTIVE


def test_hold_signal_short_circuits_before_staleness_check():
    stale_hold_signal = make_signal(action=Action.HOLD, candle_ts=NOW - timedelta(minutes=999))
    result = evaluate(
        signal=stale_hold_signal,
        account=make_account(),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
    )
    assert result.rejection_reason == RejectionReason.SIGNAL_WAS_HOLD


def test_daily_loss_breach_requests_auto_trip():
    result = evaluate(
        signal=make_signal(),
        account=make_account(daily_pnl_pct=Decimal("-0.05")),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
    )
    assert result.rejection_reason == RejectionReason.DAILY_LOSS_LIMIT_HIT
    assert result.auto_trip_killswitch is True


def test_rejected_decision_carries_no_sizing_fields():
    result = evaluate(
        signal=make_signal(confidence=Decimal("0.1")),
        account=make_account(),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.LOW_CONFIDENCE
    assert result.position_size_usdt is None
    assert result.stop_loss_price is None
