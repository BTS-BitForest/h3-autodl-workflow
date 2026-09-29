#!/bin/bash
set -euo pipefail
install -m 755 /opt/h3-suite/scripts/bin/codex /usr/local/bin/codex
install -m 755 /opt/h3-suite/scripts/bin/dsh /usr/local/bin/dsh
if ! command -v python3 >/dev/null; then ln -s /opt/h3-python/bin/python /usr/local/bin/python3; fi
mkdir -p /root/.vscode-server/data/Machine
if [ ! -e /root/.vscode-server/data/Machine/settings.json ]; then
 install -m 644 /opt/h3-suite/agent-templates/vscode-settings.json /root/.vscode-server/data/Machine/settings.json
fi
/opt/h3-python/bin/python - <<'PY'
from pathlib import Path
p=Path('/root/.bashrc');s=p.read_text() if p.exists() else ''
line='[ -f /opt/h3-suite/network/environment.sh ] && . /opt/h3-suite/network/environment.sh\n'
if line not in s:p.write_text(line+s)
PY
