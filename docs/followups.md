# Follow-ups / tracked tech-debt

A home for known, non-blocking issues we have consciously decided to defer, so
they are not lost. Each item states what it is, where it lives, why it matters,
a suggested resolution, and how to verify the fix. Status is one of OPEN /
IN-PROGRESS / DONE.

These are not bugs that produce wrong output today; they are seams worth
closing when the surrounding code is next touched. Resolved items are moved to
`docs/old/old-followups.md` once they ship (most recently the device codegen-
quality gap vs `nvcc` — predication, FMA, alignment — which was item 2 here).


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

## 2. USES-import rejects a DEVICE INTERFACE that declares shared TYPEs [OPEN]

**Where.** `type_checker.py::import_symbols` (the `InterfaceUnit` branch):
the `len(export_names) != len(export_decls)` guard. The export list is the
UNIT's parameter list (`UNIT U (a, b)`); the declaration list is every decl in
the interface body, including any `TYPE`/`CONST`/`VAR` section.

**What.** A DEVICE INTERFACE that declares a shared type alongside its exported
routines — e.g. `examples/device_ptx/mandelbrot/mandelbrot.inc`, which declares
`TYPE PIXELS = SUPER ARRAY [0..*] OF INTEGER32;` before the two exported
`PROCEDURE`s — cannot be imported by a host `USES` clause. The TYPE decl makes
`len(export_decls)` (3) exceed `len(export_names)` (2), so the checker reports
`Interface 'MANDELBROT' export list does not match its declarations` and the
referenced procedures become undefined. A device interface with no shared types
(e.g. the primes/grid-stride fixtures) imports fine.

**Why it matters.** It blocks the natural host-launcher shape for a device unit
whose kernels take a typed buffer: the host program ought to be able to `USES`
the unit and pass an `ADS(GLOBAL) OF PIXELS` through to the kernel using the
shared type name. Today the only ways to exercise such a unit from the host are
to compile the unit standalone (as the PTX substitution test does) or to call
the lowered routine directly from C (as
`tests/integration/test_device_mandelbrot_x86.py` does via a C harness). The
check is also stricter than the `ImplementationUnit`/`ModuleUnit` branch right
below it, which filters decls by `getattr(decl, 'name', None)` and would skip a
nameless TYPE section.

**Suggested resolution.** In the `InterfaceUnit` branch, derive `export_decls`
from the named, exported routine decls only (filter to `ProcDecl`/`FuncDecl`
whose `name` is in `export_names`), matching the leniency the sibling branch
already uses, rather than comparing against every body decl. Shared TYPE/CONST
names from the interface should still be imported into the host scope so a
`USES`-ing program can name `PIXELS`; decide whether non-exported interface
decls are name-imported or type-only-imported. Keep rejecting a real export-
list/declaration mismatch (an export name with no matching declaration, or a
declaration the export list did not name).

**How to verify.** Add a host `USES MANDELBROT` integration test that declares
a `PIXELS`-typed buffer, calls `mandelbrot_f32`/`mandelbrot_f64`, and checks the
result — replacing the C-harness workaround in
`tests/integration/test_device_mandelbrot_x86.py`. Keep the existing
`test_device_unit_parity.py` import-shape tests green (those interfaces have no
shared types, so they are unaffected).
