# Mandelbrot GPU port (archived)

The Pascal-side PTX-substitution plan for the `mandelbrot-gpu` port (goal met)
plus the accompanying gaps/status snapshot. Concatenated: the substitution plan
(the design and build record), then the gaps note (resolved blockers and
remaining ergonomics).

## Mandelbrot PTX substitution plan

> **ARCHIVED (2026-06-21) — goal met.** The Pascal-side PTX-substitution goal of
> this document is complete: both `mandelbrot_f32` and `mandelbrot_f64` emit
> ABI-correct, void-returning PTX entries from `examples/device_ptx/mandelbrot/`,
> and the generated `mandelbrot.ptx` has run on a real NVIDIA GPU as a no-change
> drop-in for the `nvcc`-built artifact. The substitution path is covered by
> `tests/integration/test_device_mandelbrot_ptx.py`. One item this plan listed as
> an open below-ABI-line difference — the phantom `.extern .global input/output`
> leaks — has since been **resolved** (was `followups.md` item 2; now archived in
> `docs/old/old-followups.md`). The remaining below-ABI-line codegen-quality polish
> (predication, FMA fusion, tighter pointer alignment) is tracked as an open item
> in `docs/followups.md`. Pascal-side host orchestration remains Milestone D in
> `docs/cuda-kernel-prescription.md`.

### Goal

Generate a PTX file from Pascal `DEVICE` source that can substitute for the
Mandelbrot PTX artifact currently produced from CUDA C, ideally with no changes
to the external launch site. Small launcher changes are acceptable for early
proofs, especially while validating the Pascal PTX path before adding Pascal
host-side CUDA orchestration.

This document is a planning artifact. It separates observed facts from inferred
engineering direction.

### Non-goals

- Pascal-generated CUDA/PyCUDA host orchestration.
- CUDA runtime or driver bindings in Pascal.
- Performance parity claims.
- Full CUDA source compatibility.
- Final scalar-width policy for the whole compiler.

The first success criterion is an inspectable and externally launchable PTX
kernel artifact with the right symbol, parameter ABI, indexing behavior, and
output memory layout.

### Evidence grades

Claims are tagged as:

- `[OBSERVED]` directly observed in repository code, tests, docs, or the vintage
  manual text.
- `[OBSERVED — hardware run]` observed by a maintainer running the artifact on a
  real NVIDIA GPU, outside the repo's CI (which has no device). The strongest
  grade for the launch/output rungs.
- `[INFERRED]` a reasonable engineering conclusion from observed facts.
- `[UNVERIFIED]` plausible but not yet checked in the current codebase or on a
  CUDA-capable machine.

### Observed current PTX capability

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

### Observed Mandelbrot external contract

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

### What no-change substitution requires

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

### Super arrays and the output-buffer problem

#### What the vintage manual says

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

#### What the modern compiler appears to support now

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

#### Why super arrays matter for Mandelbrot

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

### Proposed DEVICE ABI rule for `ADS(GLOBAL) OF SUPER ARRAY`

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

### Candidate Pascal Mandelbrot shape

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

### Recommended staged implementation plan

#### Stage 0: record the exact CUDA ABI

Re-open the Mandelbrot CUDA and PyCUDA sources and record:

- exact kernel signatures for `mandelbrot_f32` and `mandelbrot_f64`;
- parameter order;
- scalar widths;
- output value convention;
- launch block/grid shape;
- host-side argument packing in PyCUDA.

Deliverable: ABI table in this document or a companion artifact.

#### Stage 1: prove 2-D integer indexing

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

#### Stage 2: implement/adapt super-array device-pointer support

Minimum needed for Mandelbrot:

- preserve super-array information past AST parsing;
- allow `ADS(GLOBAL) OF SUPER ARRAY [lo..*] OF T` or `ADS(GLOBAL) OF NamedSuperArray`;
- allow `outp^[idx]` indexing through such a pointer;
- lower to raw address-space pointer arithmetic, not a descriptor;
- keep host/vintage behavior unchanged unless full super-array semantics are
  intentionally implemented.

#### Stage 3: prove `REAL` device arithmetic with f64 Mandelbrot [DONE]

`mandelbrot_f64` (Pascal `REAL64`/`REAL`) compiles to a void NVPTX `.visible
.entry` with the CUDA-matching ABI; real add/sub/mul/div, real comparison, the
`WHILE` loop, the `INTEGER32` loop counter, `INTEGER32`-to-`REAL` conversion, and
the global `INTEGER32` store are all present and single-module.

External launcher can either use an existing f64 path or make small changes to
select `mandelbrot_f64` and pass `np.float64` values.

#### Stage 4: add `REAL32` for true f32 substitution [DONE]

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

#### Stage 5: no-change substitution attempt

Once ABI-compatible f32 or f64 PTX exists:

1. Generate `mandelbrot.ptx` from Pascal.
2. Ensure the exported entry name matches the launcher.
3. Ensure parameter order and widths match the CUDA source.
4. Run the existing PyCUDA script with no changes, or with the smallest recorded
   change.
5. Compare image/output array against the CUDA-generated kernel for the same
   launch and parameters.

### Validation ladder

Use increasingly strong evidence. All eight rungs are now satisfied — the first
four in repository CI, and rungs 5-8 by a maintainer hardware run (NVIDIA GPU,
CUDA 12.x; outside the repo's CI, which has no device):

1. LLVM IR parses. **[OBSERVED]**
2. NVPTX backend emits PTX. **[OBSERVED]**
3. PTX contains `.visible .entry mandelbrot_*`. **[OBSERVED]**
4. PTX contains expected special-register reads and global stores. **[OBSERVED]**
5. `ptxas` accepts the PTX for the target SM architecture. **[OBSERVED — hardware run]**
6. PyCUDA/CUDA Driver API loads the module and resolves the symbol. **[OBSERVED — hardware run]**
7. Kernel launch completes without driver errors. **[OBSERVED — hardware run]**
8. Copied-back output matches a reference implementation. **[OBSERVED — hardware run]**

Rungs 1-4 are exercised by `tests/integration/test_device_mandelbrot_ptx.py`.
Rungs 5-8 were satisfied by generating `mandelbrot.ptx` from the Pascal example,
dropping it into the companion mandelbrot-gpu PyCUDA launcher unchanged, and
producing a correct Mandelbrot image on a real GPU. See "Hardware validation
result" below.

### Hardware validation result

[OBSERVED — hardware run, 2026-06] The Pascal-generated `mandelbrot.ptx`
substituted for the `nvcc`-generated artifact with **no launcher change**: the
existing `module_from_file(...).get_function("mandelbrot_f32" | "mandelbrot_f64")`
plus positional launch resolved and ran both kernels, and the copied-back image
matched the reference render.

A `diff` of the two PTX files (`nvcc` 12.8 vs this toolchain) was reviewed. The
key finding: **there is no ABI, symbol, parameter, layout, or semantic difference.**
The entry signatures, parameter order, parameter widths, and the `.f32`/`.f64`
split are identical. Everything that differs is below the ABI line — the two
backends scheduling the same computation differently — which is exactly the
latitude a PTX consumer allows. Observed differences, all benign:

- **Header provenance.** `nvcc` stamps `.version 8.7` / NVVM banner; this
  toolchain stamps `.version 7.1` / LLVM NVPTX banner. The older ISA level is
  accepted by `ptxas` and the driver JIT and is, if anything, more portable.
- **Pointer-parameter annotation.** This toolchain emits
  `.param .u64 .ptr .global .align 1` where `nvcc` emits a bare `.param .u64`.
  The annotated form is a strict superset (it tells `ptxas` the pointer targets
  global memory). `.align 1` is conservative — `nvcc` would say `.align 4`,
  knowing the buffer is `int`-aligned — so this is a missed alignment hint, not a
  defect.
- **Bounds-guard lowering (codegen quality).** `nvcc` hoists the
  `(width > 1) ? (width-1) : 1.0` guard into a branchless `selp.f32`; this
  toolchain lowers the source `IF` into real control flow (`bra`). Both are
  correct; predication is the preferred GPU idiom because it avoids warp
  divergence at the image edges. A peephole/lowering improvement, not a
  correctness issue.
- **FMA fusion (precision).** `nvcc` fuses `2*x*y + y0` into a single
  `fma.rn.f32`; this toolchain emits a discrete multiply/add. An FMA carries more
  intermediate precision, so the two kernels can differ in the last bit. The
  image matched anyway, which means the escape-iteration counts were robust to
  that difference for this render.
- **Predicate shape.** `nvcc` uses `and.pred` of two `setp`s for the loop
  continue test; this toolchain uses `or.pred` with an early-out branch — the
  same control flow by De Morgan, mirrored.
- **Two phantom globals.** This toolchain emits
  `.extern .global .align 8 .b64 input;` and `... output;` — unreferenced
  module-level globals leaked from the device compiland (a remnant of
  INPUT/OUTPUT handling). `.extern` with no use generates no SASS and resolved to
  nothing at launch; harmless, but worth cleaning up (tracked in
  `docs/followups.md`).

Net: a correct, ABI-faithful, hardware-validated drop-in. The remaining gap
between this and `nvcc`'s output is codegen *quality* (predication, FMA fusion,
alignment hints) and one cosmetic wart (the phantom globals) — none of it
correctness, ABI, or layout.

### Current key gaps

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
| External runtime execution | OBSERVED — hardware run | Both kernels ran on a real NVIDIA GPU via the unchanged PyCUDA launcher; output matched the reference. The repo's own CI still has no device. |
| Codegen quality vs `nvcc` | OBSERVED | Below-ABI-line differences only: branch-vs-predication on the bounds guard, discrete multiply/add vs `fma.rn`, conservative `.align 1`, and two phantom `.extern .global` leaks. Tracked in `docs/followups.md`; none affect correctness. |

### Recommendation

[OBSERVED] Super arrays are the source-level spelling for open device buffers, and
`ADS(GLOBAL) OF SUPER ARRAY` in DEVICE kernel parameters lowers as a raw pointer,
preserving CUDA/PyCUDA ABI compatibility. The remaining gap is metadata-aware
conformant-array behavior (`UPPER` on a super-array parameter), not basic
super-array support.

[OBSERVED — hardware run] Both `mandelbrot_f64` (Pascal `REAL64`) and
`mandelbrot_f32` (Pascal `REAL32`) emit ABI-correct, void-returning PTX entries
from the example at `examples/device_ptx/mandelbrot/`, and the generated
`mandelbrot.ptx` has been run on a real NVIDIA GPU as a no-change drop-in for the
`nvcc`-built artifact, producing a correct image. The Pascal-side PTX-substitution
goal of this document is **met**.

[PRESCRIBED] What remains is optional codegen-quality polish to close the
below-ABI-line gap with `nvcc` (see "Hardware validation result" and
`docs/followups.md`): predicating the bounds guard instead of branching, fusing
the `2*x*y + y0` step into an `fma`, emitting a tighter pointer-parameter
alignment hint, and dropping the phantom `.extern .global input/output` leaks.
None of these affect correctness, ABI, or layout; they separate "runs correctly"
from "indistinguishable from `nvcc`'s output."

Note this is the *external-launcher* substitution path (a Pascal-generated `.ptx`
loaded by an existing PyCUDA host). Pascal-side host orchestration —
`DEVALLOC`/`DEVCOPYTO`/`LAUNCH` — remains a separate, still-prescribed milestone
(see `docs/cuda-kernel-prescription.md` §5, Milestone D).

## Gaps for a full Pascal port of `mandelbrot-gpu`

> **Status (current).** Both items that were blocking or endangering the port —
> `SIZEOF` of named types, and command-line argument handling — have been
> **fixed in-tree** and are no longer gaps. This revision records what changed,
> verified against the actual tree and against C, and re-scopes what remains
> (ergonomics, not blockers). PTX/NVPTX toolchain skew remains a non-concern by
> direction. Earlier revisions of this document treated `SIZEOF` and the command
> line as open problems; that is now out of date.

### Context

Two repositories are in play:

- `mandelbrot-gpu`: the existing Python GPU Mandelbrot renderer (CLI, view/precision
  selection, color mapping, PNG output; CUDA path loads `mandelbrot.ptx` via PyCUDA and
  launches `mandelbrot_f32`/`mandelbrot_f64`).
- `pascal-1981`: the Pascal compiler/runtime, with host-side C-ABI FFI (Phases 0–4), a
  device-PTX path, host-side CUDA orchestration builtins (`DEVALLOC`, `DEVCOPYTO`,
  `LAUNCH`, …), correct record `SIZEOF`, and vintage command-line argument binding.

The goal is to add a Pascal implementation beside the Python one — a Pascal host program
plus Pascal device kernels — with the PTX artifact interchangeable with the CUDA/PTX the
Python code uses.

---

### Recently resolved

#### `SIZEOF` of named types — FIXED

**Symptom (was):** `SIZEOF` of a variable or type whose name was a user `TYPE` returned the
4-byte fallback — `SIZEOF(record)` → 4, and likewise named arrays, named wide-int aliases,
and the C alias `CLONG` (→ 4 instead of 8). Anonymous/inline aggregates sized correctly,
which is why the bug looked like "records only" at first.

**Root cause:** `get_type_size` (`codegen/types_map.py`) never resolved a `NamedType`
before sizing it; the branch fell through to `_scalar_size`, whose default is 4. A
secondary issue: the record arm summed field bytes with no alignment/tail padding.

**Fix (`sizeof-named-types.patch`):**

- `get_type_size` now resolves named aliases first (`resolve_type_alias`), so a variable or
  type spelled with a user `TYPE` name or a C alias is sized by its definition. This alone
  fixes records, named arrays, named wide-int aliases, and `CLONG`.
- Records are now sized through the **same layout helper the C-ABI marshaller uses**
  (`c_abi._size_of` on the record's LLVM type), so the result includes field alignment and
  tail padding and matches both the actual allocation and C's `sizeof`.
- The `SIZEOF(typename)` path was unified through `get_type_size`, so the type-name form
  resolves aliases too (previously it independently hit `_scalar_size`).

**Verified** against C `sizeof` on the same shapes:

| Pascal | `SIZEOF` | C `sizeof` |
|---|---:|---:|
| `rec = RECORD a,b: INTEGER32; c: ARRAY[0..63] OF CHAR END` (var and type) | 72 | 72 |
| `RECORD x: CHAR; y: INTEGER32 END` (padded) | 8 | 8 |
| `RECORD h: rec; n: INTEGER32 END` (nested) | 76 | 76 |
| named `ARRAY[0..9] OF CHAR` | 10 | 10 |
| named `INTEGER64`, and `CLONG` | 8 | 8 |
| scalars/pointer/inline aggregates (regression guard) | 2/2/1/8/1/8/8/10 | — |

Coverage: `tests/test_sizeof.py`.

#### Command-line arguments — IMPLEMENTED

**Was:** `main` had no `argc`/`argv`; program-heading parameters were ignored
(`./prog 42` left the parameter zero). The document had framed this as "no
`PARAMSTR`/`PARAMCOUNT`," which is the wrong (Turbo Pascal) model.

**Now (`cmdline-program-parameters.patch`):** the faithful vintage model is implemented
(IBM manual 13-5…13-7). `main` is `i32 @main(i32 %argc, i8** %argv)`, and each
heading parameter other than `INPUT`/`OUTPUT` is populated, in order, from the command line,
prompting at the keyboard when an argument is absent.

```pascal
PROGRAM mandel(view, scale, tag);
VAR view: INTEGER; scale: REAL; tag: LSTRING(32);
...
```
```
$ mandel 3 0.75 zoomA      { view:=3, scale:=0.75, tag:='zoomA' }
$ mandel 8                 { view:=8; scale and tag are prompted }
```

Supported parameter types: everything `READ` accepts (`INTEGER`/`WORD`/`REAL`/`CHAR`/
`BOOLEAN`/enumerated/subrange/`STRING`/`LSTRING`) plus `FILE` types, where the token is the
filename and a later `RESET`/`REWRITE` opens it. Parsing reuses the ordinary `READ`
machinery (via a per-parameter `stdin` redirect), so command-line and interactive parsing
are identical. Programs that take no command-line input are unaffected. Details and limits
are in `docs/command-line-support.md`; coverage in `tests/test_cmdline.py`.

---

### Remaining gaps (ergonomics, not blockers)

#### 1. C-string ergonomics are still a little rough

Passing a Pascal value as a C `char*` still means building a NUL-terminated buffer by hand
(`ARRAY[..] OF CHAR`, fill, append `CHR(0)`, pass `ADR`). `ADR` is the right primitive; the
friction is the manual NUL packing.

This is now smaller than it was, for two reasons. First, the command-line work means an
output filename can arrive as a proper `LSTRING`/`STRING` (or a `TEXT` file parameter)
rather than a hand-filled char array. Second, a thin, well-defined `LSTRING`/`STRING` →
`char*` bridge (a small runtime helper, or a documented `ADR` + explicit-NUL convention)
would cover the remaining libpng/`fopen`-style needs. **Priority: medium.** Doesn't prevent
the port; a small bridge removes most of the ugliness.

#### 2. No header-import tooling; C declarations are translated by hand

For libpng even the simplified API needs manual transcription of constants, record fields,
and Pascal type aliases (`CINT`, `ADRMEM`, `INTEGER32`, …), checked against the host ABI.
Manageable for a small subset, easy to get subtly wrong. The `SIZEOF` fix **de-risks** this:
a hand-mistranslated struct can now be cross-checked because `SIZEOF` finally agrees with
the C layout. **Priority: medium.** Not required to finish the port; it raises risk.

---

### Resolved / not a blocker

- **`SIZEOF(record)`** — fixed (see above), and now padding-accurate / C-matching.
- **Command-line arguments** — implemented via the vintage program-parameter model.
- **PTX toolchain skew** — resolved by direction. The kernel ABI is a drop-in match
  (kernel names, argument order, scalar widths, entry-point form); version/producer/
  instruction-selection differences are accepted. Action item is negative: **do not disturb
  the PTX/NVPTX path** while doing host-side work.
- **libpng** — works from Pascal via the simplified API (extended dialect).
- **Host-side C FFI in general** — alive; re-confirmed on this tree.
- **Kernel symbol/parameter compatibility with the Python launcher** — matches; the existing
  Mandelbrot device example is shaped for exactly this.
- **CUDA orchestration surface** — `DEVALLOC`/`DEVCOPYTO`/`LAUNCH`/free already exist, so the
  port need not rebuild the PyCUDA launch path. (And `DEVCOPYTO`-style byte counts can now
  trust `SIZEOF`.)

---

### Recommended order of attack

The two blockers are cleared, so the path is now straightforward:

1. **Build the first Pascal host renderer around the libpng simplified API.** Hardcode one
   or a few views to start; take an output filename as a command-line parameter (now
   supported) — an `LSTRING`/`STRING` parameter, or a thin `char*` bridge for libpng.
2. **Reuse the existing Pascal Mandelbrot device example as the kernel base.** It already
   matches the Python CUDA contract; leave the PTX/NVPTX path untouched.
3. **Add the small `LSTRING`/`STRING` → `char*` bridge** (remaining gap 1) when the manual
   NUL packing starts to bite.
4. **Grow CLI coverage** using the program-parameter mechanism (positional, vintage-faithful)
   — view selector as `INTEGER`/enumerated, precision as `CHAR`/enumerated, output name as
   `LSTRING`/`STRING`/file. A flag-style parser, if ever wanted, is a separate convenience
   layer on top of the now-available `argc`/`argv`.
5. **Translate C headers as needed** (remaining gap 2), cross-checking struct layouts with
   the now-correct `SIZEOF`.

---

### Bottom line

The two scary items are done. `SIZEOF` is correct for named records (and named arrays, wide
aliases, and `CLONG`), padding-accurate, and matches C; command-line arguments work the
faithful vintage way, with a keyboard-prompt fallback. PTX is a drop-in match and should be
left alone.

What's left is ergonomics, not feasibility: a small `char*` bridge to make libpng/`fopen`
calls less manual, and (optionally) some discipline or tooling around hand-translated C
headers — now safer because `SIZEOF` can cross-check layouts. The recommended next move is
to build a minimal end-to-end Pascal renderer: simplified libpng output, an output-filename
command-line parameter, and the existing Mandelbrot PTX kernel. The rest is icework.

