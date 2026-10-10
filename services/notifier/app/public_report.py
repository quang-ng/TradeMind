"""Public weekly aggregates. Never load account state, symbols or raw payloads."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from common.config import NotifierSettings
from common.db.models import AuditEvent, Position, Signal
from common.enums import AuditEventType, PositionStatus
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)
Count = Annotated[int, Field(ge=0)]


class PublicStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    trades: Count
    recorded_pnl: Count
    wins: Count
    losses: Count
    return_samples: Count
    mean_trade_return: Decimal | None
    total_pnl_usdt: Decimal | None


class OperationsStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signals: Count
    order_failures: Count
    reconciliation_alerts: Count
    llm_stall_alerts: Count
    llm_recoveries: Count
    killswitch_activations: Count
    config_changes: Count


class PublicWeeklyReport(BaseModel):
    # No arbitrary prose/dict fields: private values cannot hitch a ride in a payload.
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_end: AwareDatetime
    current: PublicStats
    previous: PublicStats
    operations: OperationsStats

    @field_validator("window_end")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @property
    def page_name(self) -> str:
        return f"Weekly-{self.window_end:%Y-%m-%d-%H%M%S}"


def latest_weekly_end(now: datetime, settings: NotifierSettings) -> datetime:
    if now.tzinfo is None:
        raise ValueError("UTC-aware time required")
    now = now.astimezone(timezone.utc)
    end = now.replace(hour=settings.weekly_pnl_report_hour_utc, minute=0, second=0, microsecond=0)
    end -= timedelta(days=(end.weekday() - settings.weekly_pnl_report_weekday_utc) % 7)
    if end > now:
        end -= timedelta(days=7)
    return end


async def _trade_stats(session: AsyncSession, start: datetime, end: datetime) -> PublicStats:
    row = (
        (
            await session.execute(
                select(
                    func.count(Position.id).label("trades"),
                    func.count(Position.pnl_usdt).label("recorded_pnl"),
                    func.count().filter(Position.pnl_usdt > 0).label("wins"),
                    func.count().filter(Position.pnl_usdt < 0).label("losses"),
                    func.count(Position.pnl_pct).label("return_samples"),
                    func.avg(Position.pnl_pct).label("mean_trade_return"),
                    func.sum(Position.pnl_usdt).label("total_pnl_usdt"),
                )
                .select_from(Position)
                .where(
                    Position.status == PositionStatus.CLOSED.value,
                    Position.closed_at >= start,
                    Position.closed_at < end,
                )
            )
        )
        .mappings()
        .one()
    )
    return PublicStats.model_validate(row)


async def build_public_report(
    session_factory: async_sessionmaker[AsyncSession],
    window_end: datetime,
) -> PublicWeeklyReport:
    if window_end.tzinfo is None or window_end > datetime.now(timezone.utc):
        raise ValueError("Report end must be timezone-aware and in the past")
    window_end = window_end.astimezone(timezone.utc)
    start = window_end - timedelta(days=7)
    async with session_factory() as session:
        current = await _trade_stats(session, start, window_end)
        previous = await _trade_stats(session, start - timedelta(days=7), start)
        signals = await session.scalar(
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.created_at >= start,
                Signal.created_at < window_end,
            )
        )
        # Aggregate enum allowlist only; never read event.payload or model text.
        events = (
            ("order_failures", AuditEventType.ORDER_FAILED),
            ("reconciliation_alerts", AuditEventType.RECONCILIATION_REQUIRED),
            ("llm_stall_alerts", AuditEventType.LLM_TIMEOUT_STREAK),
            ("llm_recoveries", AuditEventType.LLM_TIMEOUT_RECOVERED),
            ("killswitch_activations", AuditEventType.KILLSWITCH_ENABLED),
            ("config_changes", AuditEventType.CONFIG_CHANGED),
        )
        counts = (
            (
                await session.execute(
                    select(
                        *[
                            func.count().filter(AuditEvent.event_type == event.value).label(name)
                            for name, event in events
                        ]
                    ).where(AuditEvent.created_at >= start, AuditEvent.created_at < window_end)
                )
            )
            .mappings()
            .one()
        )
        operations = OperationsStats.model_validate({"signals": signals, **counts})
    return PublicWeeklyReport(
        window_end=window_end,
        current=current,
        previous=previous,
        operations=operations,
    )


def _percent(value: Decimal | None) -> str:
    return "Không đủ dữ liệu" if value is None else f"{value:.2%}"


def render_assessment(report: PublicWeeklyReport) -> list[str]:
    current, previous, op = report.current, report.previous, report.operations
    pnl = current.total_pnl_usdt
    result = (
        "CHƯA ĐỦ DỮ LIỆU"
        if pnl is None
        else (
            "ĐANG LỖ TRONG KỲ" if pnl < 0 else "CÓ LÃI TRONG KỲ" if pnl > 0 else "HÒA VỐN TRONG KỲ"
        )
    )
    lines = [f"## Đánh giá nhanh: {result}", ""]
    if pnl is not None:
        lines.append(f"- **Kết quả:** {pnl:+.4f} USDT từ {current.trades} giao dịch đã đóng.")
    else:
        lines.append("- **Kết quả:** chưa có P&L đã ghi nhận để đánh giá.")
    if pnl is not None and previous.total_pnl_usdt is not None:
        delta = pnl - previous.total_pnl_usdt
        direction = "tăng" if delta > 0 else "giảm" if delta < 0 else "không đổi"
        lines.append(
            f"- **So với kỳ trước:** P&L {direction} {abs(delta):.4f} USDT "
            f"(kỳ trước {previous.total_pnl_usdt:+.4f} USDT; {previous.trades} giao dịch)."
        )
    incidents = op.order_failures + op.reconciliation_alerts + op.llm_stall_alerts
    lines.append(
        f"- **Vận hành cần xem lại:** {op.order_failures} sự kiện lệnh lỗi, "
        f"{op.reconciliation_alerts} cảnh báo đối soát, {op.llm_stall_alerts} chuỗi lỗi LLM; "
        f"kill switch được bật {op.killswitch_activations} lần trong kỳ."
        if incidents or op.killswitch_activations
        else "- **Vận hành:** không ghi nhận các cảnh báo được theo dõi trong kỳ; "
        "điều này không đủ để kết luận hệ thống luôn khỏe."
    )
    lines += ["", "### Việc cần kiểm tra", ""]
    if op.reconciliation_alerts:
        lines.append(
            "- Kiểm tra các cảnh báo đối soát đã được xử lý và trạng thái lệnh đã khớp chưa."
        )
    if op.killswitch_activations:
        lines.append("- Xem nguyên nhân các lần bật kill switch; không tự tắt chỉ dựa vào báo cáo.")
    if op.order_failures or op.llm_stall_alerts:
        lines.append("- Đối chiếu các sự kiện lỗi với audit nội bộ và xác minh đã phục hồi.")
    if pnl is not None and pnl < 0:
        lines.append(
            "- Phân tích giao dịch thua và quy tắc thoát lệnh trước khi thay đổi chiến lược."
        )
    else:
        lines.append("- Tiếp tục theo dõi nhiều kỳ; một tuần không đủ chứng minh hiệu quả ổn định.")
    lines += ["", "Đây là đánh giá **kỳ đã kết thúc**, không phải trạng thái hệ thống hiện tại."]
    return lines


def render_public_report(report: PublicWeeklyReport) -> str:
    def win_rate(stats: PublicStats) -> str:
        return _percent(Decimal(stats.wins) / stats.recorded_pnl if stats.recorded_pnl else None)

    a, b = report.current, report.previous
    end = report.window_end
    start = end - timedelta(days=7)
    rows = [
        ("Giao dịch đã đóng", str(a.trades), str(b.trades)),
        (
            "Thắng / thua / hòa",
            f"{a.wins} / {a.losses} / {a.recorded_pnl - a.wins - a.losses}",
            f"{b.wins} / {b.losses} / {b.recorded_pnl - b.wins - b.losses}",
        ),
        ("Giao dịch thiếu P&L", str(a.trades - a.recorded_pnl), str(b.trades - b.recorded_pnl)),
        ("Tỷ lệ thắng (trên giao dịch có P&L)", win_rate(a), win_rate(b)),
        (
            "Lãi/lỗ % trung bình mỗi giao dịch",
            _percent(a.mean_trade_return),
            _percent(b.mean_trade_return),
        ),
        ("Số giao dịch có dữ liệu %", str(a.return_samples), str(b.return_samples)),
        (
            "Lãi/lỗ đã chốt (USDT)",
            "Không đủ dữ liệu" if a.total_pnl_usdt is None else f"{a.total_pnl_usdt:+.4f}",
            "Không đủ dữ liệu" if b.total_pnl_usdt is None else f"{b.total_pnl_usdt:+.4f}",
        ),
    ]
    op = report.operations
    operations = [
        ("Tín hiệu đã lưu", op.signals),
        ("Sự kiện lệnh thất bại", op.order_failures),
        ("Cảnh báo cần đối soát lệnh", op.reconciliation_alerts),
        ("Cảnh báo chuỗi lỗi/timeout LLM", op.llm_stall_alerts),
        ("Sự kiện LLM phục hồi", op.llm_recoveries),
        ("Số lần bật kill switch trong kỳ", op.killswitch_activations),
        ("Sự kiện thay đổi cấu hình", op.config_changes),
    ]
    return "\n".join(
        [
            "<!-- trademind-public-report-v1 -->",
            "# TradeMind — Báo cáo vận hành tuần",
            "",
            f"Kỳ báo cáo: **{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC**.",
            "Tính từ đầu kỳ, không bao gồm thời điểm cuối kỳ.",
            "",
            f"Kỳ trước: {start - timedelta(days=7):%Y-%m-%d %H:%M} → {start:%Y-%m-%d %H:%M} UTC.",
            "",
            *render_assessment(report),
            "",
            "## Biểu đồ tổng quan",
            "",
            f"![So sánh lãi/lỗ, lợi nhuận trung bình, kết quả giao dịch và sự kiện vận hành]"
            f"({report.page_name}-charts.png)",
            "",
            "Số liệu chi tiết và mẫu số của từng chỉ số nằm trong các bảng bên dưới.",
            "",
            "## Hiệu suất tổng hợp",
            "",
            "| Chỉ số | Kỳ này | Kỳ trước |",
            "|---|---:|---:|",
            *[f"| {label} | {current} | {previous} |" for label, current, previous in rows],
            "",
            "Lãi/lỗ % là trung bình cộng các `pnl_pct` đã ghi nhận, không trọng số theo vốn;",
            "**không phải lợi nhuận % của tài khoản**. "
            "Giao dịch thiếu dữ liệu không được tính là 0.",
            "Lãi/lỗ USDT là tổng P&L đã ghi nhận của các giao dịch đóng trong kỳ;",
            "không tính lãi/lỗ chưa chốt. Số dư và chế độ vận hành không được công bố.",
            "",
            "## Sự kiện vận hành trong kỳ",
            "",
            "| Chỉ số | Số lượng |",
            "|---|---:|",
            *[f"| {label} | {count} |" for label, count in operations],
            "",
            "Các số đếm chỉ phản ánh dữ liệu đã lưu, không chứng minh hệ thống chạy liên tục.",
            "Cảnh báo LLM đếm chuỗi sự cố, không phải từng timeout. Không suy ra trạng thái",
            "kill switch hiện tại từ số lần bật trong kỳ. "
            "Không có số liệu uptime hoặc lịch sử deploy.",
            "",
            "Bản công khai chỉ công bố P&L tổng hợp; không có số dư, chi tiết lệnh,",
            "vị thế đang mở,",
            "log thô hay thông tin máy chủ. PostgreSQL là nguồn lịch sử gốc.",
            "",
            "[Mục lục báo cáo](Operations-Reports)",
            "",
        ]
    )


async def publish_weekly_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: NotifierSettings,
    window_end: datetime,
) -> bool:
    from .wiki_client import WikiClient

    if not settings.wiki_report_repository:
        return False
    try:
        report = await build_public_report(session_factory, window_end)
        return await WikiClient(settings).publish(report)
    except Exception:
        # DB error details can contain connection strings or bound private values.
        logger.warning("public_weekly_report_failed", extra={"window_end": window_end.isoformat()})
        return False


async def public_report_loop(
    session_factory: async_sessionmaker[AsyncSession],
    settings: NotifierSettings,
) -> None:
    if not settings.wiki_report_repository:
        return
    completed: datetime | None = None
    while True:
        now = datetime.now(timezone.utc)
        end = latest_weekly_end(now, settings)
        if end != completed and await publish_weekly_report(session_factory, settings, end):
            completed = end
        # Retry failures hourly; wake exactly at the next weekly boundary when closer.
        delay = min(3600.0, max(1.0, (end + timedelta(days=7) - now).total_seconds()))
        await asyncio.sleep(delay)
