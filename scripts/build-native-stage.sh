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
    # The native parser's recursive-descent walk is no more stack-bounded than
    # native codegen's lowering (see the note below): on a real source file it
    # segfaults partway through under the default 8MB limit. Raise it for the
    # whole native front end, not just codegen.
    ( ulimit -s unlimited
      "$NATIVE_LEXER" < "$src_file" | "$NATIVE_PARSER" | "$NATIVE_TYPECHECKER" )
  else
    python3 -m pascal1981.cli_lex "$src_file" | \
      python3 -m pascal1981.cli_parse --source-file "$src_file" --dialect extended | \
      python3 -m pascal1981.cli_typecheck --source-file "$src_file" --dialect extended
  fi
}

(
  cd "$src_dir"
  # Native codegen's recursive-descent expression/statement lowering is not
  # stack-bounded: long ELSE-IF chains (e.g. lexer.pas's GetKeywordCode) and
  # other deep AST shapes can exceed the default 8MB stack ulimit and
  # segfault partway through -- confirmed independent of this session's
  # changes (reproduces on a pre-RetypeExpr native codegen binary too) and
  # cured entirely by an unbounded stack, so it's a stack-depth limit, not a
  # correctness bug. Raise the limit for native codegen invocations only.
  if [ -n "$native_jsonutil" ]; then
    jsonutil_ll="$work_dir/jsonutil.ll"
    run_frontend jsonutil.pas | (ulimit -s unlimited && exec "$native_jsonutil") > "$jsonutil_ll"
    clang -c "$jsonutil_ll" -o "$jsonutil_obj"
  else
    pascal1981 --dialect extended -c jsonutil.pas -o "$jsonutil_obj"
  fi
  if [ -n "$native_codegen" ]; then
    run_frontend "$(basename "$stage_src")" | (ulimit -s unlimited && exec "$native_codegen") > "$stage_ll"
  else
    pascal1981 --dialect extended -S "$(basename "$stage_src")" -o "$stage_ll"
  fi
)

clang "$stage_ll" "$jsonutil_obj" -lcjson \
  "${extra_args[@]}" \
  "$(pascal1981 -print-file-name=libpascalrt.a)" \
  -o "$out_bin"

echo "built: $out_bin"
