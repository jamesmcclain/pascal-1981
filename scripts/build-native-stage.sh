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
# By default both jsonutil.o and the stage itself are built via the
# pascal1981 Python CLI (hybrid build). Two independent opt-in env vars
# swap in native code generation:
#
#   NATIVE_CODEGEN=<native-codegen-binary>   also native-codegens the stage
#   NATIVE_JSONUTIL=<native-codegen-binary>  native-codegens jsonutil.o only
#                                             (defaults to NATIVE_CODEGEN's
#                                             value when NATIVE_CODEGEN is
#                                             set but NATIVE_JSONUTIL isn't,
#                                             so one var suffices for "make
#                                             everything native")
#
# In both cases the front-end (lex/parse/typecheck) still runs via the
# Python CLI's --split-processes-equivalent stdin/stdout JSON protocol by
# default, which is byte-for-byte what the native lexer/parser/typechecker
# binaries consume and produce -- unless NATIVE_LEXER/NATIVE_PARSER/
# NATIVE_TYPECHECKER are all three also given, in which case those native
# binaries replace the Python front-end entirely (true native+native+native
# -- no Python-toolchain involvement at any stage, per §1.4's bootstrap
# definition).
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

# The stages recurse (recursive-descent parsing, recursive AST lowering), and
# at -O0 every by-value Str255 argument gets its own spill slot, so one
# expression-nesting level costs ~114KB of frame. -O1 folds those away and
# brings the same level down to ~37KB -- an 8x cut in stack per unit of
# nesting, which is what lets the stages run inside the default 8MB stack
# instead of needing `ulimit -s unlimited` from whoever invokes them. Override
# with STAGE_OPT= to build unoptimized.
STAGE_OPT="${STAGE_OPT--O1}"

src_dir="src/pascal1981/pascal_src"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

jsonutil_obj="$work_dir/jsonutil.o"
stage_ll="$work_dir/$(basename "$stage_src" .pas).ll"

native_codegen="${NATIVE_CODEGEN:-}"
native_jsonutil="${NATIVE_JSONUTIL:-$native_codegen}"

# run_frontend <source.pas>: emits typechecked-AST JSON on stdout, via the
# native lexer/parser/typechecker binaries if all three are given, else via
# the Python CLI's equivalent stdin/stdout JSON protocol.
run_frontend() {
  local src_file="$1"
  if [ -n "${NATIVE_LEXER:-}" ] && [ -n "${NATIVE_PARSER:-}" ] && [ -n "${NATIVE_TYPECHECKER:-}" ]; then
    "$NATIVE_LEXER" < "$src_file" | "$NATIVE_PARSER" | "$NATIVE_TYPECHECKER"
  else
    python3 -m pascal1981.cli_lex "$src_file" | \
      python3 -m pascal1981.cli_parse --source-file "$src_file" --dialect extended | \
      python3 -m pascal1981.cli_typecheck --source-file "$src_file" --dialect extended
  fi
}

(
  cd "$src_dir"
  if [ -n "$native_jsonutil" ]; then
    jsonutil_ll="$work_dir/jsonutil.ll"
    run_frontend jsonutil.pas | "$native_jsonutil" > "$jsonutil_ll"
    clang $STAGE_OPT -c "$jsonutil_ll" -o "$jsonutil_obj"
  else
    pascal1981 --dialect extended -c jsonutil.pas -o "$jsonutil_obj"
  fi
  if [ -n "$native_codegen" ]; then
    run_frontend "$(basename "$stage_src")" | "$native_codegen" > "$stage_ll"
  else
    pascal1981 --dialect extended -S "$(basename "$stage_src")" -o "$stage_ll"
  fi
)

clang $STAGE_OPT "$stage_ll" "$jsonutil_obj" -lcjson \
  "${extra_args[@]}" \
  "$(pascal1981 -print-file-name=libpascalrt.a)" \
  -o "$out_bin"

echo "built: $out_bin"
