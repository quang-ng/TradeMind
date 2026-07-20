import json
from pathlib import Path
from string import Template

from common.config import RiskConfig


def test_freqtrade_does_not_duplicate_runtime_position_limit() -> None:
    """The audited RiskConfig value must be the only concurrent-position cap."""
    template_path = (
        Path(__file__).parents[3] / "freqtrade" / "user_data" / "config.json.tpl"
    )
    template = Template(template_path.read_text())
    values = {name: "test" for name in template.get_identifiers()}
    values.update(DRY_RUN="true", PAIR_WHITELIST_JSON="[]")

    rendered = json.loads(template.substitute(values))

    assert RiskConfig().max_open_positions == 2
    assert rendered["max_open_trades"] == -1
