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

[OBSERVED] Prior inspection of `~/dixie-scratch-area/mandelbrot/mandelbrot.cu`
found CUDA C kernels named:

```c
extern "C" __global__ void mandelbrot_f32(...)
extern "C" __global__ void mandelbrot_f64(...)
```

[OBSERVED] Prior inspection of `mandelbrot_cuda.py` found the PyCUDA pattern:

```python
mod = cuda.module_from_file("mandelbrot.ptx")
kernel = mod.get_function("mandelbrot_f32")
kernel(..., block=(16, 16, 1), grid=(blocks_x, blocks_y, 1))
```

[OBSERVED] The CUDA kernels use two-dimensional CUDA indexing via `threadIdx.x/y`,
`blockIdx.x/y`, and `blockDim.x/y`, write to an `int*` output buffer, and use
width/height bounds checks.

[UNVERIFIED] The exact current CUDA kernel parameter order and scalar types should
be re-read from `mandelbrot.cu` immediately before implementing a Pascal
replacement. The likely shape is an output pointer, image dimensions, coordinate
bounds, and `max_iter`, but the exact ABI must be copied from the source, not
from memory.

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

[INFERRED] Symbol matching is likely low risk because exported Pascal device-unit
procedures already lower to visible PTX entries using the exported procedure name.

[INFERRED] Scalar-width matching is the major risk for `mandelbrot_f32`, because
Pascal currently has `REAL` as a double-precision type, while true f32 ABI parity
requires a 32-bit floating type such as `REAL32`.

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

Target double-precision first:

```pascal
TYPE
  PIXELS = SUPER ARRAY [0..*] OF INTEGER32;

DEVICE INTERFACE;
UNIT MANDELBROT (mandelbrot_f64);

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

END;
```

Implementation sketch:

```pascal
DEVICE IMPLEMENTATION OF MANDELBROT;

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
VAR
  px, py, idx: INTEGER32;
  iter: INTEGER32;
  x0, y0, x, y, xtemp, dx, dy: REAL;
BEGIN
  px := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  py := THREADIDX_Y + BLOCKIDX_Y * BLOCKDIM_Y;

  IF (px < width) AND (py < height) THEN
  BEGIN
    idx := py * width + px;

    dx := (xmax - xmin) / width;
    dy := (ymax - ymin) / height;

    x0 := xmin + px * dx;
    y0 := ymin + py * dy;

    x := 0.0;
    y := 0.0;
    iter := 0;

    WHILE ((x * x + y * y) <= 4.0) AND (iter < max_iter) DO
    BEGIN
      xtemp := x * x - y * y + x0;
      y := 2.0 * x * y + y0;
      x := xtemp;
      iter := iter + 1
    END;

    outp^[idx] := iter
  END
END;
.
```

[UNVERIFIED] This sketch may need syntax/coercion adjustments. In particular,
`INTEGER32` to `REAL` conversion in DEVICE code must be tested, and explicit
conversion syntax may be needed if mixed arithmetic does not currently lower.

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

### Stage 3: prove `REAL` device arithmetic with f64 Mandelbrot

Target `mandelbrot_f64` first, using Pascal `REAL`.

Required artifact tests:

- `REAL` parameters in a PTX kernel entry;
- real add/sub/mul/div;
- real comparison;
- `WHILE` loop;
- `INTEGER32` loop counter;
- `INTEGER32` to `REAL` conversion or explicit conversion;
- global `INTEGER32` store.

External launcher can either use an existing f64 path or make small changes to
select `mandelbrot_f64` and pass `np.float64` values.

### Stage 4: add `REAL32` for true f32 substitution

True no-change replacement for `mandelbrot_f32` likely requires a Pascal
32-bit-float type:

```pascal
REAL32
```

Likely work:

- type registration and type checking;
- LLVM lowering to `float`;
- real constants/coercions involving `REAL32`;
- arithmetic and comparisons;
- PTX parameter ABI tests proving `.f32`-style behavior;
- conversion rules between `INTEGER32`, `REAL32`, and `REAL`.

Until this exists, do not label a Pascal kernel `mandelbrot_f32` if its floating
parameters are actually double-width. That would create an ABI mismatch with the
existing PyCUDA call site.

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
| Exact Mandelbrot ABI table | UNVERIFIED | Re-read CUDA/PyCUDA source before implementation. |
| Super-array semantic/codegen model | OBSERVED/INFERRED | Parser/docs exist; long-form `NEW` for one-dimensional super-array pointers and string-bound intrinsics now work, but full dynamic-bound metadata / conformant-array semantics are still pending. |
| Raw `ADS(GLOBAL) OF SUPER ARRAY` pointer ABI | INFERRED | Proposed for CUDA compatibility; not currently proven. |
| 2-D buffer-store artifact test | INFERRED | Builtins exist; dedicated test still needed. |
| DEVICE `REAL` arithmetic audit | UNVERIFIED | Host `REAL` exists; Mandelbrot-class device path needs tests. |
| `INTEGER32` to `REAL` conversion | UNVERIFIED | Needed for coordinate calculation. |
| `REAL32` | OBSERVED/INFERRED | Needed for true `mandelbrot_f32` ABI parity; not established now. |
| External runtime execution | UNVERIFIED | Requires NVIDIA driver/device outside current VM. |

## Recommendation

[INFERRED] Use super arrays as the source-level spelling for open device buffers,
but initially lower `ADS(GLOBAL) OF SUPER ARRAY` in DEVICE kernel parameters as a
raw pointer to preserve CUDA/PyCUDA ABI compatibility. The remaining gap is
metadata-aware conformant-array behavior, not basic super-array support.

[INFERRED] Target `mandelbrot_f64` first, because Pascal `REAL` already maps to a
double-precision concept and the CUDA example has an f64 kernel. This avoids
forcing `REAL32` design before the rest of the PTX substitution path is proven.

[INFERRED] After f64 runtime proof, add `REAL32` and pursue true no-change
`mandelbrot_f32` substitution.
