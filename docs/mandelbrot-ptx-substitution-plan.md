# Mandelbrot PTX substitution plan

## Goal

Generate a PTX file from Pascal `DEVICE` source that can substitute for the
Mandelbrot PTX artifact currently produced from CUDA C, ideally with no changes
to the external launch site. Small launcher changes are acceptable for early
proofs, especially while validating the Pascal PTX path before adding Pascal
host-side CUDA orchestration.

This document is a planning artifact. It separates observed facts from inferred
engineering direction.

## Non-goals

- Pascal-generated CUDA/PyCUDA host orchestration.
- CUDA runtime or driver bindings in Pascal.
- Performance parity claims.
- Full CUDA source compatibility.
- Final scalar-width policy for the whole compiler.

The first success criterion is an inspectable and externally launchable PTX
kernel artifact with the right symbol, parameter ABI, indexing behavior, and
output memory layout.

## Evidence grades

Claims are tagged as:

- `[OBSERVED]` directly observed in repository code, tests, docs, or the vintage
  manual text.
- `[INFERRED]` a reasonable engineering conclusion from observed facts.
- `[UNVERIFIED]` plausible but not yet checked in the current codebase or on a
  CUDA-capable machine.

## Observed current PTX capability

[OBSERVED] The repository now has a PTX artifact path:

```bash
PYTHONPATH=src python3 -m pascal1981.compile_to_ptx \
  examples/device_ptx/fill_indices/fill.pas \
  examples/device_ptx/fill_indices/fill.ptx \
  --emit-llvm examples/device_ptx/fill_indices/fill.ll \
  --cpu sm_70
```

[OBSERVED] The `fill_indices` example proves this shape:

```pascal
DEVICE INTERFACE;
UNIT FILL (fill_indices);
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
END;
```

[OBSERVED] Generated PTX contains an exported entry, CUDA special-register reads,
and a global 32-bit store, e.g. `.visible .entry fill_indices`, `%tid.x`,
`%ctaid.x`, `%ntid.x`, and `st.global.u32`.

[OBSERVED] Milestone C added device-only builtins:

- `THREADIDX_X/Y/Z`
- `BLOCKIDX_X/Y/Z`
- `BLOCKDIM_X/Y/Z`
- `GRIDDIM_X/Y/Z`
- `SYNCTHREADS`

[OBSERVED] Normal host code rejects these builtins, while `DEVICE` source accepts
them, including `device=x86` CPU-device lowering.

[OBSERVED] `INTEGER32` is accepted in `DEVICE` source without requiring the broad
`wide-integers` feature flag. Host `INTEGER32` behavior remains feature-gated.

## Observed Mandelbrot external contract

[OBSERVED] Inspection of `mandelbrot.cu` (the companion mandelbrot-gpu repo)
found CUDA C kernels named:

```c
extern "C" __global__
void mandelbrot_f32(int* output, int width, int height, int max_iter,
                    float  x_min, float  x_max, float  y_min, float  y_max);
extern "C" __global__
void mandelbrot_f64(int* output, int width, int height, int max_iter,
                    double x_min, double x_max, double y_min, double y_max);
```

[OBSERVED] Inspection of `mandelbrot_cuda.py` found the PyCUDA pattern:

```python
mod = cuda.module_from_file("mandelbrot.ptx")
kernel = mod.get_function("mandelbrot_f32")   # or mandelbrot_f64
kernel(output_gpu, np.int32(WIDTH), np.int32(HEIGHT), np.int32(max_iter),
       real_dtype(x_min), real_dtype(x_max), real_dtype(y_min), real_dtype(y_max),
       block=(16, 16, 1), grid=(blocks_x, blocks_y, 1))
```

[OBSERVED] The CUDA kernels use two-dimensional CUDA indexing via `threadIdx.x/y`,
`blockIdx.x/y`, and `blockDim.x/y`, write to an `int*` output buffer, and use
width/height bounds checks.

[OBSERVED] **The exact ABI, re-read from source (correcting an earlier guess in
this document).** PyCUDA packs arguments *positionally*, so the order and widths
must match exactly:

| # | name | C type | PTX param |
| - | ---- | ------ | --------- |
| 0 | `output` | `int*` | `.u64 .ptr .global` |
| 1 | `width` | `int` | `.u32` |
| 2 | `height` | `int` | `.u32` |
| 3 | `max_iter` | `int` | `.u32` |
| 4 | `x_min` | `float`/`double` | `.f32`/`.f64` |
| 5 | `x_max` | `float`/`double` | `.f32`/`.f64` |
| 6 | `y_min` | `float`/`double` | `.f32`/`.f64` |
| 7 | `y_max` | `float`/`double` | `.f32`/`.f64` |

Note `max_iter` is the **fourth** parameter and the four coordinate bounds come
**last** — not the order this document originally sketched. The pixel-to-plane
mapping in the CUDA source is also specific and must be copied for output parity:

```c
float width_denom  = (width  > 1) ? (float)(width  - 1) : 1.0f;
float height_denom = (height > 1) ? (float)(height - 1) : 1.0f;
float x0 = x_min + (x_max - x_min) * (float)px / width_denom;
float y0 = y_min + (y_max - y_min) * (float)py / height_denom;
```

i.e. the denominator is `width-1`/`height-1` (guarded against the 1-pixel case),
not `width`/`height`.

## What no-change substitution requires

[INFERRED] To replace the CUDA-generated PTX without changing the Python launch
site, the Pascal-generated PTX must match:

1. PTX file name expected by the launcher, usually `mandelbrot.ptx`.
2. Kernel symbol name, e.g. `mandelbrot_f32`.
3. Parameter count.
4. Parameter order.
5. Parameter widths and ABI classes: pointer, 32-bit integer, 32-bit float,
   64-bit float, etc.
6. Output buffer element type and row-major layout.
7. Launch geometry assumptions: currently `block=(16,16,1)` and computed 2-D
   grid dimensions.

[OBSERVED] Symbol matching holds: exported Pascal device-unit procedures lower to
`.visible .entry <name>` using the exported procedure name, so `get_function`
resolves them.

[OBSERVED] Scalar-width matching is resolved by `REAL32` (Stage 4): `mandelbrot_f32`
parameters are `.f32` and `mandelbrot_f64` parameters are `.f64`.

[OBSERVED] **The load-bearing fix: kernel entries must return void.** Every device
`PROCEDURE` previously lowered to an `i32`-returning function (a harmless vintage
internal convention), which on a GPU triple produced a PTX entry with a
`func_retval0` slot and a `st.param.b32 [func_retval0]`. `cuLaunchKernel` provides
no return slot, so that is an ABI mismatch — a kernel `__global__` must be `void`.
Exported device-unit entries on a GPU triple now lower to `define ptx_kernel void`
with no return slot (the x86 CPU-device parity path keeps the i32 shape, so it
stays byte-identical). The existing codegen rule that a kernel entry must be a
`PROCEDURE` (not a value-returning `FUNCTION`) remains, GPU-triple-gated.

## Super arrays and the output-buffer problem

### What the vintage manual says

[OBSERVED] The OCR text in
`~/backup/IBM_Pascal_Compiler_Aug81_djvu.txt` says IBM Personal Computer Pascal
provides a super array type "to let array lengths vary" and that the lower bound
is given while the upper bound is undefined.

[OBSERVED] The manual says super arrays cannot be used directly for ordinary
variables, but can be used:

- as the type of a formal reference parameter; and
- as the referent type of a pointer.

[OBSERVED] The manual gives examples of the form:

```pascal
TYPE
  VECT = SUPER ARRAY [0..*] OF REAL;
VAR
  PVEC: AVECT;
  V10: VECT(10);
PROCEDURE SORT(VAR V: VECT);
```

[OBSERVED] The manual describes this parameter use as conformant-array behavior
and says `UPPER` returns the actual upper bound of a super-array parameter or
referent.

### What the modern compiler appears to support now

[OBSERVED] The parser recognizes `SUPER ARRAY [lo..*] OF T` and records a
`super` flag on the AST array node.

[OBSERVED] The grammar document includes:

```ebnf
super_array_type = "SUPER" "ARRAY" "[" constant ".." "*" "]" "OF" type ;
type_designator = identifier "(" constant ")" ;
```

[OBSERVED] The type checker currently resolves an AST array whose upper bound is
`None` by treating the upper bound as the lower bound, then returning an ordinary
internal `ArrayType` with concrete bounds. The internal `type_system.ArrayType`
does not currently carry a `super` flag or dynamic upper-bound metadata.

[INFERRED] Therefore, parser/docs support exists and some super-array-aware
codegen paths now work, but full IBM-style conformant-array semantics are still
not implemented in the semantic/codegen model.

### Why super arrays matter for Mandelbrot

[INFERRED] A Mandelbrot output buffer is naturally a variable-length device
buffer. The vintage dialect already has a vocabulary for this:

```pascal
TYPE
  PIXELS = SUPER ARRAY [0..*] OF INTEGER32;
```

[INFERRED] A Pascal device kernel could then be shaped as:

```pascal
PROCEDURE mandelbrot_f64(
  outp: ADS(GLOBAL) OF PIXELS;
  width: INTEGER32;
  height: INTEGER32;
  xmin: REAL;
  xmax: REAL;
  ymin: REAL;
  ymax: REAL;
  max_iter: INTEGER32
);
```

and index the output as:

```pascal
outp^[idx] := iter
```

without inventing a new `DEVICE BUFFER` syntax and without choosing a fake
compile-time maximum such as `ARRAY [0..1048575] OF INTEGER32`.

## Proposed DEVICE ABI rule for `ADS(GLOBAL) OF SUPER ARRAY`

[INFERRED] For CUDA/PTX substitution, `ADS(GLOBAL) OF SUPER ARRAY [lo..*] OF T`
should initially lower as a raw address-space pointer to `T`, not as a fat
descriptor and not with hidden upper-bound parameters. This is an ABI choice for
device kernels, not a statement that all super-array usage is unimplemented.

Proposed initial rule:

```pascal
TYPE
  PIXELS = SUPER ARRAY [0..*] OF INTEGER32;

PROCEDURE k(outp: ADS(GLOBAL) OF PIXELS; n: INTEGER32);
```

lowers externally like:

```c
void k(int *outp, int n);
```

with PTX parameters shaped like a pointer plus a 32-bit integer.

[INFERRED] Bounds should be carried explicitly by ordinary kernel parameters such
as `n`, `width`, and `height`. This matches the CUDA style and preserves the
external launch ABI.

[INFERRED] `UPPER(outp^)` should be deferred or rejected for this raw-pointer
DEVICE ABI until a bound-carrying ABI is deliberately designed. Full vintage
super-array semantics need actual upper-bound metadata; drop-in CUDA pointer ABI
does not provide it.

This is a pragmatic split:

- use IBM Pascal's super-array syntax for open buffers;
- use CUDA-compatible raw pointer ABI for externally launched device kernels;
- keep full host/conformant-array semantics as a separate implementation task.

## Candidate Pascal Mandelbrot shape

**Status: implemented.** A working DEVICE UNIT lives at
`examples/device_ptx/mandelbrot/` (`mandelbrot.inc` interface + `mandelbrot.pas`
implementation) and is exercised by
`tests/integration/test_device_mandelbrot_ptx.py`. Both `mandelbrot_f32` and
`mandelbrot_f64` are emitted, with the exact CUDA parameter ABI above, as
void-returning `.visible .entry` kernels.

The interface (note the parameter order matches the CUDA ABI table, and the f32
kernel uses the new `REAL32` type so its parameters lower to `.f32`):

```pascal
DEVICE INTERFACE;
UNIT MANDELBROT (mandelbrot_f32, mandelbrot_f64);

TYPE
  PIXELS = SUPER ARRAY [0..*] OF INTEGER32;

PROCEDURE mandelbrot_f64(
  output: ADS(GLOBAL) OF PIXELS;
  width: INTEGER32;
  height: INTEGER32;
  max_iter: INTEGER32;
  x_min: REAL64;
  x_max: REAL64;
  y_min: REAL64;
  y_max: REAL64);
{ ... and mandelbrot_f32 with REAL32 coordinate parameters ... }
END;
```

Implementation sketch (the f64 body; the f32 body is identical but typed REAL32,
and uses an integer `2` in `2 * x * y` so the doubling stays single precision):

```pascal
PROCEDURE mandelbrot_f64( {...same signature...} );
VAR
  px, py, idx, iteration: INTEGER32;
  wd, hd, x0, y0, x, y, xtemp: REAL64;
BEGIN
  px := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  py := THREADIDX_Y + BLOCKIDX_Y * BLOCKDIM_Y;
  IF (px < width) AND (py < height) THEN
  BEGIN
    IF width  > 1 THEN wd := width  - 1 ELSE wd := 1;   { (width-1) denom }
    IF height > 1 THEN hd := height - 1 ELSE hd := 1;
    x0 := x_min + (x_max - x_min) * px / wd;
    y0 := y_min + (y_max - y_min) * py / hd;
    x := 0.0; y := 0.0; iteration := 0;
    WHILE ((x * x + y * y) <= 4.0) AND (iteration < max_iter) DO
    BEGIN
      xtemp := x * x - y * y + x0;
      y := 2.0 * x * y + y0;
      x := xtemp;
      iteration := iteration + 1
    END;
    idx := py * width + px;
    output^[idx] := iteration
  END
END;
.
```

[OBSERVED] `INTEGER32` to `REAL`/`REAL32` conversion in DEVICE arithmetic works:
the coordinate calculation lowers `(float)px`-style conversions automatically
(`cvt.rn.f64.s32` / `cvt.rn.f32.s32` in PTX), so no explicit conversion syntax is
needed.

## Recommended staged implementation plan

### Stage 0: record the exact CUDA ABI

Re-open the Mandelbrot CUDA and PyCUDA sources and record:

- exact kernel signatures for `mandelbrot_f32` and `mandelbrot_f64`;
- parameter order;
- scalar widths;
- output value convention;
- launch block/grid shape;
- host-side argument packing in PyCUDA.

Deliverable: ABI table in this document or a companion artifact.

### Stage 1: prove 2-D integer indexing

Before floating point, add a small Pascal `DEVICE UNIT` kernel:

```pascal
TYPE
  PIXELS = SUPER ARRAY [0..*] OF INTEGER32;

PROCEDURE fill_linear_2d(
  outp: ADS(GLOBAL) OF PIXELS;
  width: INTEGER32;
  height: INTEGER32
);
```

Body:

```pascal
px := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
py := THREADIDX_Y + BLOCKIDX_Y * BLOCKDIM_Y;
IF (px < width) AND (py < height) THEN
BEGIN
  idx := py * width + px;
  outp^[idx] := idx
END
```

Expected PTX markers:

- `.visible .entry fill_linear_2d`
- `%tid.x`, `%tid.y`
- `%ctaid.x`, `%ctaid.y`
- `%ntid.x`, `%ntid.y`
- `st.global.u32`

External runtime check:

```text
out[y * width + x] == y * width + x
```

### Stage 2: implement/adapt super-array device-pointer support

Minimum needed for Mandelbrot:

- preserve super-array information past AST parsing;
- allow `ADS(GLOBAL) OF SUPER ARRAY [lo..*] OF T` or `ADS(GLOBAL) OF NamedSuperArray`;
- allow `outp^[idx]` indexing through such a pointer;
- lower to raw address-space pointer arithmetic, not a descriptor;
- keep host/vintage behavior unchanged unless full super-array semantics are
  intentionally implemented.

### Stage 3: prove `REAL` device arithmetic with f64 Mandelbrot [DONE]

`mandelbrot_f64` (Pascal `REAL64`/`REAL`) compiles to a void NVPTX `.visible
.entry` with the CUDA-matching ABI; real add/sub/mul/div, real comparison, the
`WHILE` loop, the `INTEGER32` loop counter, `INTEGER32`-to-`REAL` conversion, and
the global `INTEGER32` store are all present and single-module.

External launcher can either use an existing f64 path or make small changes to
select `mandelbrot_f64` and pass `np.float64` values.

### Stage 4: add `REAL32` for true f32 substitution [DONE]

`REAL32` (LLVM `float`) and `REAL64` (a 64-bit synonym for `REAL`) now exist.
In DEVICE code they are always available; in host code they are gated behind the
`wide-reals` feature flag (parallel to `wide-integers`). Implemented:

- type registration and type checking (`Real32Type`; `REAL64` resolves to the
  existing `REAL` singleton);
- LLVM lowering of `REAL32` to `float`, `REAL64`/`REAL` to `double`;
- real constants and coercions: integer-family widens into `REAL32`; `REAL32`
  widens into `REAL` (C-like `float op double -> double`); a real literal adopts
  a `REAL32` context (the `f`-suffix analog) so single-precision kernels stay in
  f32; `REAL -> REAL32` is **not** implicit (no silent narrowing);
- arithmetic and comparisons, including float/float division staying in float;
- `REAL32` is writable (WRITE widens it to double for output);
- PTX parameter ABI proven `.f32` for `mandelbrot_f32` and the kernel body proven
  single-precision (no `.f64` ops leak in).

`mandelbrot_f32` is therefore labelled correctly: its floating parameters are
genuinely 32-bit, so there is no ABI mismatch with the existing PyCUDA call site.

### Stage 5: no-change substitution attempt

Once ABI-compatible f32 or f64 PTX exists:

1. Generate `mandelbrot.ptx` from Pascal.
2. Ensure the exported entry name matches the launcher.
3. Ensure parameter order and widths match the CUDA source.
4. Run the existing PyCUDA script with no changes, or with the smallest recorded
   change.
5. Compare image/output array against the CUDA-generated kernel for the same
   launch and parameters.

## Validation ladder

Use increasingly strong evidence:

1. LLVM IR parses.
2. NVPTX backend emits PTX.
3. PTX contains `.visible .entry mandelbrot_*`.
4. PTX contains expected special-register reads and global stores.
5. `ptxas` accepts the PTX for the target SM architecture.
6. PyCUDA/CUDA Driver API loads the module and resolves the symbol.
7. Kernel launch completes without driver errors.
8. Copied-back output matches a reference implementation.

## Current key gaps

| Gap | Basis | Notes |
| --- | --- | --- |
| Exact Mandelbrot ABI table | OBSERVED | Re-read from `mandelbrot.cu` / `mandelbrot_cuda.py`; table recorded above. |
| Kernel entries return void | OBSERVED | Fixed: exported device entries lower to `ptx_kernel void`, no `func_retval`. |
| Super-array semantic/codegen model | OBSERVED/INFERRED | `ADS(GLOBAL) OF SUPER ARRAY` indexing works for the device pointer ABI; full dynamic-bound metadata / conformant-array semantics still pending. |
| Raw `ADS(GLOBAL) OF SUPER ARRAY` pointer ABI | OBSERVED | Lowers to a raw `.u64 .ptr .global`; proven by the mandelbrot example. |
| 2-D buffer-store artifact test | OBSERVED | `tests/integration/test_device_mandelbrot_ptx.py` asserts 2-D indexing + `st.global.u32`. |
| DEVICE `REAL`/`REAL32` arithmetic | OBSERVED | Both kernels compile; f32 proven single-precision, f64 double. |
| `INTEGER32` to `REAL`/`REAL32` conversion | OBSERVED | Lowers automatically (`cvt.rn.f{32,64}.s32`). |
| `REAL32` | OBSERVED | Implemented (LLVM `float`); `mandelbrot_f32` params are `.f32`. |
| External runtime execution | UNVERIFIED | Requires NVIDIA driver/device outside current VM (the `@requires_gpu` rung). |

## Recommendation

[OBSERVED] Super arrays are the source-level spelling for open device buffers, and
`ADS(GLOBAL) OF SUPER ARRAY` in DEVICE kernel parameters lowers as a raw pointer,
preserving CUDA/PyCUDA ABI compatibility. The remaining gap is metadata-aware
conformant-array behavior (`UPPER` on a super-array parameter), not basic
super-array support.

[OBSERVED] Both `mandelbrot_f64` (Pascal `REAL64`) and `mandelbrot_f32` (Pascal
`REAL32`) now emit ABI-correct, void-returning PTX entries from the example at
`examples/device_ptx/mandelbrot/`.

[PRESCRIBED] The remaining work is the last rung of the validation ladder:
`ptxas`-accept the emitted `mandelbrot.ptx`, load it with the existing PyCUDA
launcher, run both kernels on a real device, and diff the output against the
CUDA-built reference. This needs an NVIDIA driver/device (the §8 container in
`cuda-kernel-prescription.md`) and is out of reach in the current VM. The Pascal
side of the substitution is complete.
