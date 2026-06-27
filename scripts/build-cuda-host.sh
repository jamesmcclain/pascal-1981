#!/usr/bin/env bash
#
# End-to-end recipe: compile a Pascal DEVICE UNIT + host PROGRAM and run the
# kernel on a real GPU through the CUDA Driver API shim (cuda-kernel-prescription
# §5.2 Strategy 1).  The Pascal sources are byte-for-byte the same as the CPU
# stand-in; only the runtime shim differs.
#
# Three commands (the runtime archive is prebuilt, not rebuilt here):
#   1. device unit -> PTX            (pascal1981 --target ptx)
#   2. host program -> .ll           (pascal1981 --device-backend cuda)
#   3. objectify the PTX blob + link (clang)
#
# The host is compiled with --device-backend cuda, so it emits no in-process
# launch thunk and no kernel-symbol reference -- there is no second device
# compile ('dev.ll').  The PTX text is packaged as its own data object, a
# NUL-terminated __pas_device_ptx blob the host references as an external symbol;
# the CUDA shim cuModuleLoadData's it at run time.  (This is a data blob, NOT
# ptxas/cubin output -- hence _blob.o, never .ptx.o.)
#
# Usage:
#   scripts/build-cuda-host.sh DEVICE_UNIT.pas HOST_MAIN.pas OUT_EXE \
#       [-- extra pascal1981 feature flags, e.g. -f wide-integers]
#
# Requirements: an NVIDIA GPU + driver, llvmlite with the NVPTX backend, clang,
# and the CUDA toolkit headers.  Build the cuda runtime archive once first:
#   make -C runtime cuda
set -euo pipefail

if [ "$#" -lt 3 ]; then
    sed -n '2,30p' "$0"
    exit 2
fi

DEVICE_UNIT=$1; HOST_MAIN=$2; OUT_EXE=$3; shift 3
PAS_FLAGS=()
if [ "${1:-}" = "--" ]; then shift; PAS_FLAGS=("$@"); fi

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CUDA_HOME=${CUDA_HOME:-/usr/local/cuda}
SM=${SM:-sm_89}
RUNTIME_CUDA=$REPO_ROOT/runtime/build/libpascalrt_cuda.a
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PAS() { PYTHONPATH="$REPO_ROOT/src" python3 -m pascal1981 "$@"; }

if [ ! -f "$RUNTIME_CUDA" ]; then
    echo ">> building the cuda runtime archive (make -C runtime cuda)" >&2
    make -C "$REPO_ROOT/runtime" cuda >/dev/null
fi

echo ">> 1. device unit -> PTX" >&2
PAS --target ptx "$DEVICE_UNIT" "$WORK/dev.ptx" --sm "$SM" "${PAS_FLAGS[@]}" >/dev/null

echo ">> 2. host program -> .ll (device-backend cuda)" >&2
PAS "${PAS_FLAGS[@]}" --device-backend cuda "$HOST_MAIN" "$WORK/host.ll" >/dev/null

echo ">> 3. objectify PTX blob + link" >&2
printf '\t.section .rodata\n\t.globl __pas_device_ptx\n__pas_device_ptx:\n\t.incbin "%s"\n\t.byte 0\n' \
    "$WORK/dev.ptx" > "$WORK/dev_ptx_blob.s"
clang -c "$WORK/dev_ptx_blob.s" -o "$WORK/dev_ptx_blob.o"
clang "$WORK/host.ll" "$WORK/dev_ptx_blob.o" "$RUNTIME_CUDA" \
    -L"$CUDA_HOME/lib64/stubs" -lcuda -o "$OUT_EXE"

echo ">> done: $OUT_EXE" >&2
