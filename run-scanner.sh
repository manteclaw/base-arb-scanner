#!/bin/bash
# Continuous scanner loop (read-only, no trades ever).
# Usage: ./run-scanner.sh [interval_seconds]
set -e
cd "$(dirname "$0")"
INTERVAL="${1:-${SCAN_INTERVAL:-30}}"
python3 scanner.py --loop --interval "$INTERVAL"
