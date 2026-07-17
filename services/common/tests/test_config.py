from common.config import SchedulerSettings


def test_scheduler_default_symbols_exclude_sol() -> None:
    settings = SchedulerSettings()

    assert settings.symbols == ["BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT"]
    assert settings.llm_ohlcv_window == 4
