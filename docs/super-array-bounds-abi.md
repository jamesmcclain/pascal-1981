# Heap super-array dynamic-bound ABI

Reference for the runtime representation of one-dimensional heap super arrays.
Historical evidence for the D-001/D-002 remediation and the pre-fix problem
statement, together with forward-looking design guidance for any future
multi-dimensional or parameter-carrying extension, are archived in
`docs/old/super-array-remediation-context.md`.

Claims below are graded per the repository's anti-confabulation discipline:
OBSERVED (checkable against this repository's code/tests or a recorded
differential run), DOCUMENTED (stated in the IBM Pascal 2.0 manual), INFERRED
(deduction; not directly evidenced).

## The representation

A heap super-array block is laid out as:

```
malloc block:   [ i64 upper_bound ][ element data ... ]
                ^                  ^
                header (8 bytes)   p points here
```

- `NEW(p, u)` computes `count = u - low + 1`, allocates
  `8 + count * sizeof(element)` bytes, stores `u` (sign-extended to i64) in
  the header, and stores the address of the element data — 8 bytes past the
  start of the block — into `p`. Indexing and dereference are therefore
  unchanged: `p` looks exactly like a pointer to a fixed array. [OBSERVED —
  `codegen/runtime_builtins.py::builtin_new`]
- `UPPER(p^)` loads the i64 immediately before the data pointer and narrows
  it to the intrinsic's integer result. `LOWER(p^)` is the static declared
  lower bound; no memory access. [OBSERVED — `codegen/exprs.py`]
- `DISPOSE(p)` on a super-array pointer frees `p - 8` (the true allocation
  start), not `p`. Plain pointees keep the old `free(p)` lowering.
  [OBSERVED — `codegen/runtime_builtins.py::builtin_dispose`; the ASan-linked
  smoke run of NEW/write/DISPOSE completes cleanly]
- `$INDEXCK` on `p^[i]`, when the designator step just dereferenced a plain
  `^SUPER ARRAY` pointer variable, checks `i >= low` (static) and
  `i <= header` (dynamic). Super arrays no longer participate in the static
  `(low, high)` check path at all, since they have no static high bound.
  [OBSERVED — `codegen/types_map.py`]

The header is a single i64 regardless of element type. `malloc` alignment on
the supported x86-64 host target is at least 8, so element data past the
header stays suitably aligned for every element type this compiler emits.
[INFERRED from the C standard's malloc alignment guarantee; not separately
tested]

## Soundness boundary of the dynamic index check

The dynamic `UPPER`/`INDEXCK` header read is emitted only for values reached
through a **plain `POINTER`-flavor** `^SUPER ARRAY` variable. Under this
compiler's rules such values originate only from long-form `NEW` (short-form
`NEW` is rejected for super-array referents, and `ADR`/`ADS` produce
distinct pointer flavors), so the header is always present. [INFERRED — this
is an invariant of the current front end, not a checked property; `RETYPE`
could in principle forge such a pointer, in which case the header read is
garbage, matching the general "RETYPE means you asserted the layout"
posture.]

Not covered, deliberately (unchanged scope from the follow-up item):

- `UPPER`/`LOWER` on super-array **parameters** or other non-heap super-array
  storage: still resolved statically or rejected; no hidden bound word is
  passed with parameters, so the call ABI is unchanged. [OBSERVED]
- Multi-dimensional super arrays and variant-record long-form `NEW`: still
  rejected; per the archived remediation plan, expansion beyond the
  one-dimensional subset needs new differential probes against the genuine
  1981 compiler first. [DOCUMENTED in the archived follow-up; the manual's
  full super-array surface is wider than what is shipped]

## Device-code boundary (drop-in PTX discipline preserved)

Device code keeps heap allocation rescinded: `NEW`/`DISPOSE` remain
type-check errors in `DEVICE` modules, so no bound header can ever exist in
device memory. Consequently `UPPER(p^)` on a super array is **rejected during
type checking in device code** with a diagnostic directing the programmer to
explicit bound parameters. [OBSERVED —
`tests/test_super_array_bounds.py::test_device_module_rejects_super_array_upper_deref`]

This is exactly the split recorded in
`docs/old/mandelbrot-ptx-substitution-plan.md`: kernel buffers use super-array
syntax for the open-buffer *type*, while bounds travel as ordinary kernel
parameters (`n`, `width`, `height`), preserving the drop-in CUDA pointer ABI
for generated `.ptx` artifacts. [DOCUMENTED in that plan] No device codegen
path was touched by this change; the committed `fill_indices` PTX artifact
regenerates byte-identical before and after. [OBSERVED — diff of
`compile_to_ptx` output on the pre-change and post-change trees]

Kernel entries therefore carry no hidden bound word, no changed parameter
layout, and no new metadata: a `.ptx` file produced by this compiler remains
a no-change drop-in wherever it was one before.
