#!/usr/bin/env bash
# Format Python (isort + yapf) and C (GNU indent) sources in place.
# find -exec is used instead of `$(find | grep ...)` so paths with
# whitespace can't word-split, and skips .git/venv/build byproducts.
set -euo pipefail
cd "$(dirname "$0")/.."

find src tests setup.py -name '*.py' -not -path '*/__pycache__/*' -exec isort {} +
find src tests setup.py -name '*.py' -not -path '*/__pycache__/*' -exec yapf -i {} +
VERSION_CONTROL=none find runtime -name '*.c' -exec indent -kr -nut -l180 {} +
