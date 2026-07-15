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
        config=config,
    )

    # risk_amount = 10000*0.01=100; stop_distance = 500/60000*2.0 ~= 0.016667
    # (inside [0.015, 0.08], no clamping); raw_size = 100/0.016667 ~= 6000,
    # clamped to equity*max_position_pct = 10000*0.05 = 500.
    assert result.position_size_usdt == Decimal("500")
    assert result.stop_loss_price == Decimal("60000") * (Decimal("1") - result.stop_distance_pct)


def test_sizing_never_goes_negative_with_zero_free_balance():
    config = RiskConfig()
    result = compute_sizing(
        equity_usdt=Decimal("10000"),
        free_balance_usdt=Decimal("0"),
        entry_price=Decimal("60000"),
        atr_14=Decimal("500"),
        config=config,
    )
    assert result.position_size_usdt == Decimal("0")


@given(
    equity_usdt=st.decimals(min_value=Decimal("100"), max_value=Decimal("1000000"), places=2),
    free_balance_usdt=st.decimals(min_value=Decimal("0"), max_value=Decimal("1000000"), places=2),
    entry_price=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2),
    atr_14=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("100000"), places=4),
)
def test_position_size_never_exceeds_max_position_pct_or_free_balance(
    equity_usdt, free_balance_usdt, entry_price, atr_14
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
        config=config,
    )

    assert result.position_size_usdt <= equity_usdt * config.max_position_pct
    assert result.position_size_usdt <= free_balance_usdt
    assert result.position_size_usdt >= Decimal("0")
