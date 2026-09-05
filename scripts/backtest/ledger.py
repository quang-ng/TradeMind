from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from common.config import RiskConfig
from risk_engine.app.evaluator import evaluate
from risk_engine.app.exit_evaluator import evaluate_exit
from risk_engine.app.schemas import AccountState, ExpectancyView, SignalView

# The offline ledger does not (yet) feed the M5 historical-expectancy filter
# a per-setup cohort, so it always sees an empty view and abstains — its
# default (disabled) behaviour. Expectancy *reporting* over a replay lives
# in mechanical_replay.py / expectancy_report.py (positive-expectancy M4).
_NO_EXPECTANCY = ExpectancyView(setup_key="(backtest)", sample_size=0, expectancy_r=None)

# Mirrors freqtrade/user_data/strategies/ExternalSignalStrategy.py: the
# safety nets that fire independent of any LLM signal. Kept here instead of
# imported since the strategy module isn't importable outside a Freqtrade
# runtime — MUST be kept in sync by hand with that file.
#
# 2026-08-11: MINIMAL_ROI updated to match the strategy's post-incident
# table (see ExternalSignalStrategy.py's minimal_roi comment) — the old
# 24h+ floor of 1% was found to be firing as the primary exit rather than
# a rare backstop. TRAILING_ACTIVATION_PCT/TRAILING_DISTANCE_PCT are new,
# mirroring custom_stoploss()'s trailing step added the same day — see
# that file's comment for why they're 2%/1.5%, not the first-guess
# 1%/0.75% (a grid search run through this same module found the *margin*
# between the two, not either number alone, drives how often the trail
# gets gapped through for a net loss).
#
# 2026-08-31: MINIMAL_ROI x3 + trailing to 4.5%/2.7%, mirroring the same-day
# "let winners run" change in ExternalSignalStrategy.py (see its comments for
# the take-profit sweep that picked x3). The ATR stop / hard_loss_cut were
# deliberately left untouched. Keep this block byte-for-byte in step with
# that file.
#
# 2026-09-05: "0" tier walked back 18% -> 10% per issue #19's first
# live-trade check (see ExternalSignalStrategy.py's minimal_roi comment for
# the data). Every other tier and the trailing pair are unchanged.
STATIC_STOPLOSS_PCT = Decimal("-0.08")
MINIMAL_ROI = {
    0: Decimal("0.10"),
    240: Decimal("0.09"),
    720: Decimal("0.06"),
    1440: Decimal("0.045"),
    2880: Decimal("0.03"),
    5760: Decimal("0.015"),
}
TRAILING_ACTIVATION_PCT = Decimal("0.045")
TRAILING_DISTANCE_PCT = Decimal("0.027")


@dataclass
class SimPosition:
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    size_usdt: Decimal
    size_base: Decimal
    stop_loss_price: Decimal  # risk_engine's ATR stop — same value custom_stoploss()
    # applies via the `slpct:` entry tag, so (unlike before 2026-08-11) this now
    # actually drives check_static_exit below rather than being audit-only.
    peak_price: Decimal  # trade's high-water mark since entry — mirrors
    # freqtrade's Trade.max_rate, used for the trailing-stop check below.
    # Positive-expectancy plan M4: `evaluate()`'s post-clamp 1R for this
    # trade (`risk_engine` `RiskResult.actual_risk_usdt`, D1), carried so
    # `_record_close` can compute `r_multiple`. `None` only if the entry
    # somehow bypassed sizing (test fixtures that build SimPosition directly).
    actual_risk_usdt: Decimal | None = None


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
    # Positive-expectancy plan M4: `pnl_usdt / actual_risk_usdt`, the same
    # R-multiple definition the live path persists on `Position.r_multiple`
    # (M1). `None` when `actual_risk_usdt` is missing or non-positive, so
    # `common.performance` excludes it from R metrics exactly as it does a
    # legacy live row.
    r_multiple: Decimal | None = None


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
    # When True, a tripped killswitch is still recorded (killswitch_tripped
    # stays True for reporting — see print_summary) but no longer blocks
    # entries. Real production requires a human `/killswitch_off` to resume
    # after CONSECUTIVE_LOSS_PAUSE/DAILY_LOSS_LIMIT_HIT (no auto-reset
    # exists), and this replay has no operator to simulate that — without
    # this flag, one bad stretch permanently freezes every remaining
    # decision candle for the rest of the run, which silently turns a
    # multi-month edge question into a test of however many hours preceded
    # the first trip. Use this to isolate the entry/exit rubric's own edge
    # from that operational circuit breaker; the real system still has the
    # breaker; this flag only removes it from the replay's arithmetic.
    ignore_killswitch: bool = False

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
        r_multiple = (
            pnl_usdt / position.actual_risk_usdt
            if position.actual_risk_usdt is not None and position.actual_risk_usdt > 0
            else None
        )

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
            r_multiple=r_multiple,
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
        """Freqtrade's own exit mechanism (per-trade ATR stop with a
        trailing step, plus the `minimal_roi` decay table) — fires
        independent of any LLM signal, so it must be checked every candle a
        position is open, not just on SELL actions.

        Mirrors `ExternalSignalStrategy.custom_stoploss()` + `minimal_roi`.
        `position.peak_price` is updated from this candle's high first —
        matching freqtrade updating `Trade.max_rate` before invoking
        `custom_stoploss()` for a completed candle — then the stop check
        uses that same-candle peak, and only after that does the ROI check
        run against this candle's high.

        The trailing activation gate uses profit-at-peak
        (`position.peak_price`), not this candle's close: it's rechecked
        every candle, and a pullback is exactly when close-based profit can
        dip back under the activation bar even though the trade did reach
        it — gating on the close would silently drop trailing protection
        at the moment it's most needed. Mirrors the same peak-vs-current
        distinction in custom_stoploss().
        """
        position = self.positions.get(symbol)
        if position is None:
            return None

        low = Decimal(str(candle["l"]))
        high = Decimal(str(candle["h"]))
        open_ = Decimal(str(candle["o"]))

        position.peak_price = max(position.peak_price, high)
        peak_profit_pct = (position.peak_price - position.entry_price) / position.entry_price

        # Defensive floor mirroring custom_stoploss()'s own fallback: never
        # let the effective stop be looser than the strategy-wide static
        # bound, even if stop_loss_price were ever wider than expected.
        stop_price = max(
            position.stop_loss_price, position.entry_price * (1 + STATIC_STOPLOSS_PCT)
        )
        exit_reason = "atr_stoploss"
        if peak_profit_pct >= TRAILING_ACTIVATION_PCT:
            trailing_price = position.peak_price * (1 - TRAILING_DISTANCE_PCT)
            if trailing_price > stop_price:
                stop_price = trailing_price
                exit_reason = "trailing_stop"

        if low <= stop_price:
            fill = min(open_, stop_price) if open_ < stop_price else stop_price
            return self._record_close(position, candle_close_time, fill, exit_reason)

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
            killswitch_enabled=self.killswitch_tripped and not self.ignore_killswitch,
            is_duplicate_decision=False,
            expectancy=_NO_EXPECTANCY,
        )
        if result.auto_trip_killswitch:
            self.killswitch_tripped = True
            if self.ignore_killswitch:
                # consecutive_losses.py's rejection is independent of the
                # killswitch_enabled flag above — it re-derives its own
                # violation straight from self.consecutive_losses every
                # candle, and that counter only resets on a winning close,
                # which can never happen once entries are blocked. Left
                # alone this self-perpetuates (permanent freeze) regardless
                # of the flag. Reset it here too, so ignoring the killswitch
                # actually means what it says instead of only defusing the
                # kill_switch.py rule while consecutive_losses keeps gating.
                self.consecutive_losses = 0
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
            peak_price=fill_price,
            actual_risk_usdt=result.actual_risk_usdt,
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
