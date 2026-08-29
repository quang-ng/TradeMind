from decimal import Decimal

from common.config import RiskConfig
from hypothesis import given
from hypothesis import strategies as st

from risk_engine.app.sizing import compute_sizing


def test_compute_sizing_matches_hand_calculation():
    config = RiskConfig()
    result = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("10000"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=Decimal("1.0"),
        config=config,
    )

    # risk_amount = 10000*0.01=100; stop_distance = 500/60000*2.0 ~= 0.016667
    # (inside [0.015, 0.08], no clamping); raw_size = 100/0.016667 ~= 6000,
    # clamped to equity*max_position_pct = 10000*0.05 = 500. confidence=1.0
    # applies a 1x scale, so the pre-existing hand-calculation is unaffected.
    assert result.position_size_usdt == Decimal("500")
    assert result.stop_loss_price == Decimal("60000") * (Decimal("1") - result.stop_distance_pct)
    # nominal budget = equity * risk_per_trade_pct = 100; actual is smaller
    # here because max_position_pct clamped position_size_usdt down to 500.
    assert result.nominal_risk_amount_usdt == Decimal("100")
    assert result.actual_risk_usdt == result.position_size_usdt * result.stop_distance_pct
    assert result.actual_risk_usdt < result.nominal_risk_amount_usdt


def test_actual_risk_usdt_equals_nominal_when_unclamped():
    """D1: when no clamp binds, position_size_usdt is exactly
    raw_size_usdt = risk_amount_usdt / stop_distance_pct, so
    actual_risk_usdt (position_size_usdt * stop_distance_pct) collapses
    back to the nominal risk_amount_usdt budget.

    Under the *default* config, max_position_pct (0.05) always binds before
    raw_size_usdt does, because raw_size only stays under equity*0.05 when
    stop_distance_pct >= risk_per_trade_pct/max_position_pct = 0.2 — above
    max_stop_loss_pct (0.08), i.e. unreachable. `max_position_pct` is
    raised here specifically to exercise the genuinely-unclamped path.
    """
    config = RiskConfig(max_position_pct=Decimal("1.0"))
    result = compute_sizing(
        equity_usdt=Decimal("1000000"),
        free_balance_usdt=Decimal("1000000"),  # free-balance clamp doesn't bind either
        entry_price=Decimal("50000"),  # atr/price*multiplier = 0.02 exactly, no repeating decimal
        atr_14=Decimal("500"),
        confidence=Decimal("1.0"),
        config=config,
    )
    assert result.actual_risk_usdt == result.nominal_risk_amount_usdt


def test_actual_risk_usdt_below_nominal_when_clamped_by_max_position_pct():
    config = RiskConfig()
    result = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("10000"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=Decimal("1.0"),
        config=config,
    )
    assert result.position_size_usdt == Decimal("10000") * config.max_position_pct
    assert result.actual_risk_usdt < result.nominal_risk_amount_usdt


def test_actual_risk_usdt_below_nominal_when_clamped_by_free_balance():
    config = RiskConfig()
    result = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("50"),  # far below both risk budget and max_position_pct
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=Decimal("1.0"),
        config=config,
    )
    assert result.position_size_usdt == Decimal("50")
    assert result.actual_risk_usdt == Decimal("50") * result.stop_distance_pct
    assert result.actual_risk_usdt < result.nominal_risk_amount_usdt


def test_actual_risk_usdt_below_nominal_when_scaled_by_confidence():
    """Confidence scaling (2026-08-04) shrinks position_size_usdt after all
    other clamps, so actual_risk_usdt must reflect it too — this is exactly
    the gap D1 exists to close (a pre-clamp figure would overstate what was
    truly risked on a low-confidence entry). `max_position_pct` is raised
    (see test_actual_risk_usdt_equals_nominal_when_unclamped) so confidence
    scaling is isolated as the only clamp in effect."""
    config = RiskConfig(max_position_pct=Decimal("1.0"))
    result = compute_sizing(
        equity_usdt=Decimal("1000000"),
        free_balance_usdt=Decimal("1000000"),
        entry_price=Decimal("50000"),  # atr/price*multiplier = 0.02 exactly, no repeating decimal
        atr_14=Decimal("500"),
        confidence=config.min_confidence,  # floor scale, 0.5x
        config=config,
    )
    assert (
        result.actual_risk_usdt
        == result.nominal_risk_amount_usdt * config.min_confidence_size_scale
    )


def test_sizing_never_goes_negative_with_zero_free_balance():
    config = RiskConfig()
    result = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("0"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=Decimal("1.0"),
        config=config,
    )
    assert result.position_size_usdt == Decimal("0")


def test_confidence_at_min_confidence_applies_the_floor_scale():
    """A signal that barely clears `min_confidence` (0.70) should be sized at
    `min_confidence_size_scale` (0.5) of what full conviction would get."""
    config = RiskConfig()
    full = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("10000"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=Decimal("1.0"),
        config=config,
    )
    floor = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("10000"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=config.min_confidence,
        config=config,
    )
    assert floor.position_size_usdt == full.position_size_usdt * config.min_confidence_size_scale


def test_confidence_below_min_confidence_is_clamped_to_the_floor_scale():
    """Sizing itself doesn't reject low-confidence signals (that's
    `rules/min_confidence.py`'s job) — it just must never scale below the
    configured floor, even if called with a confidence under the bar."""
    config = RiskConfig()
    below = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("10000"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=Decimal("0.1"),
        config=config,
    )
    floor = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("10000"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        confidence=config.min_confidence,
        config=config,
    )
    assert below.position_size_usdt == floor.position_size_usdt


@given(
    equity_usdt=st.decimals(min_value=Decimal("100"), max_value=Decimal("1000000"), places=2),
    free_balance_usdt=st.decimals(min_value=Decimal("0"), max_value=Decimal("1000000"), places=2),
    entry_price=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2),
    atr_14=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("100000"), places=4),
    confidence=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2),
)
def test_position_size_never_exceeds_max_position_pct_or_free_balance(
    equity_usdt, free_balance_usdt, entry_price, atr_14, confidence
):
    """PROJECT.md Section 12 Phase 2 exit criteria: a property-based test
    proves position size never exceeds max_position_pct or free_balance
    under randomized inputs."""
    config = RiskConfig()
    result = compute_sizing(
        equity_usdt=equity_usdt,
        free_balance_usdt=free_balance_usdt,
        entry_price=entry_price,
        atr_14=atr_14,
        confidence=confidence,
        config=config,
    )

    assert result.position_size_usdt <= equity_usdt * config.max_position_pct
    assert result.position_size_usdt <= free_balance_usdt


@given(
    equity_usdt=st.decimals(min_value=Decimal("100"), max_value=Decimal("1000000"), places=2),
    free_balance_usdt=st.decimals(min_value=Decimal("0"), max_value=Decimal("1000000"), places=2),
    entry_price=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2),
    atr_14=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("100000"), places=4),
    confidence=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2),
)
def test_actual_risk_usdt_never_negative_and_never_exceeds_nominal(
    equity_usdt, free_balance_usdt, entry_price, atr_14, confidence
):
    """D1's invariant, property-tested per PROJECT.md Section 12 Phase 2's
    existing precedent for sizing.py: `actual_risk_usdt` (the R-multiple
    denominator) is never negative and never exceeds the pre-clamp
    `nominal_risk_amount_usdt` budget, under randomized inputs — clamps and
    confidence-scaling can only ever shrink what's truly at risk, never
    inflate it above the nominal target."""
    config = RiskConfig()
    result = compute_sizing(
        equity_usdt=equity_usdt,
        free_balance_usdt=free_balance_usdt,
        entry_price=entry_price,
        atr_14=atr_14,
        confidence=confidence,
        config=config,
    )

    assert result.actual_risk_usdt >= Decimal("0")
    assert result.actual_risk_usdt <= result.nominal_risk_amount_usdt
    assert result.actual_risk_usdt == result.position_size_usdt * result.stop_distance_pct
    assert result.position_size_usdt >= Decimal("0")
