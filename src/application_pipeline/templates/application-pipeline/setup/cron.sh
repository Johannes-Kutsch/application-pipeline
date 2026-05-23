#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

mkdir -p "application-pipeline/.runtime-data"

(
  flock -n 9 || exit 0

  fail() {
    local stage="$1"
    local msg="$2"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local fname
    fname="$(echo "$ts" | tr ':' '-').md"
    mkdir -p "application-pipeline/.runtime-data/failures"
    cat > "application-pipeline/.runtime-data/failures/$fname" <<EOF
# Run failed at $ts

**Stage:** $stage
**Error:** ShellError: $msg
**Last 20 log lines:**
\`\`\`
$(tail -20 "application-pipeline/.runtime-data/logs/cron.log" 2>/dev/null || true)
\`\`\`
EOF
  }

  _pip_stderr="$(pip install --upgrade application-pipeline 2>&1 1>/dev/null)" \
    || echo "WARNING: pip install --upgrade application-pipeline (attempt 1) failed: $_pip_stderr"
  _pip_stderr="$(pip install --upgrade application-pipeline 2>&1 1>/dev/null)" \
    || echo "WARNING: pip install --upgrade application-pipeline (attempt 2) failed: $_pip_stderr"

  application-pipeline init --refresh || { fail "ShellError" "application-pipeline init --refresh failed"; exit 1; }
  application-pipeline run || { fail "ShellError" "application-pipeline run failed"; exit 1; }

  tail -n 10000 "application-pipeline/.runtime-data/logs/cron.log" > "application-pipeline/.runtime-data/logs/cron.log.tmp" 2>/dev/null \
    && mv "application-pipeline/.runtime-data/logs/cron.log.tmp" "application-pipeline/.runtime-data/logs/cron.log" \
    || true

) 9>application-pipeline/.runtime-data/.cron.lock
