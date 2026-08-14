#!/usr/bin/env bash
# Build one native (pascal1981-dialect) compiler stage into a standalone
# linked binary. Every stage USES jsonutil, so this always compiles and
# links jsonutil.pas's object file alongside the stage's own source.
#
# Usage: scripts/build-native-stage.sh <stage.pas> <output-binary> [extra clang args...]
#
# Extra clang args are appended to the link line, e.g. for a stage that also
# needs LLVM:
#   scripts/build-native-stage.sh src/pascal1981/pascal_src/codegen.pas \
#     /tmp/pascal1981-codegen-native -L/usr/lib/llvm-20/lib -lLLVM-20
#
# By default jsonutil.o is built via the pascal1981 Python CLI (hybrid
# build). Set NATIVE_JSONUTIL to the path of a native-built codegen.pas
# binary to instead generate jsonutil.o's IR with that native codegen
# (front-end stages -- lex/parse/typecheck -- still run via the Python
# CLI's --split-processes-equivalent stdin/stdout JSON protocol, which is
# byte-for-byte what the native lexer/parser/typechecker binaries consume
# and produce; only code generation is swapped to native). This exercises
# native-compiled callers linked against a native-compiled jsonutil.o, e.g.
# for verifying the byval aggregate-parameter fix (§1.1) under native+native
# linkage rather than only the default hybrid linkage.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <stage.pas> <output-binary> [extra clang args...]" >&2
  exit 1
fi

stage_src="$1"
out_bin="$2"
shift 2
extra_args=("$@")

src_dir="src/pascal1981/pascal_src"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

jsonutil_obj="$work_dir/jsonutil.o"
stage_ll="$work_dir/$(basename "$stage_src" .pas).ll"

(
  cd "$src_dir"
  if [ -n "${NATIVE_JSONUTIL:-}" ]; then
    jsonutil_ll="$work_dir/jsonutil.ll"
    python3 -m pascal1981.cli_lex jsonutil.pas | \
      python3 -m pascal1981.cli_parse --source-file jsonutil.pas --dialect extended | \
      python3 -m pascal1981.cli_typecheck --source-file jsonutil.pas --dialect extended | \
      "$NATIVE_JSONUTIL" > "$jsonutil_ll"
    clang -c "$jsonutil_ll" -o "$jsonutil_obj"
  else
    pascal1981 --dialect extended -c jsonutil.pas -o "$jsonutil_obj"
  fi
  pascal1981 --dialect extended -S "$(basename "$stage_src")" -o "$stage_ll"
)

clang "$stage_ll" "$jsonutil_obj" -lcjson \
  "${extra_args[@]}" \
  "$(pascal1981 -print-file-name=libpascalrt.a)" \
  -o "$out_bin"

echo "built: $out_bin"
