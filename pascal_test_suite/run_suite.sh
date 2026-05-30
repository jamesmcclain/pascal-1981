#!/usr/bin/env bash
# Usage: ./run_suite.sh /path/to/parser_dir   (defaults to current dir)
# Expects parser.py + lexer.py importable from $DIR.
DIR="${1:-.}"
here="$(cd "$(dirname "$0")" && pwd)"
fail=0
check() { # $1=expected accept|reject  $2=file
  out=$(python3 "$DIR/parser.py" "$2" 2>&1)
  echo "$out" | grep -q '^OK' && got=accept || got=reject
  if [ "$1" = "$got" ]; then printf '  ok   %s\n' "$(basename "$2")"
  else printf '  BUG  %s (want %s, got %s)\n' "$(basename "$2")" "$1" "$got"; fail=1; fi
}
echo "should_pass:"; for f in "$here"/should_pass/*.pas; do check accept "$f"; done
echo "should_fail:"; for f in "$here"/should_fail/*.pas; do check reject "$f"; done
echo "judgment_calls (informational, no verdict):"
for f in "$here"/judgment_calls/*.pas; do
  out=$(python3 "$DIR/parser.py" "$f" 2>&1); echo "$out" | grep -q '^OK' && got=accept || got=reject
  printf '  --   %s -> %s\n' "$(basename "$f")" "$got"
done
exit $fail
