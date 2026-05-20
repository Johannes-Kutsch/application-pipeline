#!/usr/bin/env bash
set -euo pipefail

SETTINGS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# application-pipeline:$SETTINGS_DIR"

existing="$(crontab -l 2>/dev/null || true)"
if [ -z "$existing" ]; then
  exit 0
fi

filtered="$(echo "$existing" | grep -v "$MARKER" || true)"
if [ "$existing" = "$filtered" ]; then
  exit 0
fi

if [ -z "$filtered" ]; then
  crontab -r 2>/dev/null || true
else
  echo "$filtered" | crontab -
fi
