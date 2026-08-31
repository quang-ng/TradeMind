from decimal import Decimal

from common.config import RiskConfig
from common.enums import RejectionReason

from risk_engine.app.rules import expectancy_filter

from .factories import make_context, make_expectancy

ENABLED = RiskConfig(expectancy_filter_enabled=True)

# A cohort past the minimum sample with a proven-negative expectancy.
NEGATIVE = make_expectancy(sample_size=40, expectancy_r=Decimal("-0.30"))
# Past the minimum sample, but expectancy is positive.
POSITIVE = make_expectancy(sample_size=40, expectancy_r=Decimal("0.42"))
# Negative expectancy, but too few trades to act on it.
THIN = make_expectancy(sample_size=5, expectancy_r=Decimal("-0.30"))
# No R-tracked trades at all.
EMPTY = make_expectancy(sample_size=0, expectancy_r=None)


# --- check(): only ever rejects when enabled + proven-negative -------------


def test_disabled_by_default_passes_even_a_negative_cohort():
    ctx = make_context(config=RiskConfig(), expectancy=NEGATIVE)
    assert ctx.config.expectancy_filter_enabled is False
    assert expectancy_filter.check(ctx) is None


def test_enabled_rejects_a_sufficient_negative_cohort():
    ctx = make_context(config=ENABLED, expectancy=NEGATIVE)
    violation = expectancy_filter.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.NEGATIVE_EXPECTANCY_SETUP


def test_enabled_passes_a_positive_cohort():
    ctx = make_context(config=ENABLED, expectancy=POSITIVE)
    assert expectancy_filter.check(ctx) is None


def test_enabled_abstains_below_the_minimum_sample_size():
    ctx = make_context(config=ENABLED, expectancy=THIN)
    assert expectancy_filter.check(ctx) is None


def test_enabled_abstains_when_the_cohort_has_no_history():
    ctx = make_context(config=ENABLED, expectancy=EMPTY)
    assert expectancy_filter.check(ctx) is None


def test_expectancy_exactly_at_the_floor_is_not_rejected():
    """`expectancy_min_r` default is 0 and the check is strict `<` — a
    cohort sitting exactly at 0R is break-even, not proven-negative."""
    ctx = make_context(
        config=ENABLED,
        expectancy=make_expectancy(sample_size=40, expectancy_r=Decimal("0")),
    )
    assert expectancy_filter.check(ctx) is None


def test_custom_floor_and_sample_size_are_honoured():
    config = RiskConfig(
        expectancy_filter_enabled=True,
        expectancy_min_sample_size=10,
        expectancy_min_r=Decimal("0.1"),
    )
    # 15 trades clears the custom sample floor; +0.05R sits below the
    # custom +0.1R expectancy floor -> reject.
    ctx = make_context(
        config=config,
        expectancy=make_expectancy(sample_size=15, expectancy_r=Decimal("0.05")),
    )
    violation = expectancy_filter.check(ctx)
    assert violation is not None
    assert violation.reason == RejectionReason.NEGATIVE_EXPECTANCY_SETUP


# --- build_expectancy_check(): the shadow verdict, recorded always --------


def test_shadow_check_reports_negative_verdict_without_enforcing_it():
    ctx = make_context(config=RiskConfig(), expectancy=NEGATIVE)
    check = expectancy_filter.build_expectancy_check(ctx)
    assert check.decision == expectancy_filter.NEGATIVE_EXPECTANCY
    assert check.enforced is False
    assert check.setup_key == NEGATIVE.setup_key
    assert check.sample_size == 40
    assert check.historical_expectancy_r == Decimal("-0.30")


def test_shadow_check_marks_enforced_when_the_filter_is_on():
    ctx = make_context(config=ENABLED, expectancy=NEGATIVE)
    check = expectancy_filter.build_expectancy_check(ctx)
    assert check.decision == expectancy_filter.NEGATIVE_EXPECTANCY
    assert check.enforced is True


def test_shadow_check_reports_insufficient_data_for_a_thin_cohort():
    ctx = make_context(config=ENABLED, expectancy=THIN)
    check = expectancy_filter.build_expectancy_check(ctx)
    assert check.decision == expectancy_filter.INSUFFICIENT_DATA


def test_shadow_check_reports_allow_for_a_positive_cohort():
    ctx = make_context(config=ENABLED, expectancy=POSITIVE)
    check = expectancy_filter.build_expectancy_check(ctx)
    assert check.decision == expectancy_filter.ALLOW


def test_shadow_check_payload_is_json_safe():
    ctx = make_context(config=ENABLED, expectancy=NEGATIVE)
    payload = expectancy_filter.build_expectancy_check(ctx).as_payload()
    assert payload == {
        "setup_key": NEGATIVE.setup_key,
        "sample_size": 40,
        "historical_expectancy_r": "-0.30",
        "decision": "NEGATIVE_EXPECTANCY",
        "enforced": True,
    }


def test_shadow_check_payload_carries_null_expectancy_for_empty_cohort():
    ctx = make_context(config=ENABLED, expectancy=EMPTY)
    payload = expectancy_filter.build_expectancy_check(ctx).as_payload()
    assert payload["historical_expectancy_r"] is None
    assert payload["decision"] == "INSUFFICIENT_DATA"
