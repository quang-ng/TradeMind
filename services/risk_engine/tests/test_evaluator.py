from datetime import timedelta
from decimal import Decimal

from common.config import RiskConfig
from common.enums import Action, RejectionReason

from risk_engine.app.evaluator import evaluate
from risk_engine.app.rules.expectancy_filter import (
    INSUFFICIENT_DATA,
    NEGATIVE_EXPECTANCY,
)

from .factories import NOW, make_account, make_expectancy, make_signal

# A cohort with a statistically sufficient, proven-negative historical
# expectancy — the only shape the M5 filter ever acts on.
NEGATIVE_COHORT = make_expectancy(sample_size=40, expectancy_r=Decimal("-0.25"))


def test_approves_when_all_rules_pass():
    result = evaluate(
        signal=make_signal(),
        account=make_account(),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=make_expectancy(),
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
        expectancy=make_expectancy(),
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
        expectancy=make_expectancy(),
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
        expectancy=make_expectancy(),
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
        expectancy=make_expectancy(),
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.LOW_CONFIDENCE
    assert result.position_size_usdt is None
    assert result.stop_loss_price is None


# --- M5: Historical Expectancy Filter wiring --------------------------------


def test_expectancy_check_recorded_on_every_evaluated_entry():
    """D4: the shadow verdict lands on both approved and rejected results,
    so it can be audited from day one."""
    approved = evaluate(
        signal=make_signal(),
        account=make_account(),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=make_expectancy(sample_size=40, expectancy_r=Decimal("0.5")),
    )
    rejected = evaluate(
        signal=make_signal(confidence=Decimal("0.1")),
        account=make_account(),
        config=RiskConfig(),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=make_expectancy(sample_size=40, expectancy_r=Decimal("0.5")),
    )
    assert approved.expectancy_check is not None
    assert approved.expectancy_check.decision == "ALLOW"
    assert rejected.expectancy_check is not None
    assert rejected.expectancy_check.decision == "ALLOW"


def test_disabled_filter_never_rejects_a_negative_expectancy_setup():
    """Shadow mode (the default): a proven-negative cohort still approves;
    the verdict is only recorded, never enforced."""
    result = evaluate(
        signal=make_signal(),
        account=make_account(),
        config=RiskConfig(),  # expectancy_filter_enabled=False
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=NEGATIVE_COHORT,
    )
    assert result.approved is True
    assert result.expectancy_check.decision == NEGATIVE_EXPECTANCY
    assert result.expectancy_check.enforced is False


def test_enabled_filter_rejects_a_negative_expectancy_setup():
    result = evaluate(
        signal=make_signal(),
        account=make_account(),
        config=RiskConfig(expectancy_filter_enabled=True),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=NEGATIVE_COHORT,
    )
    assert result.approved is False
    assert result.rejection_reason == RejectionReason.NEGATIVE_EXPECTANCY_SETUP
    assert result.expectancy_check.decision == NEGATIVE_EXPECTANCY
    assert result.expectancy_check.enforced is True


def test_enabled_filter_abstains_on_insufficient_history():
    result = evaluate(
        signal=make_signal(),
        account=make_account(),
        config=RiskConfig(expectancy_filter_enabled=True),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=make_expectancy(sample_size=5, expectancy_r=Decimal("-0.9")),
    )
    assert result.approved is True
    assert result.expectancy_check.decision == INSUFFICIENT_DATA


def test_min_confidence_still_short_circuits_ahead_of_the_expectancy_filter():
    """Rule ordering (Section 9.1): confidence (rule 5) is reported before
    the expectancy filter (rule 6) even when both would reject."""
    result = evaluate(
        signal=make_signal(confidence=Decimal("0.1")),
        account=make_account(),
        config=RiskConfig(expectancy_filter_enabled=True),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=NEGATIVE_COHORT,
    )
    assert result.rejection_reason == RejectionReason.LOW_CONFIDENCE


def test_expectancy_filter_short_circuits_ahead_of_portfolio_gates():
    """Rule ordering (Section 9.1): the expectancy filter (rule 6, an
    entry-quality gate) is reported before max-open-positions (rule 7, a
    portfolio gate) when both would reject."""
    result = evaluate(
        signal=make_signal(symbol="BTC/USDT"),
        account=make_account(open_position_symbols=frozenset({"BTC/USDT"})),
        config=RiskConfig(expectancy_filter_enabled=True),
        now=NOW,
        killswitch_enabled=False,
        is_duplicate_decision=False,
        expectancy=NEGATIVE_COHORT,
    )
    assert result.rejection_reason == RejectionReason.NEGATIVE_EXPECTANCY_SETUP
