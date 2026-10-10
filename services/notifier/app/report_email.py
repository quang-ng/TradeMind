"""Email rendering of the same private report, with an embedded (CID) chart."""

import re
from html import escape

from pydantic import BaseModel, ConfigDict

from .private_report import PrivateWeeklyReport, render_private_report
from .report_charts import render_equity_chart, render_report_charts

CHART_CID = "trademind-weekly-charts"
EQUITY_CID = "trademind-equity-chart"


class ReportEmail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    html: str
    chart_png: bytes
    equity_png: bytes


def _inline(text: str) -> str:
    safe = escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)


def render_report_email(
    report: PrivateWeeklyReport,
    *,
    cumulative_note: str = "",
    archive_repository: str = "",
) -> ReportEmail:
    # Render only our own generated Markdown. All text is escaped before styling;
    # no raw HTML, arbitrary links or external image requests are accepted.
    markdown = render_private_report(report)
    body: list[str] = []
    plain: list[str] = []
    in_table = False
    for line in markdown.splitlines():
        if line.startswith("<!--") or line.startswith("[Mục lục]"):
            continue
        if in_table and not line.startswith("|"):
            body.append("</tbody></table></div>")
            in_table = False
        if line.startswith("!["):
            cid = EQUITY_CID if "-equity.png)" in line else CHART_CID
            body.append(
                f'<img src="cid:{cid}" alt="Biểu đồ báo cáo tuần" '
                'width="760" style="display:block;width:100%;max-width:760px;height:auto;">'
            )
            plain.append("[Biểu đồ được nhúng trong bản HTML của email]")
            continue
        plain.append(line)
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue
            if not in_table:
                body.append(
                    '<div style="overflow-x:auto"><table cellpadding="8" '
                    'cellspacing="0" style="border-collapse:collapse;width:100%;'
                    'font-size:13px;border:1px solid #dbe2ea"><tbody>'
                )
                in_table = True
            body.append(
                "<tr>"
                + "".join(
                    f'<td style="border-bottom:1px solid #dbe2ea">{_inline(c)}</td>' for c in cells
                )
                + "</tr>"
            )
        elif line.startswith("#"):
            level = min(3, len(line) - len(line.lstrip("#")))
            body.append(
                f'<h{level} style="color:#0f172a;margin:24px 0 12px">'
                f"{_inline(line.lstrip('# '))}</h{level}>"
            )
        elif line.startswith("- "):
            body.append(f'<p style="margin:8px 0">• {_inline(line[2:])}</p>')
        elif line:
            body.append(f'<p style="line-height:1.6">{_inline(line)}</p>')
    if in_table:
        body.append("</tbody></table></div>")
    if cumulative_note:
        plain += ["", cumulative_note]
        body.append(f"<h2>Lũy kế từ khi bắt đầu</h2><p>{_inline(cumulative_note)}</p>")
    if archive_repository:
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", archive_repository
        ):
            raise ValueError("Invalid archive repository")
        url = f"https://github.com/{archive_repository}/blob/HEAD/{report.summary.page_name}.md"
        plain += ["", f"Xem bản lưu nội bộ (cần quyền GitHub): {url}"]
        body.append(
            f'<p><a href="{escape(url, quote=True)}">Xem bản lưu nội bộ trên GitHub</a>'
            " (cần quyền truy cập)</p>"
        )
    html = (
        '<!doctype html><html lang="vi"><meta charset="utf-8">'
        '<body style="margin:0;background:#f1f5f9;font-family:Arial,sans-serif;color:#334155">'
        '<div style="max-width:800px;margin:24px auto;padding:24px;background:white">'
        + "\n".join(body)
        + "</div></body></html>"
    )
    return ReportEmail(
        text="\n".join(plain),
        html=html,
        chart_png=render_report_charts(report.summary),
        equity_png=render_equity_chart(report),
    )
