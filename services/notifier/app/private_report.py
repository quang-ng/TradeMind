"""Private report: typed closed-trade journal and explicitly current account snapshot."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from common.config import NotifierSettings
from common.db.models import Order, PerformanceSnapshot, Position
from common.enums import PositionStatus
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .public_report import PublicWeeklyReport, build_public_report, render_public_report


class PrivateTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    symbol: str = Field(pattern=r"^[A-Z0-9]+/[A-Z0-9]+$")
    closed_at: AwareDatetime
    pnl_usdt: Decimal | None
    pnl_pct: Decimal | None
    entry_price: Decimal
    exit_price: Decimal | None
    dry_run: bool


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    equity_usdt: Decimal = Field(ge=0)
    open_positions: int = Field(ge=0)
    killswitch_enabled: bool
    dry_run: bool


class EquityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    period_end: AwareDatetime
    observed_at: AwareDatetime | None
    equity_usdt: Decimal | None = Field(ge=0)


def equity_periods(
    samples: tuple[EquityPoint, ...],
    end: datetime,
    count: int = 12,
) -> tuple[EquityPoint, ...]:
    points = []
    for offset in reversed(range(count)):
        boundary = end - timedelta(days=7 * offset)
        eligible = [
            p
            for p in samples
            if p.observed_at is not None
            and boundary - timedelta(days=7) < p.observed_at <= boundary
            and p.equity_usdt is not None
        ]
        latest = max(eligible, key=lambda p: p.observed_at) if eligible else None
        points.append(
            EquityPoint(
                period_end=boundary,
                observed_at=latest.observed_at if latest else None,
                equity_usdt=latest.equity_usdt if latest else None,
            )
        )
    return tuple(points)


class PrivateWeeklyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: PublicWeeklyReport
    trades: tuple[PrivateTrade, ...]
    observed_at: AwareDatetime
    account: AccountSnapshot | None
    equity_history: tuple[EquityPoint, ...] = ()


async def build_private_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: NotifierSettings,
    window_end: datetime,
) -> PrivateWeeklyReport:
    summary = await build_public_report(session_factory, window_end)
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(
                        Position.symbol,
                        Position.closed_at,
                        Position.pnl_usdt,
                        Position.pnl_pct,
                        Position.entry_price,
                        Position.exit_price,
                        Order.dry_run,
                    )
                    .join(Order, Order.id == Position.entry_order_id)
                    .where(
                        Position.status == PositionStatus.CLOSED.value,
                        Position.closed_at >= window_end - timedelta(days=7),
                        Position.closed_at < window_end,
                    )
                    .order_by(Position.closed_at, Position.id)
                )
            )
            .mappings()
            .all()
        )
        trades = tuple(PrivateTrade.model_validate(row) for row in rows)
        snapshots = (
            await session.execute(
                select(
                    PerformanceSnapshot.computed_at,
                    PerformanceSnapshot.starting_equity_usdt,
                )
                .where(
                    PerformanceSnapshot.computed_at > window_end - timedelta(days=84),
                    PerformanceSnapshot.computed_at <= window_end,
                )
                .order_by(PerformanceSnapshot.computed_at)
            )
        ).all()
        samples = tuple(
            EquityPoint(
                period_end=at,
                observed_at=at,
                equity_usdt=equity,
            )
            for at, equity in snapshots
        )
        history = equity_periods(samples, window_end)

    account = await fetch_account_snapshot(settings)
    return PrivateWeeklyReport(
        summary=summary,
        trades=trades,
        observed_at=datetime.now(timezone.utc),
        account=account,
        equity_history=history,
    )


async def fetch_account_snapshot(settings: NotifierSettings) -> AccountSnapshot | None:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{settings.admin_api_url.rstrip('/')}/status",
                headers={"Authorization": f"Bearer {settings.admin_api_key}"},
            )
            response.raise_for_status()
            return AccountSnapshot.model_validate(response.json())
    except (httpx.HTTPError, ValueError):
        return None


def _money(value: Decimal | None) -> str:
    return "Thiếu dữ liệu" if value is None else f"{value:+.4f}"


def render_private_report(report: PrivateWeeklyReport) -> str:
    summary = render_public_report(report.summary)
    summary = summary.replace(
        "<!-- trademind-public-report-v1 -->", "<!-- trademind-private-report-v1 -->"
    )
    summary = summary.replace("# TradeMind — Báo cáo vận hành tuần", "# TradeMind — Báo cáo nội bộ")
    summary = summary[: summary.index("Bản công khai chỉ công bố")]
    summary = summary.replace(
        "không tính lãi/lỗ chưa chốt. Số dư và chế độ vận hành không được công bố.",
        "không tính lãi/lỗ chưa chốt. Snapshot tài khoản bên dưới được lấy khi tạo báo cáo.",
    )
    current = [
        "## Tình trạng tại thời điểm kiểm tra",
        "",
        f"Kiểm tra lúc **{report.observed_at:%Y-%m-%d %H:%M UTC}**; "
        "không phải số dư/trạng thái tại cuối kỳ báo cáo.",
        "",
    ]
    if report.account is None:
        current.append(
            "**CHƯA XÁC MINH:** không lấy được trạng thái Admin API; cần kiểm tra nội bộ."
        )
    else:
        a = report.account
        state = "ĐANG CHẶN LỆNH MỞ MỚI" if a.killswitch_enabled else "KILL SWITCH ĐANG TẮT"
        current += [
            f"**{state}**. Admin API trả được trạng thái tài khoản.",
            "",
            f"- Tổng tài sản hiện tại: **{a.equity_usdt:.4f} USDT**.",
            f"- Vị thế đang mở: **{a.open_positions}**.",
            f"- Chế độ hiện tại: **{'dry-run' if a.dry_run else 'live'}**.",
            "",
            "Đây không phải kiểm tra đầy đủ mọi service; không suy ra uptime từ snapshot.",
        ]
    pnl = [t.pnl_usdt for t in report.trades if t.pnl_usdt is not None]
    wins = [v for v in pnl if v > 0]
    losses = [v for v in pnl if v < 0]
    gain, loss = sum(wins, Decimal(0)), -sum(losses, Decimal(0))
    analysis = [
        "## Vì sao lãi hoặc lỗ?",
        "",
        f"- Tổng các lệnh thắng: **{gain:.4f} USDT**; "
        f"tổng mức lỗ: **{loss:.4f} USDT** (chỉ giao dịch có P&L).",
    ]
    if wins:
        analysis.append(f"- Lãi trung bình lệnh thắng: **{gain / len(wins):.4f} USDT**.")
    if losses:
        analysis.append(f"- Lỗ trung bình lệnh thua: **{loss / len(losses):.4f} USDT**.")
    if loss:
        analysis.append(f"- Profit factor (tổng lãi / tổng lỗ): **{gain / loss:.2f}**.")
    if gain < loss:
        analysis.append("- Tổng tiền mất ở các lệnh thua lớn hơn tổng tiền kiếm ở các lệnh thắng.")
    analysis += [
        "",
        "Đây là phân rã kết quả, chưa chứng minh nguyên nhân chiến lược hay lỗi hệ thống.",
    ]
    # Insert the current snapshot before charts, after the historical assessment.
    summary = summary.replace(
        "## Biểu đồ tổng quan", "\n".join(current + ["", *analysis, "", "## Biểu đồ tổng quan"])
    )
    equity_section = [
        "## Tổng tài sản theo kỳ",
        "",
        f"![Tổng tài sản qua 12 kỳ báo cáo]({report.summary.page_name}-equity.png)",
        "",
        "Mỗi điểm là snapshot tài sản được ghi nhận cuối cùng trong kỳ; không phải",
        "số dư chính xác tại ranh giới kỳ. Kỳ thiếu dữ liệu để trống, không nội suy từ P&L.",
        "",
        "| Kỳ kết thúc (UTC) | Snapshot thực tế (UTC) | Tổng tài sản USDT |",
        "|---|---|---:|",
    ]
    for point in report.equity_history:
        at = point.observed_at.strftime("%Y-%m-%d %H:%M") if point.observed_at else "Chưa có"
        equity = f"{point.equity_usdt:.4f}" if point.equity_usdt is not None else "Thiếu dữ liệu"
        equity_section.append(f"| {point.period_end:%Y-%m-%d} | {at} | {equity} |")
    summary += "\n" + "\n".join(equity_section) + "\n"
    lines = [
        summary,
        "## Nhật ký giao dịch đã đóng",
        "",
        "| Đóng lúc (UTC) | Cặp | Giá vào | Giá ra | P&L USDT | P&L % | Chế độ |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for t in report.trades:
        pct = "Thiếu" if t.pnl_pct is None else f"{t.pnl_pct:+.2%}"
        lines.append(
            f"| {t.closed_at:%Y-%m-%d %H:%M} | {t.symbol} | {t.entry_price} | "
            f"{t.exit_price if t.exit_price is not None else 'Thiếu'} | "
            f"{_money(t.pnl_usdt)} | {pct} | {'dry-run' if t.dry_run else 'live'} |"
        )
    lines += [
        "",
        "Chỉ chia sẻ nội bộ. Không chứa token, mật khẩu, raw payload hoặc log thô.",
        "",
        "[Mục lục](README.md)",
        "",
    ]
    return "\n".join(lines)
