#!/usr/bin/env bash
set -euo pipefail

SETTINGS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$SETTINGS_DIR/config.py"
cd "$SETTINGS_DIR/.."

(
  flock -n 9 || exit 0

  fail() {
    local stage="$1"
    local msg="$2"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local fname
    fname="$(echo "$ts" | tr ':' '-').md"
    mkdir -p "$SETTINGS_DIR/failures"
    cat > "$SETTINGS_DIR/failures/$fname" <<EOF
# Run failed at $ts

**Stage:** $stage
**Error:** ShellError: $msg
**Last 20 log lines:**
\`\`\`
$(tail -20 "$SETTINGS_DIR/logs/cron.log" 2>/dev/null || true)
\`\`\`
EOF
  }

  pip install --upgrade application-pipeline || { fail "ShellError" "pip install --upgrade application-pipeline (first attempt) failed"; exit 1; }
  pip install --upgrade application-pipeline || { fail "ShellError" "pip install --upgrade application-pipeline (second attempt) failed"; exit 1; }

  application-pipeline init --refresh || { fail "ShellError" "application-pipeline init --refresh failed"; exit 1; }
  application-pipeline run "$CONFIG" || { fail "ShellError" "application-pipeline run failed"; exit 1; }

  tail -n 10000 "$SETTINGS_DIR/logs/cron.log" > "$SETTINGS_DIR/logs/cron.log.tmp" 2>/dev/null \
    && mv "$SETTINGS_DIR/logs/cron.log.tmp" "$SETTINGS_DIR/logs/cron.log" \
    || true

) 9>"$SETTINGS_DIR/.cron.lock"
