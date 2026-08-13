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
  pascal1981 --dialect extended -c jsonutil.pas -o "$jsonutil_obj"
  pascal1981 --dialect extended -S "$(basename "$stage_src")" -o "$stage_ll"
)

clang "$stage_ll" "$jsonutil_obj" -lcjson \
  "${extra_args[@]}" \
  "$(pascal1981 -print-file-name=libpascalrt.a)" \
  -o "$out_bin"

echo "built: $out_bin"
