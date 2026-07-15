#!/bin/sh
set -eu

python3 - <<'PYEOF'
import os
import string

with open("/freqtrade/user_data/config.json.tpl") as f:
    template = string.Template(f.read())

with open("/freqtrade/user_data/config.json", "w") as f:
    f.write(template.substitute(os.environ))
PYEOF

exec freqtrade trade --config /freqtrade/user_data/config.json --strategy ExternalSignalStrategy
