# Follow-ups / tracked tech-debt

A home for known, non-blocking issues we have consciously decided to defer, so
they are not lost. Each item states what it is, where it lives, why it matters,
a suggested resolution, and how to verify the fix. Status is one of OPEN /
IN-PROGRESS / DONE.

These are not bugs that produce wrong output today; they are seams worth
closing when the surrounding code is next touched. Items 1 and 2 were surfaced
while reviewing the lazy-extern / `INPUT`/`OUTPUT`-ownership work (checklist
S2.2.1 full form + S4.1).


---

## 1. Super-array remediation residue and device-heap boundary [OPEN]

**Where.** The D-001/D-002 historical evidence now lives in
`docs/old/discrepancies-super-array.md` and
`docs/old/discrepancies-remediation-plan.md`. Current implementation touchpoints
are `type_checker.py::_check_new_args`, `codegen/runtime_builtins.py::builtin_new`,
string-bound lowering in expression codegen, and DEVICE recission checks in the
type checker.

**What.** The observed D-001/D-002 gaps are remediated for normal host code:
`LOWER`/`UPPER` accept `STRING(n)` and `LSTRING(n)`, and one-dimensional
`SUPER ARRAY` pointer referents accept long-form `NEW(p, upper_bound)`. DEVICE
code intentionally does **not** support heap allocation; `NEW` and `DISPOSE` are
rejected during type checking with a device-code dynamic-allocation diagnostic,
including long-form `NEW(p, upper_bound)`.

**Why it matters.** The shipped support is deliberately narrower than the full
vintage surface. Long-form `NEW` currently covers the one-dimensional
super-array allocation case needed by D-002; it does not imply variant-record
long-form `NEW`, multi-dimensional super-array heap allocation, or GPU/device
heap allocation. Also, allocation sizing for heap super arrays does not yet
establish a general ABI for preserving dynamic upper-bound metadata for later
`UPPER(p^)`-style queries.

**Suggested resolution.** If future work expands super arrays, decide the runtime
representation first: how dynamic bounds are stored, how dereferenced super-array
bounds are recovered, and how kernel buffer bounds are passed. For DEVICE code,
prefer caller-provided buffers and explicit bound metadata over backend-specific
GPU allocator calls unless a real device heap design is approved.

**How to verify.** Keep the existing regression tests for normal-code
`LOWER`/`UPPER`, long-form super-array `NEW`, DEVICE string bounds, and DEVICE
heap recission. Add new differential probes before expanding beyond the current
subset, especially for multi-dimensional super arrays, `UPPER(p^)`, and any
variant-record long-form `NEW` behavior.

---

## 2. Phantom `.extern .global input/output` in device PTX [OPEN]

**Where.** Device PTX emission for `DEVICE UNIT` compilands; the INPUT/OUTPUT
single-definition handling (`codegen/base.py`, S4.1) and the lazy-extern path.

**What.** The generated `mandelbrot.ptx` carries two unreferenced module-level
globals — `.extern .global .align 8 .b64 input;` and `... output;` — that no
kernel uses. They are a leak of the host INPUT/OUTPUT stream globals into a device
compiland that has no host I/O.

**Why it matters.** Harmless at runtime: an `.extern` with no use generates no
SASS and resolved to nothing during the hardware launch (the kernel ran correctly
with them present). But they are confusing in a device artifact — a reader of a
Mandelbrot kernel rightly wonders what `input`/`output` are — and they are the one
purely cosmetic difference from the `nvcc` output noted in the PTX diff
(`docs/mandelbrot-ptx-substitution-plan.md`, "Hardware validation result").

**Suggested resolution.** Suppress emission of the INPUT/OUTPUT (and any other
host-stream) globals when the compiland is a `DEVICE` unit/module, the same way
host-runtime externs are already suppressed there. Confirm zero unreferenced
globals in device PTX.

**How to verify.** Extend `tests/integration/test_device_mandelbrot_ptx.py` (or a
device-no-host-externs guard test) to assert no `.extern .global` for `input` /
`output` appears in the emitted PTX. Keep host INPUT/OUTPUT ownership unchanged.

---

## 3. Device codegen-quality gap vs `nvcc` (predication, FMA, alignment) [OPEN]

**Where.** Expression/statement lowering for `DEVICE` code (`codegen/exprs.py`,
`codegen/stmts.py`) and pointer-parameter typing (`codegen/types_map.py`).

**What.** A PTX diff of the Mandelbrot kernels (`nvcc` 12.8 vs this toolchain)
found only below-the-ABI-line differences. Three are codegen-quality gaps worth
closing when the device lowering is next tuned:

- **Branch vs predication on the bounds guard.** The source
  `IF width > 1 THEN ... ELSE ...` lowers to real control flow (`bra`); `nvcc`
  predicates it into a branchless `selp.f32`. Predication is the preferred GPU
  idiom because it avoids warp divergence at image edges.
- **No FMA fusion.** `2*x*y + y0` lowers to a discrete multiply/add; `nvcc` fuses
  it into one `fma.rn`. The FMA also carries more intermediate precision, so the
  two kernels can differ in the last bit (the rendered image still matched).
- **Conservative pointer alignment.** Pointer parameters are emitted as
  `.ptr .global .align 1`; the element type is known (`int`), so `.align 4` is
  the tighter, correct hint.

**Why it matters.** None of these affect correctness, ABI, or memory layout — the
kernel is a faithful drop-in as-is. They are the difference between "runs
correctly" and "indistinguishable from `nvcc`'s output," and the FMA/predication
points have real performance and edge-case-precision implications on large
renders.

**Suggested resolution.** Consider enabling `contract`/FMA fast-math on device
arithmetic (or emitting `llvm.fma` for the fused pattern), letting the NVPTX
backend predicate small `IF` guards (or lowering simple `IF/ELSE`-of-assignment to
`select`), and propagating element alignment to the pointer-parameter type. Treat
fast-math contraction as a deliberate, documented choice since it changes last-bit
results.

**How to verify.** Re-diff `mandelbrot.ptx` against the `nvcc` reference and check
for `fma.rn`, `selp`, and a tighter `.align`. Guard any FMA/fast-math change with a
note that device float results may differ in the last bit from the strict-IEEE
host path.
