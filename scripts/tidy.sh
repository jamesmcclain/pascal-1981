#!/usr/bin/env bash
# Remove build byproducts. Safe with spaces in paths and with zero matches
# (the old `rm -r $(find | grep ...)` errored when nothing matched and
# word-split any path containing whitespace).
set -euo pipefail
cd "$(dirname "$0")/.."

find . -type d -name '__pycache__' -prune -exec rm -rf {} +
make -C runtime clean
