#!/usr/bin/env bash
set -euo pipefail

SETTINGS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SH="$SETTINGS_DIR/setup/cron.sh"
MARKER="# application-pipeline:$SETTINGS_DIR"
CRON_LINE="30 0 * * 1-5 $CRON_SH >> $SETTINGS_DIR/logs/cron.log 2>&1 $MARKER"

chmod +x "$CRON_SH" \
  "$(dirname "$CRON_SH")/cron-install.sh" \
  "$(dirname "$CRON_SH")/cron-uninstall.sh"

(
  crontab -l 2>/dev/null | grep -v "$MARKER" || true
  echo "$CRON_LINE"
) | crontab -
