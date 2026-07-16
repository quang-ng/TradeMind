#!/bin/sh
set -eu

python3 - <<'PYEOF'
import json
import os
import string

with open("/freqtrade/user_data/config.json.tpl") as f:
    template = string.Template(f.read())

symbols = [s.strip() for s in os.environ.get("SYMBOLS", "").split(",") if s.strip()]
values = dict(os.environ, PAIR_WHITELIST_JSON=json.dumps(symbols))

with open("/freqtrade/user_data/config.json", "w") as f:
    f.write(template.substitute(values))
PYEOF

exec freqtrade trade --config /freqtrade/user_data/config.json --strategy ExternalSignalStrategy
