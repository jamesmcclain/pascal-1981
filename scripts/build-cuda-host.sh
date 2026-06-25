#!/usr/bin/env bash
#
# End-to-end recipe: compile a Pascal DEVICE UNIT + host PROGRAM and run the
# kernel on a real GPU through the CUDA Driver API shim (cuda-kernel-prescription
# §5.2 Strategy 1).  This is the GPU counterpart of the CPU-device stand-in; the
# Pascal sources are byte-for-byte the same, only the runtime shim differs.
#
# Pipeline:
#   1. device unit  -> PTX  (--device-triple nvptx64-nvidia-cuda, via compile_to_ptx)
#   2. device unit  -> host x86 .ll  (defines the kernel symbol the host launch
#                                     thunk references; dead code at run time,
#                                     the real kernel comes from the PTX)
#   3. interface    -> .ll
#   4. host program -> .ll, embedding the PTX via --embed-device-ptx
#   5. link main.ll + device .ll + the CUDA runtime archive + -lcuda
#   6. run on the GPU
#
# Usage:
#   scripts/build-cuda-host.sh DEVICE_UNIT.pas IFACE.inc HOST_MAIN.pas OUT_EXE \
#       [-- extra pascal1981 feature flags, e.g. -f wide-integers]
#
# Requirements: an NVIDIA GPU + driver, llvmlite with the NVPTX backend, clang,
# and the CUDA toolkit headers.  Build the runtime archive with the CUDA shim
# first:  make -C runtime DEVICE_SHIM=cuda
set -euo pipefail

if [ "$#" -lt 4 ]; then
    sed -n '2,30p' "$0"
    exit 2
fi

DEVICE_UNIT=$1; IFACE=$2; HOST_MAIN=$3; OUT_EXE=$4; shift 4
PAS_FLAGS=()
if [ "${1:-}" = "--" ]; then shift; PAS_FLAGS=("$@"); fi

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CUDA_HOME=${CUDA_HOME:-/usr/local/cuda}
SM=${SM:-sm_89}
RUNTIME_LIB=$REPO_ROOT/runtime/build/libpascalrt.a
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PAS() { PYTHONPATH="$REPO_ROOT/src" python3 -m pascal1981 "$@"; }
PTX() { PYTHONPATH="$REPO_ROOT/src" python3 -m pascal1981.compile_to_ptx "$@"; }

# Ensure the CUDA shim is in the runtime archive (rebuild if missing/stale).
if ! ar t "$RUNTIME_LIB" 2>/dev/null | grep -q '^cuda_launch.o$'; then
    echo ">> building runtime with the CUDA shim (DEVICE_SHIM=cuda)" >&2
    make -C "$REPO_ROOT/runtime" clean >/dev/null
    make -C "$REPO_ROOT/runtime" DEVICE_SHIM=cuda >/dev/null
fi

echo ">> 1. device unit -> PTX" >&2
PTX "$DEVICE_UNIT" "$WORK/dev.ptx" --cpu "$SM" "${PAS_FLAGS[@]}"

echo ">> 2. device unit -> host .ll (defines the kernel symbol)" >&2
PAS "${PAS_FLAGS[@]}" "$DEVICE_UNIT" "$WORK/dev.ll" >/dev/null

echo ">> 3. interface -> .ll" >&2
PAS "${PAS_FLAGS[@]}" "$IFACE" "$WORK/iface.ll" >/dev/null

echo ">> 4. host program -> .ll (embedding PTX)" >&2
PAS "${PAS_FLAGS[@]}" --embed-device-ptx "$WORK/dev.ptx" "$HOST_MAIN" "$WORK/main.ll" >/dev/null

echo ">> 5. link host + device .ll + CUDA shim" >&2
clang "$WORK/main.ll" "$WORK/dev.ll" "$RUNTIME_LIB" \
    -L"$CUDA_HOME/lib64/stubs" -lcuda -o "$OUT_EXE"

echo ">> 6. done: $OUT_EXE" >&2
