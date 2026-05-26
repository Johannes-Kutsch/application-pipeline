#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ ! -d ".venv" ]]; then
  echo "ERROR: .venv/ not found. Run: pip install --upgrade virtualenv && python -m venv .venv && .venv/bin/pip install application-pipeline" >&2
  exit 1
fi

_pip_stderr="$(.venv/bin/pip install --upgrade application-pipeline 2>&1 1>/dev/null)" \
  || echo "WARNING: .venv/bin/pip install --upgrade application-pipeline (attempt 1) failed: $_pip_stderr"
_pip_stderr="$(.venv/bin/pip install --upgrade application-pipeline 2>&1 1>/dev/null)" \
  || echo "WARNING: .venv/bin/pip install --upgrade application-pipeline (attempt 2) failed: $_pip_stderr"

.venv/bin/application-pipeline cron "$@"
