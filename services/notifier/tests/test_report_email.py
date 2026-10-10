from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from notifier.app.email_client import EmailClient
from notifier.app.private_report import EquityPoint, equity_periods
from notifier.app.report_email import render_report_email
from notifier.tests.test_private_report import private_report
from notifier.tests.test_public_report import END


def test_equity_periods_keep_last_observation_and_missing_gaps():
    samples = (
        EquityPoint(
            period_end=END, observed_at=END - timedelta(days=1), equity_usdt=Decimal("102")
        ),
        EquityPoint(
            period_end=END, observed_at=END - timedelta(days=2), equity_usdt=Decimal("101")
        ),
        EquityPoint(
            period_end=END, observed_at=END + timedelta(seconds=1), equity_usdt=Decimal("999")
        ),
        EquityPoint(
            period_end=END, observed_at=END - timedelta(days=14), equity_usdt=Decimal("98")
        ),
    )
    result = equity_periods(samples, END, 3)
    assert [p.equity_usdt for p in result] == [Decimal("98"), None, Decimal("102")]
    assert result[-1].observed_at == END - timedelta(days=1)


def test_email_uses_private_content_cid_images_and_escaped_text():
    content = render_report_email(
        private_report(),
        cumulative_note="<script>secret</script>",
        archive_repository="dqflow/TradeMind-Reports",
    )
    assert "Việc cần kiểm tra" in content.html
    assert "Tổng tài sản theo kỳ" in content.html
    assert "cid:trademind-weekly-charts" in content.html
    assert "cid:trademind-equity-chart" in content.html
    assert "<script>" not in content.html and "&lt;script&gt;" in content.html
    assert 'src="https://' not in content.html
    assert "https://github.com/dqflow/TradeMind-Reports/blob/HEAD/" in content.html
    assert "](" not in content.text
    assert content.chart_png.startswith(b"\x89PNG") and content.equity_png.startswith(b"\x89PNG")


def test_mime_has_plain_html_and_two_inline_png_parts(monkeypatch):
    smtp = MagicMock()
    monkeypatch.setattr("notifier.app.email_client.smtplib.SMTP", smtp)
    EmailClient()._send_sync(
        "Subject", "Plain", '<img src="cid:trademind-equity-chart">', b"png1", b"png2"
    )
    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Plain"
    assert "cid:trademind-equity-chart" in message.get_body(preferencelist=("html",)).get_content()
    images = [p for p in message.walk() if p.get_content_type() == "image/png"]
    assert [p["Content-ID"] for p in images] == [
        "<trademind-weekly-charts>",
        "<trademind-equity-chart>",
    ]
    assert all(p.get_content_disposition() == "inline" for p in images)
