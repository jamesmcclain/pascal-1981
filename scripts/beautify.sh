#!/usr/bin/env bash
# Format Python (isort + yapf) and C (GNU indent) sources in place.
# find -exec is used instead of `$(find | grep ...)` so paths with
# whitespace can't word-split, and skips .git/venv/build byproducts.
set -euo pipefail
cd "$(dirname "$0")/.."

# A formatter can be present on PATH (satisfying `command -v`/`which`, and
# thus tests/support.py's HAS_FORMATTERS skip gate) yet still not actually
# run -- e.g. a `pip install --user` shim left behind after the Python
# environment it points at was replaced or had the package removed. Left
# unchecked, that surfaces many `find -exec` invocations deep inside a bare
# ModuleNotFoundError traceback, which reads like a bug in this script (or
# the pre-commit hook that calls it) rather than what it actually is: a
# broken local toolchain. Check each tool actually runs before touching any
# source file, so the failure is unambiguous and names the fix.
fail_broken() {
    local tool="$1" hint="$2"
    echo "beautify.sh: '$tool' is on PATH but does not run (broken install," >&2
    echo "or a shebang pointing at a Python environment that no longer has" >&2
    echo "it installed). $hint" >&2
    exit 1
}

require_on_path() {
    local tool="$1" hint="$2"
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "beautify.sh: '$tool' not found on PATH. $hint" >&2
        exit 1
    fi
}

isort_hint="Install with: pip install isort (into whichever Python environment's bin/ is first on PATH)."
yapf_hint="Install with: pip install yapf (into whichever Python environment's bin/ is first on PATH)."
indent_hint="Install GNU indent (e.g. apt install indent, brew install gnu-indent)."

require_on_path isort "$isort_hint"
isort --version >/dev/null 2>&1 || fail_broken isort "$isort_hint"

require_on_path yapf "$yapf_hint"
yapf --version >/dev/null 2>&1 || fail_broken yapf "$yapf_hint"

require_on_path indent "$indent_hint"
# Not `indent --version`: GNU indent exits nonzero on it regardless of
# whether the binary works (a documented quirk, not a health signal), so
# probe the same way it's actually used instead -- format a trivial
# snippet through stdin/stdout, which does exit 0 on a working install.
printf 'int f(void){return 1;}\n' | indent >/dev/null 2>&1 || fail_broken indent "$indent_hint"

find src tests setup.py -name '*.py' -not -path '*/__pycache__/*' -exec isort {} +
find src tests setup.py -name '*.py' -not -path '*/__pycache__/*' -exec yapf -i {} +
VERSION_CONTROL=none find runtime -name '*.c' -exec indent -kr -nut -l180 {} +
