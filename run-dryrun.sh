#!/bin/bash
# One-off dry run: a single scan across all pairs, then exit.
# Read-only — the scanner never holds keys and never sends transactions.
set -e
cd "$(dirname "$0")"
python3 scanner.py
