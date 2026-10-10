from io import BytesIO

from PIL import Image

from notifier.app.report_charts import CHART_OWNER, render_report_charts
from notifier.tests.test_public_report import report, stats


def test_png_is_valid_deterministic_and_owned():
    content = render_report_charts(report())
    with Image.open(BytesIO(content)) as image:
        assert image.format == 'PNG'
        assert image.size == (1950, 1350)
        assert image.info['Software'] == CHART_OWNER
    assert content == render_report_charts(report())


def test_missing_and_zero_data_render_without_crashing():
    empty = stats(trades=0, recorded_pnl=0, wins=0, losses=0, return_samples=0,
                  mean_trade_return=None, total_pnl_usdt=None)
    value = report().model_copy(update={'current': empty, 'previous': empty})
    assert render_report_charts(value).startswith(b'\x89PNG')


def test_chart_rounding_matches_decimal_report():
    from decimal import Decimal

    from matplotlib.figure import Figure

    from notifier.app.report_charts import _comparison
    axis = Figure().subplots()
    _comparison(axis, [Decimal('-0.355'), None], 'test', '%')
    labels = [label.get_text() for label in axis.texts]
    assert '-0.36%' in labels
    assert 'Thiếu dữ liệu' in labels
