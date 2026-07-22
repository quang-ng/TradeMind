from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from common.config import RiskConfig
from risk_engine.app.evaluator import evaluate
from risk_engine.app.exit_evaluator import evaluate_exit
from risk_engine.app.schemas import AccountState, SignalView

# Mirrors freqtrade/user_data/strategies/ExternalSignalStrategy.py: the
# static safety net that fires independent of any LLM signal. Kept here
# instead of imported since the strategy module isn't importable outside a
# Freqtrade runtime.
STATIC_STOPLOSS_PCT = Decimal("-0.08")
MINIMAL_ROI = {
    0: Decimal("0.06"),
    240: Decimal("0.025"),
    720: Decimal("0.015"),
    1440: Decimal("0.01"),
}


@dataclass
class SimPosition:
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    size_usdt: Decimal
    size_base: Decimal
    stop_loss_price: Decimal  # risk_engine's ATR stop — audit only, doesn't trigger the exit


@dataclass
class ClosedTrade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    size_usdt: Decimal
    pnl_usdt: Decimal
    pnl_pct: Decimal
    exit_reason: str


@dataclass
class Ledger:
    """In-memory mirror of `risk_engine.app.account_state.load_account_state`,
    fed by simulated fills instead of Postgres, so `evaluate()`/
    `evaluate_exit()` run unmodified against it.

    `equity_usdt` mirrors production's current behavior — account_state.py
    pins it at a starting placeholder because there is no live balance
    source yet — and stays fixed unless `compounding=True`."""

    starting_equity_usdt: Decimal
    fee_pct: Decimal = Decimal("0.001")
    slippage_pct: Decimal = Decimal("0.0")
    compounding: bool = False

    positions: dict[str, SimPosition] = field(default_factory=dict)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    consecutive_losses: int = 0
    last_loss_closed_at: datetime | None = None
    symbol_last_closed_at: dict[str, datetime] = field(default_factory=dict)
    killswitch_tripped: bool = False
    realized_pnl_usdt: Decimal = Decimal("0")

    @property
    def equity_usdt(self) -> Decimal:
        if self.compounding:
            return self.starting_equity_usdt + self.realized_pnl_usdt
        return self.starting_equity_usdt

    @property
    def total_exposure_usdt(self) -> Decimal:
        return sum((p.size_usdt for p in self.positions.values()), start=Decimal("0"))

    @property
    def free_balance_usdt(self) -> Decimal:
        return max(self.equity_usdt - self.total_exposure_usdt, Decimal("0"))

    def daily_pnl_pct(self, now: datetime) -> Decimal:
        today = now.date()
        daily = sum(
            (t.pnl_usdt for t in self.closed_trades if t.exit_time.date() == today),
            start=Decimal("0"),
        )
        equity = self.equity_usdt
        return (daily / equity) if equity > 0 else Decimal("0")

    def account_state(self, now: datetime) -> AccountState:
        return AccountState(
            equity_usdt=self.equity_usdt,
            free_balance_usdt=self.free_balance_usdt,
            open_position_symbols=frozenset(self.positions),
            total_exposure_usdt=self.total_exposure_usdt,
            daily_pnl_pct=self.daily_pnl_pct(now),
            consecutive_losses=self.consecutive_losses,
            last_loss_closed_at=self.last_loss_closed_at,
            symbol_last_closed_at=dict(self.symbol_last_closed_at),
        )

    def _record_close(
        self, position: SimPosition, exit_time: datetime, exit_price: Decimal, reason: str
    ) -> ClosedTrade:
        proceeds = position.size_base * exit_price * (1 - self.fee_pct - self.slippage_pct)
        pnl_usdt = proceeds - position.size_usdt
        pnl_pct = pnl_usdt / position.size_usdt if position.size_usdt > 0 else Decimal("0")

        trade = ClosedTrade(
            symbol=position.symbol,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=exit_price,
            size_usdt=position.size_usdt,
            pnl_usdt=pnl_usdt,
            pnl_pct=pnl_pct,
            exit_reason=reason,
        )
        self.closed_trades.append(trade)
        self.realized_pnl_usdt += pnl_usdt
        self.symbol_last_closed_at[position.symbol] = exit_time
        if pnl_usdt < 0:
            self.consecutive_losses += 1
            self.last_loss_closed_at = exit_time
        else:
            self.consecutive_losses = 0
        del self.positions[position.symbol]
        return trade

    def check_static_exit(
        self, symbol: str, candle: dict, candle_close_time: datetime
    ) -> ClosedTrade | None:
        """Freqtrade's own exit mechanism (static stoploss + `minimal_roi`
        decay table) — fires independent of any LLM signal, so it must be
        checked every candle a position is open, not just on SELL actions."""
        position = self.positions.get(symbol)
        if position is None:
            return None

        low = Decimal(str(candle["l"]))
        high = Decimal(str(candle["h"]))
        open_ = Decimal(str(candle["o"]))

        stop_price = position.entry_price * (1 + STATIC_STOPLOSS_PCT)
        if low <= stop_price:
            fill = min(open_, stop_price) if open_ < stop_price else stop_price
            return self._record_close(position, candle_close_time, fill, "static_stoploss")

        elapsed_minutes = (candle_close_time - position.entry_time).total_seconds() / 60
        roi_threshold = next(
            (
                MINIMAL_ROI[mark]
                for mark in sorted(MINIMAL_ROI, reverse=True)
                if elapsed_minutes >= mark
            ),
            None,
        )
        if roi_threshold is not None:
            roi_price = position.entry_price * (1 + roi_threshold)
            if high >= roi_price:
                fill = max(open_, roi_price) if open_ > roi_price else roi_price
                return self._record_close(position, candle_close_time, fill, "minimal_roi")
        return None

    def apply_entry(
        self,
        symbol: str,
        signal_view: SignalView,
        config: RiskConfig,
        now: datetime,
        fill_price: Decimal,
    ):
        result = evaluate(
            signal=signal_view,
            account=self.account_state(now),
            config=config,
            now=now,
            killswitch_enabled=self.killswitch_tripped,
            is_duplicate_decision=False,
        )
        if result.auto_trip_killswitch:
            self.killswitch_tripped = True
        if not result.approved:
            return result, None

        effective_price = fill_price * (1 + self.fee_pct + self.slippage_pct)
        size_base = (
            result.position_size_usdt / effective_price if effective_price > 0 else Decimal("0")
        )
        position = SimPosition(
            symbol=symbol,
            entry_time=now,
            entry_price=fill_price,
            size_usdt=result.position_size_usdt,
            size_base=size_base,
            stop_loss_price=result.stop_loss_price,
        )
        self.positions[symbol] = position
        return result, position

    def apply_exit_signal(
        self,
        symbol: str,
        signal_view: SignalView,
        config: RiskConfig,
        now: datetime,
        fill_price: Decimal,
    ):
        result = evaluate_exit(
            signal=signal_view,
            account=self.account_state(now),
            config=config,
            now=now,
            is_duplicate_decision=False,
        )
        if not result.approved:
            return result, None
        position = self.positions[symbol]
        trade = self._record_close(position, now, fill_price, "llm_sell_signal")
        return result, trade
