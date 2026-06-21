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
mandelbrot.inc   # DEVICE INTERFACE; exports mandelbrot_f32 and mandelbrot_f64
mandelbrot.pas   # DEVICE IMPLEMENTATION OF MANDELBROT; the two kernel bodies
README.md        # this document
```

(By repository convention the interface file uses the `.inc` extension; the
compiler does not require it.)

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
PYTHONPATH=src python3 -m pascal1981.compile_to_ptx \
  examples/device_ptx/mandelbrot/mandelbrot.pas \
  examples/device_ptx/mandelbrot/mandelbrot.ptx \
  --emit-llvm examples/device_ptx/mandelbrot/mandelbrot.ll \
  --cpu sm_86
```

This needs `llvmlite`/LLVM with the NVPTX backend; it needs **no** NVIDIA device,
CUDA driver/runtime, `nvcc`, or the Pascal runtime library.

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
