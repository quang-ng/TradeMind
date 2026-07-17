from common.config import SchedulerSettings


def test_scheduler_default_symbols_exclude_sol() -> None:
    settings = SchedulerSettings()

    assert settings.symbols == ["BTC/USDT", "ETH/USDT", "BNB/USDT", "USDC/USDT"]
