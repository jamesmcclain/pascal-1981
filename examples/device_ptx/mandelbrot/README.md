# Mandelbrot device kernels — a CUDA PTX drop-in

This example is a Pascal `DEVICE UNIT` that produces a `mandelbrot.ptx` artifact
intended as a **drop-in replacement** for the PTX built from `mandelbrot.cu` in
the companion *mandelbrot-gpu* repository. The host launcher there
(`mandelbrot_cuda.py`, PyCUDA) loads `mandelbrot.ptx`, looks up
`mandelbrot_f32` / `mandelbrot_f64` by name, and launches them with a positional
argument list — so the Pascal kernels must match the CUDA kernels symbol-for-symbol
and parameter-for-parameter.

## Files

```text
mandelbrot.inc        # DEVICE INTERFACE; exports mandelbrot_f32 and mandelbrot_f64
mandelbrot.pas        # DEVICE IMPLEMENTATION OF MANDELBROT; the two kernel bodies
mandelbrot_host.pas   # host PROGRAM: orchestration + fixed palette + ASCII render
Makefile              # builds + runs the full example (DEVICE=cpu|cuda)
README.md             # this document
```

(By repository convention the interface file uses the `.inc` extension; the
compiler does not require it.)

## Building and running the full example

`mandelbrot_host.pas` is a Pascal host PROGRAM that does the device orchestration
(`DEVALLOC` / `LAUNCH` / `DEVCOPYFROM`), launches `mandelbrot_f64` over a fixed
view, turns the returned escape counts into an in-memory RGB image with a fixed
"fire" palette, and prints an ASCII reduction so a run is observably correct
(nothing is written to disk). It is a deliberately minimal cousin of the host in
the companion *mandelbrot-gpu* repository.

```bash
cd examples/device_ptx/mandelbrot
make DEVICE=cuda run     # build the host + device, run on the GPU
```

`DEVICE` selects the device-orchestration runtime shim at build time:

- `DEVICE=cuda` — the real GPU path (CUDA Driver API shim + embedded PTX). Needs
  the CUDA toolkit headers, `-lcuda`, and an NVIDIA device. `SM` defaults to
  `sm_86` to mirror `mandelbrot.cu`.
- `DEVICE=cpu` (the default) — the CPU-device stand-in, **not yet wired for this
  example**; see [`../CPU_DEVICE_TODO.md`](../CPU_DEVICE_TODO.md) (it needs a
  grid-stride kernel, a deferred kernel change).

The host orchestration is compiler-generated from the Pascal source; only the
leaf runtime shim is C. The kernels are unchanged, so the emitted PTX remains the
drop-in described next. Build rules live in
[`../device-example.mk`](../device-example.mk).

The GPU build is now three commands (the runtime archive is prebuilt once with
`make -C runtime cuda`): device unit -> PTX (`--target ptx`); host program ->
`.ll` (`--device-backend cuda`, which emits no launch thunk and no kernel-symbol
reference, so there is **no** second device compile); then one `clang` link of
`host.ll` + the PTX-blob object + `libpascalrt_cuda.a` `-lcuda`. The PTX text is
packaged as its own NUL-terminated `__pas_device_ptx` data object (a `*_blob.o`,
**not** `ptxas`/cubin output) that the host references as an external symbol.

## The ABI being matched

From `mandelbrot.cu`:

```c
extern "C" __global__
void mandelbrot_f32(int* output, int width, int height, int max_iter,
                    float  x_min, float  x_max, float  y_min, float  y_max);
extern "C" __global__
void mandelbrot_f64(int* output, int width, int height, int max_iter,
                    double x_min, double x_max, double y_min, double y_max);
```

The Pascal interface mirrors this exactly. The output buffer is a
`SUPER ARRAY [0..*] OF INTEGER32` behind an `ADS(GLOBAL)` pointer, which lowers to
a raw `int*` device pointer; image dimensions ride in `width`/`height`, as in CUDA.
`mandelbrot_f32` uses the `REAL32` type (LLVM `float`) so its coordinate
parameters are genuinely 32-bit; `mandelbrot_f64` uses `REAL64` (≡ `REAL`, f64).

## Build the PTX

```bash
PYTHONPATH=src python3 -m pascal1981 --target ptx \
  examples/device_ptx/mandelbrot/mandelbrot.pas \
  examples/device_ptx/mandelbrot/mandelbrot.ptx \
  --sm sm_86 -f wide-integers
```

`--target ptx` on the single `pascal1981` driver replaces the old
`python -m pascal1981.compile_to_ptx` (still accepted as a deprecated alias;
`--sm` replaces `--cpu`). It needs `llvmlite`/LLVM with the NVPTX backend; it
needs **no** NVIDIA device, CUDA driver/runtime, `nvcc`, or the Pascal runtime
library.

## Inspect the artifact

```bash
# both kernels are launchable, void-returning entries
grep '\.visible \.entry mandelbrot_f32' mandelbrot.ptx
grep '\.visible \.entry mandelbrot_f64' mandelbrot.ptx

# the f32 kernel takes .f32 coordinates; the f64 kernel takes .f64
grep '\.param \.f32' mandelbrot.ptx
grep '\.param \.f64' mandelbrot.ptx

# a kernel must NOT declare a return slot
! grep -q 'func_retval' mandelbrot.ptx && echo 'no return slot (good)'

# 2-D CUDA indexing and the 32-bit global store
grep '%tid.y' mandelbrot.ptx
grep 'st.global.u32' mandelbrot.ptx
```

If NVIDIA tools are present, `ptxas` is a stronger check:

```bash
ptxas -arch=sm_86 -v -o mandelbrot.cubin mandelbrot.ptx
```

## Running it for real

To actually substitute for the CUDA-built PTX, place the generated
`mandelbrot.ptx` where the *mandelbrot-gpu* launcher expects it and run the
existing PyCUDA script on a CUDA-capable machine. The f64 path can run with no
launcher change; the f32 path is now ABI-correct (`.f32` parameters), so the
existing `get_function("mandelbrot_f32")` call site applies unchanged.

The on-device launch + output diff is the final rung of the validation ladder in
`docs/mandelbrot-ptx-substitution-plan.md` and requires a GPU (see the container
recipe in `docs/cuda-kernel-prescription.md` §8). Everything up to "valid PTX with
the right ABI" is covered here and by
`tests/integration/test_device_mandelbrot_ptx.py`.
