import json
import os
import subprocess
from pathlib import Path
from string import Template

import pytest
from common.config import RiskConfig


def test_freqtrade_does_not_duplicate_runtime_position_limit() -> None:
    """The audited RiskConfig value must be the only concurrent-position cap."""
    template_path = (
        Path(__file__).parents[3] / "freqtrade" / "user_data" / "config.json.tpl"
    )
    template = Template(template_path.read_text())
    values = {name: "test" for name in template.get_identifiers()}
    values.update(
        DRY_RUN="true",
        FREQTRADE_DB_URL="sqlite:////freqtrade/db/tradesv3.dryrun.sqlite",
        PAIR_WHITELIST_JSON="[]",
    )

    rendered = json.loads(template.substitute(values))

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
