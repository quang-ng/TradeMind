from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from common.enums import Action
from risk_engine.app.evaluator import evaluate
from risk_engine.app.exit_evaluator import evaluate_exit
from risk_engine.app.schemas import AccountState, SignalView

from .metrics import build_summary
from .schemas import (
    Candle,
    EquityPoint,
    ReplayConfig,
    ReplayDecision,
    ReplayResult,
    ReplayTrade,
    SyntheticSignal,
)


@dataclass
class _PendingOrder:
    symbol: str
    side: str
    signal_time: datetime
    stake_usdt: Decimal | None = None


@dataclass
class _OpenPosition:
    symbol: str
    opened_at: datetime
    entry_reference_price: Decimal
    entry_price: Decimal
    amount: Decimal
    stake_usdt: Decimal
    entry_fee_usdt: Decimal
    stop_price: Decimal


class ReplaySimulator:
    """Chronological, offline replay of deterministic TradeMind controls."""

    def __init__(self, config: ReplayConfig | None = None) -> None:
        self.config = config or ReplayConfig()
        self.cash = self.config.starting_equity_usdt
        self.positions: dict[str, _OpenPosition] = {}
        self.pending: dict[str, _PendingOrder] = {}
        self.trades: list[ReplayTrade] = []
        self.decisions: list[ReplayDecision] = []
        self.equity_curve: list[EquityPoint] = []
        self.last_prices: dict[str, Decimal] = {}
        self.killswitch_enabled = False

    def run(
        self, candles: list[Candle], signals: list[SyntheticSignal]
    ) -> ReplayResult:
        ordered_candles = self._validate_and_order_candles(candles)
        signals_by_key = self._index_signals(signals)

        for candle in ordered_candles:
            self._execute_pending(candle)
            self._apply_safety_exits(candle)
            self.last_prices[candle.symbol] = candle.close
            signal = signals_by_key.get((candle.symbol, candle.close_time))
            if signal is not None:
                self._process_signal(candle, signal)
            self._record_equity(candle.close_time)

        ending_equity = self._equity()
        summary = build_summary(
            starting_equity=self.config.starting_equity_usdt,
            ending_equity=ending_equity,
            trades=self.trades,
            decisions=self.decisions,
            equity_curve=self.equity_curve,
            open_positions=len(self.positions),
            pending_orders=len(self.pending),
        )
        return ReplayResult(
            config=self.config,
            summary=summary,
            trades=self.trades,
            decisions=self.decisions,
            equity_curve=self.equity_curve,
        )

    @staticmethod
    def _validate_and_order_candles(candles: list[Candle]) -> list[Candle]:
        if not candles:
            raise ValueError("at least one candle is required")
        ordered = sorted(candles, key=lambda candle: (candle.open_time, candle.symbol))
        seen: set[tuple[str, datetime]] = set()
        last_close: dict[str, datetime] = {}
        for candle in ordered:
            key = (candle.symbol, candle.open_time)
            if key in seen:
                raise ValueError(
                    f"duplicate candle: {candle.symbol} {candle.open_time.isoformat()}"
                )
            seen.add(key)
            previous = last_close.get(candle.symbol)
            if previous is not None and candle.open_time < previous:
                raise ValueError(f"overlapping candles for {candle.symbol}")
            last_close[candle.symbol] = candle.close_time
        return ordered

    @staticmethod
    def _index_signals(
        signals: list[SyntheticSignal],
    ) -> dict[tuple[str, datetime], SyntheticSignal]:
        indexed: dict[tuple[str, datetime], SyntheticSignal] = {}
        for signal in signals:
            key = (signal.symbol, signal.candle_close_time)
            if key in indexed:
                raise ValueError(
                    f"duplicate signal: {signal.symbol} {signal.candle_close_time.isoformat()}"
                )
            indexed[key] = signal
        return indexed

    def _execute_pending(self, candle: Candle) -> None:
        order = self.pending.get(candle.symbol)
        if order is None or candle.open_time < order.signal_time:
            return
        del self.pending[candle.symbol]
        if order.side == "BUY":
            assert order.stake_usdt is not None
            entry_price = candle.open * (Decimal("1") + self.config.slippage_rate)
            entry_fee = order.stake_usdt * self.config.fee_rate
            total_cost = order.stake_usdt + entry_fee
            if total_cost > self.cash or candle.symbol in self.positions:
                return
            amount = order.stake_usdt / entry_price
            self.cash -= total_cost
            self.positions[candle.symbol] = _OpenPosition(
                symbol=candle.symbol,
                opened_at=candle.open_time,
                entry_reference_price=candle.open,
                entry_price=entry_price,
                amount=amount,
                stake_usdt=order.stake_usdt,
                entry_fee_usdt=entry_fee,
                stop_price=entry_price
                * (Decimal("1") - self.config.static_stop_loss_pct),
            )
            return
        if order.side == "SELL" and candle.symbol in self.positions:
            reference = candle.open
            fill = reference * (Decimal("1") - self.config.slippage_rate)
            self._close_position(candle.symbol, candle.open_time, reference, fill, "signal_exit")

    def _apply_safety_exits(self, candle: Candle) -> None:
        position = self.positions.get(candle.symbol)
        if position is None:
            return

        # Conservative intrabar ordering: if the same candle spans both a
        # stop and an ROI target, assume the loss happened first.
        if candle.low <= position.stop_price:
            fill = position.stop_price * (Decimal("1") - self.config.slippage_rate)
            self._close_position(
                candle.symbol,
                candle.close_time,
                position.stop_price,
                fill,
                "stop_loss",
            )
            return

        age_minutes = Decimal(str((candle.close_time - position.opened_at).total_seconds() / 60))
        target = self._roi_target(age_minutes)
        open_fill = candle.open * (Decimal("1") - self.config.slippage_rate)
        if self._net_return_at_price(position, open_fill) >= target:
            self._close_position(
                candle.symbol, candle.open_time, candle.open, open_fill, "roi"
            )
            return

        target_fill = self._price_for_net_return(position, target)
        trigger = target_fill / (Decimal("1") - self.config.slippage_rate)
        if candle.high >= trigger:
            self._close_position(
                candle.symbol, candle.close_time, trigger, target_fill, "roi"
            )

    @staticmethod
    def _roi_target(age_minutes: Decimal) -> Decimal:
        if age_minutes < 60:
            return Decimal("0.10")
        if age_minutes < 120:
            return Decimal("0.05")
        if age_minutes < 240:
            return Decimal("0.02")
        return Decimal("0")

    def _net_return_at_price(
        self, position: _OpenPosition, exit_price: Decimal
    ) -> Decimal:
        proceeds = position.amount * exit_price * (Decimal("1") - self.config.fee_rate)
        cost = position.stake_usdt + position.entry_fee_usdt
        return (proceeds - cost) / cost

    def _price_for_net_return(
        self, position: _OpenPosition, target: Decimal
    ) -> Decimal:
        cost = position.stake_usdt + position.entry_fee_usdt
        required_proceeds = cost * (Decimal("1") + target)
        return required_proceeds / (
            position.amount * (Decimal("1") - self.config.fee_rate)
        )

    def _close_position(
        self,
        symbol: str,
        closed_at: datetime,
        reference_price: Decimal,
        exit_price: Decimal,
        reason: str,
    ) -> None:
        position = self.positions.pop(symbol)
        gross_exit = position.amount * exit_price
        exit_fee = gross_exit * self.config.fee_rate
        proceeds = gross_exit - exit_fee
        self.cash += proceeds
        gross_pnl = position.amount * (exit_price - position.entry_price)
        net_pnl = proceeds - position.stake_usdt - position.entry_fee_usdt
        total_cost = position.stake_usdt + position.entry_fee_usdt
        self.trades.append(
            ReplayTrade(
                symbol=symbol,
                opened_at=position.opened_at,
                closed_at=closed_at,
                entry_reference_price=position.entry_reference_price,
                entry_price=position.entry_price,
                exit_reference_price=reference_price,
                exit_price=exit_price,
                amount=position.amount,
                stake_usdt=position.stake_usdt,
                entry_fee_usdt=position.entry_fee_usdt,
                exit_fee_usdt=exit_fee,
                gross_pnl_usdt=gross_pnl,
                net_pnl_usdt=net_pnl,
                net_pnl_pct=net_pnl / total_cost,
                exit_reason=reason,
            )
        )

    def _process_signal(self, candle: Candle, signal: SyntheticSignal) -> None:
        if signal.symbol != candle.symbol or signal.candle_close_time != candle.close_time:
            raise ValueError("signal does not match its candle")
        signal_view = SignalView(
            id=f"synthetic:{signal.symbol}:{signal.candle_close_time.isoformat()}",
            symbol=signal.symbol,
            action=signal.action,
            confidence=signal.confidence,
            candle_ts=signal.candle_close_time,
            price=candle.close,
            atr_14=signal.atr_14,
        )
        account = self._account_state(candle.close_time)
        duplicate = signal.symbol in self.pending
        if signal.action == Action.SELL:
            result = evaluate_exit(
                signal=signal_view,
                account=account,
                config=self.config.risk,
                now=candle.close_time,
                is_duplicate_decision=duplicate,
            )
            side = "SELL" if result.approved else None
            if result.approved:
                self.pending[signal.symbol] = _PendingOrder(
                    symbol=signal.symbol,
                    side="SELL",
                    signal_time=signal.candle_close_time,
                )
        else:
            result = evaluate(
                signal=signal_view,
                account=account,
                config=self.config.risk,
                now=candle.close_time,
                killswitch_enabled=self.killswitch_enabled,
                is_duplicate_decision=duplicate,
            )
            side = "BUY" if result.approved else None
            if result.auto_trip_killswitch:
                self.killswitch_enabled = True
            if result.approved:
                assert result.position_size_usdt is not None
                self.pending[signal.symbol] = _PendingOrder(
                    symbol=signal.symbol,
                    side="BUY",
                    signal_time=signal.candle_close_time,
                    stake_usdt=result.position_size_usdt,
                )
        self.decisions.append(
            ReplayDecision(
                symbol=signal.symbol,
                candle_close_time=signal.candle_close_time,
                action=signal.action,
                confidence=signal.confidence,
                approved=result.approved,
                rejection_reason=result.rejection_reason,
                pending_side=side,
            )
        )

    def _account_state(self, now: datetime) -> AccountState:
        equity = self._equity()
        pending_entries = {
            symbol: order
            for symbol, order in self.pending.items()
            if order.side == "BUY" and order.stake_usdt is not None
        }
        reserved = sum(
            (
                order.stake_usdt * (Decimal("1") + self.config.fee_rate)
                for order in pending_entries.values()
                if order.stake_usdt is not None
            ),
            start=Decimal("0"),
        )
        open_symbols = frozenset(self.positions) | frozenset(pending_entries)
        exposure = sum(
            (position.stake_usdt for position in self.positions.values()),
            start=Decimal("0"),
        ) + sum(
            (
                order.stake_usdt
                for order in pending_entries.values()
                if order.stake_usdt is not None
            ),
            start=Decimal("0"),
        )
        closed_desc = sorted(self.trades, key=lambda trade: trade.closed_at, reverse=True)
        consecutive_losses = 0
        last_loss_closed_at = None
        for trade in closed_desc:
            if trade.net_pnl_usdt >= 0:
                break
            consecutive_losses += 1
            last_loss_closed_at = last_loss_closed_at or trade.closed_at
        symbol_last_closed: dict[str, datetime] = {}
        for trade in closed_desc:
            symbol_last_closed.setdefault(trade.symbol, trade.closed_at)
        daily_pnl = sum(
            (
                trade.net_pnl_usdt
                for trade in self.trades
                if trade.closed_at.date() == now.date()
            ),
            start=Decimal("0"),
        )
        return AccountState(
            equity_usdt=equity,
            free_balance_usdt=max(self.cash - reserved, Decimal("0")),
            open_position_symbols=open_symbols,
            total_exposure_usdt=exposure,
            daily_pnl_pct=daily_pnl / equity if equity > 0 else Decimal("0"),
            consecutive_losses=consecutive_losses,
            last_loss_closed_at=last_loss_closed_at,
            symbol_last_closed_at=symbol_last_closed,
        )

    def _equity(self) -> Decimal:
        liquidation_value = sum(
            (
                position.amount
                * self.last_prices.get(symbol, position.entry_price)
                * (Decimal("1") - self.config.fee_rate)
                for symbol, position in self.positions.items()
            ),
            start=Decimal("0"),
        )
        return self.cash + liquidation_value

    def _record_equity(self, timestamp: datetime) -> None:
        self.equity_curve.append(EquityPoint(timestamp=timestamp, equity_usdt=self._equity()))
