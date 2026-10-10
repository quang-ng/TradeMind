"""PNG renderers: public aggregate overview and private-only equity history."""

from decimal import Decimal
from io import BytesIO
from typing import TYPE_CHECKING

from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from .public_report import PublicWeeklyReport

if TYPE_CHECKING:
    from .private_report import PrivateWeeklyReport

CHART_OWNER = "TradeMind public reports"
_GREEN = "#15803d"
_RED = "#dc2626"
_BLUE = "#2563eb"
_GRAY = "#64748b"


def _comparison(axis: Axes, values: list[Decimal | None], title: str, unit: str) -> None:
    axis.set_title(title, loc="left", fontweight="bold", pad=16)
    axis.axhline(0, color="#94a3b8", linewidth=0.8)
    known = [float(value) for value in values if value is not None]
    span = max([abs(value) for value in known] + [0.01])
    axis.set_ylim(min([0.0, *known]) - span * 0.35, max([0.0, *known]) + span * 0.35)
    for index, value in enumerate(values):
        if value is None:
            axis.text(index, 0, "Thiếu dữ liệu", ha="center", va="bottom", color=_GRAY)
            continue
        axis.bar(index, float(value), width=0.48, color=_GREEN if value >= 0 else _RED, zorder=3)
        axis.annotate(
            f"{value:+.2f}{unit}",
            (index, float(value)),
            xytext=(0, 8 if value >= 0 else -8),
            textcoords="offset points",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontweight="bold",
            color=_GREEN if value >= 0 else _RED,
        )
    axis.set_xticks([0, 1], ["Kỳ trước", "Kỳ này"])
    axis.set_xlim(-0.6, 1.6)
    axis.set_ylabel(unit.strip() or "%")


def render_report_charts(report: PublicWeeklyReport) -> bytes:
    # Decimal math stays in the report/query layer; floats are only plot coordinates.
    figure = Figure(figsize=(13, 9), dpi=150, facecolor="white", layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    figure.suptitle(
        f"TRADEMIND  /  TỔNG QUAN TUẦN\nKỳ kết thúc {report.window_end:%d/%m/%Y %H:%M} UTC",
        fontsize=18,
        fontweight="bold",
        color="#0f172a",
    )
    cohorts = [report.previous, report.current]
    _comparison(
        axes[0, 0],
        [None if s.total_pnl_usdt is None else s.total_pnl_usdt for s in cohorts],
        "Lãi/lỗ đã chốt (USDT)",
        " USDT",
    )
    _comparison(
        axes[0, 1],
        [None if s.mean_trade_return is None else s.mean_trade_return * 100 for s in cohorts],
        "Lãi/lỗ trung bình mỗi giao dịch (%)",
        "%",
    )
    axes[0, 1].set_xlabel("Không phải lợi nhuận % của tài khoản", fontsize=9, color=_GRAY)

    outcome = axes[1, 0]
    outcome.set_title("Kết quả giao dịch đã đóng", loc="left", fontweight="bold", pad=16)
    parts = [
        ("Thắng", [s.wins for s in cohorts], _GREEN),
        ("Thua", [s.losses for s in cohorts], _RED),
        ("Hòa", [s.recorded_pnl - s.wins - s.losses for s in cohorts], _BLUE),
        ("Thiếu P&L", [s.trades - s.recorded_pnl for s in cohorts], "#cbd5e1"),
    ]
    bottom = [0, 0]
    for label, counts, color in parts:
        bars = outcome.bar([0, 1], counts, bottom=bottom, width=0.48, color=color, label=label)
        outcome.bar_label(
            bars,
            labels=[str(n) if n else "" for n in counts],
            label_type="center",
            color="#0f172a" if label == "Thiếu P&L" else "white",
        )
        bottom = [a + b for a, b in zip(bottom, counts, strict=True)]
    outcome.set_xticks([0, 1], ["Kỳ trước", "Kỳ này"])
    outcome.set_ylabel("Số giao dịch")
    outcome.set_ylim(0, max(1, *bottom) * 1.3)
    outcome.yaxis.set_major_locator(MaxNLocator(integer=True))
    outcome.legend(loc="upper left", ncols=2, fontsize=9, frameon=False)

    op = report.operations
    events = axes[1, 1]
    events.set_title("Sự kiện vận hành trong kỳ", loc="left", fontweight="bold", pad=16)
    counts = [
        op.order_failures,
        op.reconciliation_alerts,
        op.llm_stall_alerts,
        op.llm_recoveries,
        op.killswitch_activations,
        op.config_changes,
    ]
    labels = [
        "Lệnh thất bại",
        "Cần đối soát",
        "Chuỗi lỗi LLM",
        "LLM phục hồi",
        "Bật kill switch",
        "Đổi cấu hình",
    ]
    bars = events.barh(labels, counts, color=_BLUE, height=0.55)
    events.bar_label(bars, padding=5)
    events.invert_yaxis()
    events.set_xlim(0, max(1, *counts) * 1.3)
    events.xaxis.set_major_locator(MaxNLocator(integer=True))
    events.set_xlabel(f"Số sự kiện • {op.signals} tín hiệu đã lưu", fontsize=9, color=_GRAY)
    for axis in axes.flat:
        axis.spines[["top", "right"]].set_visible(False)
        axis.spines[["left", "bottom"]].set_color("#e2e8f0")
        axis.tick_params(colors="#475569")
        axis.grid(axis="x" if axis is events else "y", color="#f1f5f9", zorder=0)
        axis.set_axisbelow(True)
    output = BytesIO()
    figure.savefig(output, format="png", metadata={"Software": CHART_OWNER})
    return output.getvalue()


def render_equity_chart(report: "PrivateWeeklyReport") -> bytes:
    """Private-only balances; never called by the public Wiki publisher."""
    figure = Figure(figsize=(13, 4.5), dpi=150, facecolor="white", layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    points = report.equity_history
    values = [float(p.equity_usdt) if p.equity_usdt is not None else float("nan") for p in points]
    axis.set_title("Tổng tài sản qua 12 kỳ báo cáo", loc="left", fontweight="bold", fontsize=16)
    axis.set_ylabel("USDT")
    axis.set_xlabel("Snapshot cuối cùng đã ghi nhận trong mỗi kỳ • kỳ thiếu dữ liệu để trống")
    if any(p.equity_usdt is not None for p in points):
        axis.plot(range(len(points)), values, marker="o", color=_BLUE, linewidth=2)
        for i, point in enumerate(points):
            if point.equity_usdt is not None:
                axis.annotate(
                    f"{point.equity_usdt:.2f}",
                    (i, float(point.equity_usdt)),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=9,
                )
        axis.margins(y=0.25)
    else:
        axis.text(
            0.5,
            0.5,
            "Chưa có snapshot tài sản cho các kỳ này",
            transform=axis.transAxes,
            ha="center",
            color=_GRAY,
            fontsize=13,
        )
    axis.set_xticks(
        range(len(points)),
        [f"{p.period_end:%d/%m/%Y}" for p in points],
        rotation=35,
        ha="right",
        fontsize=9,
    )
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", color="#e2e8f0")
    output = BytesIO()
    figure.savefig(output, format="png", metadata={"Software": CHART_OWNER})
    return output.getvalue()
