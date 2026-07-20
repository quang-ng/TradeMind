from collections import Counter
from decimal import Decimal

from .schemas import EquityPoint, ReplayDecision, ReplaySummary, ReplayTrade


def maximum_drawdown(equity_curve: list[EquityPoint]) -> Decimal:
    peak = Decimal("0")
    maximum = Decimal("0")
    for point in equity_curve:
        peak = max(peak, point.equity_usdt)
        if peak > 0:
            maximum = max(maximum, (peak - point.equity_usdt) / peak)
    return maximum


def build_summary(
    *,
    starting_equity: Decimal,
    ending_equity: Decimal,
    trades: list[ReplayTrade],
    decisions: list[ReplayDecision],
    equity_curve: list[EquityPoint],
    open_positions: int,
    pending_orders: int,
) -> ReplaySummary:
    net_pnl = ending_equity - starting_equity
    gross_pnl = sum((trade.gross_pnl_usdt for trade in trades), start=Decimal("0"))
    fees = sum(
        (trade.entry_fee_usdt + trade.exit_fee_usdt for trade in trades),
        start=Decimal("0"),
    )
    winning_total = sum(
        (trade.net_pnl_usdt for trade in trades if trade.net_pnl_usdt > 0),
        start=Decimal("0"),
    )
    losing_total = -sum(
        (trade.net_pnl_usdt for trade in trades if trade.net_pnl_usdt < 0),
        start=Decimal("0"),
    )
    wins = sum(trade.net_pnl_usdt > 0 for trade in trades)
    losses = sum(trade.net_pnl_usdt < 0 for trade in trades)
    count = len(trades)
    rejections = Counter(
        decision.rejection_reason.value
        for decision in decisions
        if decision.rejection_reason is not None
    )
    return ReplaySummary(
        starting_equity_usdt=starting_equity,
        ending_equity_usdt=ending_equity,
        net_pnl_usdt=net_pnl,
        net_return_pct=net_pnl / starting_equity,
        gross_pnl_usdt=gross_pnl,
        fees_usdt=fees,
        trades=count,
        wins=wins,
        losses=losses,
        win_rate=Decimal(wins) / Decimal(count) if count else Decimal("0"),
        profit_factor=(winning_total / losing_total if losing_total > 0 else None),
        expectancy_usdt=net_pnl / Decimal(count) if count else Decimal("0"),
        max_drawdown_pct=maximum_drawdown(equity_curve),
        open_positions=open_positions,
        pending_orders=pending_orders,
        rejection_counts=dict(rejections),
    )
