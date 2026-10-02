import json
import os
import subprocess
from pathlib import Path
from string import Template

import pytest
from common.config import RiskConfig

FREQTRADE_DIR = Path(__file__).parents[3] / "freqtrade"


def _render_config(*, stoploss_on_exchange: str = "true") -> dict:
    template = Template((FREQTRADE_DIR / "user_data" / "config.json.tpl").read_text())
    values = {name: "test" for name in template.get_identifiers()}
    values.update(
        DRY_RUN="true",
        STOPLOSS_ON_EXCHANGE=stoploss_on_exchange,
        FREQTRADE_DB_URL="sqlite:////freqtrade/db/tradesv3.dryrun.sqlite",
        PAIR_WHITELIST_JSON="[]",
    )
    return json.loads(template.substitute(values))


def test_freqtrade_does_not_duplicate_runtime_position_limit() -> None:
    """The audited RiskConfig value must be the only concurrent-position cap."""
    rendered = _render_config()

    assert RiskConfig().max_open_positions == 2
    assert RiskConfig().signal_max_age_minutes == 65
    assert rendered["max_open_trades"] == -1
    assert isinstance(rendered["stake_amount"], (int, float))


@pytest.mark.parametrize(
    ("dry_run", "expected_url"),
    [
        ("true", "sqlite:////freqtrade/db/tradesv3.dryrun.sqlite"),
        ("false", "sqlite:////freqtrade/db/tradesv3.sqlite"),
    ],
)
def test_freqtrade_uses_mode_specific_database(dry_run: str, expected_url: str) -> None:
    script_path = Path(__file__).parents[3] / "freqtrade" / "select-db-url.sh"
    result = subprocess.run(
        ["sh", "-c", f'. "{script_path}"; printf %s "$FREQTRADE_DB_URL"'],
        check=True,
        capture_output=True,
        env={**os.environ, "DRY_RUN": dry_run},
        text=True,
    )

    assert result.stdout == expected_url


def test_freqtrade_rejects_invalid_dry_run_value() -> None:
    script_path = Path(__file__).parents[3] / "freqtrade" / "select-db-url.sh"
    result = subprocess.run(
        ["sh", str(script_path)],
        check=False,
        capture_output=True,
        env={**os.environ, "DRY_RUN": "yes"},
        text=True,
    )

    assert result.returncode != 0
    assert "DRY_RUN must be exactly" in result.stderr


@pytest.mark.parametrize(("flag", "expected"), [("true", True), ("false", False)])
def test_freqtrade_stoploss_on_exchange_follows_env_flag(flag: str, expected: bool) -> None:
    order_types = _render_config(stoploss_on_exchange=flag)["order_types"]

    assert order_types["stoploss_on_exchange"] is expected


def test_freqtrade_order_types_keep_bot_side_stop_market_and_exchange_stop_safe() -> None:
    """PROJECT.md Section 9.2: exchange stop-limit as VPS-down backup, bot
    stop check market-exits. Config order_types replaces the strategy's dict
    wholesale, so every required key must be present."""
    rendered = _render_config()
    order_types = rendered["order_types"]

    assert {"entry", "exit", "stoploss", "stoploss_on_exchange"} <= order_types.keys()
    # Unchanged entry/ROI behaviour (these were Freqtrade's defaults already).
    assert order_types["entry"] == "limit"
    assert order_types["exit"] == "limit"
    # Bot-side stop hits exit at market. On Binance spot the exchange order
    # still falls back to STOP_LOSS_LIMIT (the only stop type Freqtrade maps).
    assert order_types["stoploss"] == "market"
    assert order_types["emergency_exit"] == "market"
    assert order_types["stoploss_on_exchange_limit_ratio"] == 0.97
    # Freqtrade warns lowering this risks an exchange ban.
    assert order_types["stoploss_on_exchange_interval"] >= 60
    # The exchange stop must survive a bot stop/restart/crash.
    assert rendered["cancel_open_orders_on_exit"] is False


@pytest.mark.parametrize("flag", ["true", "false"])
def test_freqtrade_accepts_boolean_stoploss_on_exchange(flag: str) -> None:
    result = subprocess.run(
        ["sh", str(FREQTRADE_DIR / "validate-stoploss-on-exchange.sh")],
        check=False,
        capture_output=True,
        env={**os.environ, "STOPLOSS_ON_EXCHANGE": flag},
        text=True,
    )

    assert result.returncode == 0


@pytest.mark.parametrize("flag", ["yes", "True", "1", ""])
def test_freqtrade_rejects_invalid_stoploss_on_exchange_value(flag: str) -> None:
    result = subprocess.run(
        ["sh", str(FREQTRADE_DIR / "validate-stoploss-on-exchange.sh")],
        check=False,
        capture_output=True,
        env={**os.environ, "STOPLOSS_ON_EXCHANGE": flag},
        text=True,
    )

    assert result.returncode != 0
    assert "STOPLOSS_ON_EXCHANGE must be exactly" in result.stderr


def test_freqtrade_rejects_missing_stoploss_on_exchange() -> None:
    env = {k: v for k, v in os.environ.items() if k != "STOPLOSS_ON_EXCHANGE"}
    result = subprocess.run(
        ["sh", str(FREQTRADE_DIR / "validate-stoploss-on-exchange.sh")],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode != 0


def test_freqtrade_entrypoint_validates_stoploss_flag_before_rendering_config() -> None:
    entrypoint = (FREQTRADE_DIR / "docker-entrypoint.sh").read_text()
    dockerfile = (FREQTRADE_DIR / "Dockerfile").read_text()

    validate_at = entrypoint.index(". /validate-stoploss-on-exchange.sh")
    assert validate_at < entrypoint.index("template.substitute")
    assert (
        "COPY freqtrade/validate-stoploss-on-exchange.sh /validate-stoploss-on-exchange.sh"
        in dockerfile
    )


def test_freqtrade_image_is_pinned_to_verified_version() -> None:
    """ExternalSignalStrategy overrides Freqtrade internals verified on 2026.8;
    a floating :stable tag could silently change that behaviour."""
    dockerfile = (FREQTRADE_DIR / "Dockerfile").read_text()

    assert "FROM freqtradeorg/freqtrade:2026.8\n" in dockerfile
