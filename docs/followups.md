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

## 2. USES-import rejects a DEVICE INTERFACE that declares shared TYPEs [DONE]

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

**Relationship to item 3.** This item and item 3 below share the same root
cause (interface `TYPE` declarations not propagating into downstream scopes) but
manifest at different pipeline stages and require separate fixes. Fixing this
item does not fix item 3, and vice versa.

**Shipped** (`502a13c`). `import_symbols` now matches export names to routine
decls by name, not by count; non-routine TYPE/CONST decls no longer fail the
validation guard. The entire device-interface import runs under `_device_context`
so INTEGER32/ADS types resolve correctly. Non-exported TYPE/CONST decls are
imported into the host scope. `codegen_use_clause` updated in parallel (name-
based matching + TYPE/CONST seeding into `type_aliases`). `_is_writable_type`
and `_is_readable_type` now accept INTEGER32/INTEGER64 unconditionally (the
feature flag gates naming the type in source; it should not block printing a
correctly-typed value). Covered by `tests/integration/test_uses_device_shared_type.py`.
Note: full host-calls-kernel-with-ADS-buffer execution remains deferred to
Milestone D (host orchestration); the tests cover type-checker and compile+link.

---

## 3. Interface TYPE aliases invisible to the device implementation checker and codegen [DONE]

**Where.** Two parallel gaps:
- `type_checker.py::check_implementation_unit`: never calls `check_interface_unit`
  (or equivalent) before processing implementation declarations, so interface
  `TYPE` aliases are never added to the symbol table.
- `codegen/decls.py::codegen_implementation`: never iterates over `unit.interface.decls`
  to register `TypeDecl` nodes into `self.type_aliases` before processing
  implementation declarations.

**What.** A `DEVICE IMPLEMENTATION OF` file that references a type alias declared
in its own interface (via `$INCLUDE`) cannot resolve that alias. Confirmed by
direct reproduction:

```pascal
{ interface declares: TYPE BUFFER = SUPER ARRAY [0..*] OF INTEGER32; }
{ implementation omits the TYPE section and references BUFFER: }
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
```

Produces `ERROR: Unknown type: NamedType(name='BUFFER', param=None)` from the
type checker. In a subtler variant — where the procedure body dereferences the
pointer without the alias being resolved — the alias falls through to the `ADSMEM`
(ADS-of-CHAR) default and produces the confusing `ERROR: Cannot index non-array
type CHAR`.

The `mandelbrot` and future kernels using shared buffer type aliases currently
work around this by restating the `TYPE` section identically in both the
interface and the implementation. That is the `mandelbrot.pas` / `mandelbrot.inc`
pattern: `TYPE PIXELS = SUPER ARRAY [0..*] OF INTEGER32;` appears in both files.

**Why it matters.** It forces type alias duplication across the interface/
implementation boundary, contradicting the design intent that the interface is
the single source of truth for exported signatures. It also makes the interface
useless as the sole place to evolve a shared buffer type — both files must be
kept in sync manually. Any mismatch between the two `TYPE` sections would produce
a signature-mismatch error with no clear diagnostic pointing at the duplication.

**Suggested resolution.**

*Type checker:* at the top of `check_implementation_unit`, before the
`for decl in impl.decls` loop, iterate over `iface.decls` and call
`check_declaration(decl)` for each `TypeDecl` (and `ConstDecl`) — the non-
routine, non-executable declarations that are safe to re-process in the
implementation scope. Do not re-check `ProcDecl`/`FuncDecl` interface entries
at this point (they are handled by `validate_implementation_against_interface`
and `current_interface_decls`); seeding only type and constant aliases is
sufficient and avoids double-defining routine symbols.

*Codegen:* at the top of `codegen_implementation`, before the
`for decl in unit.decls` loop, iterate over `unit.interface.decls` and call
`codegen_decl(decl)` for each `TypeDecl` (and `ConstDecl`). Same
restriction: skip `ProcDecl`/`FuncDecl` to avoid emitting duplicate LLVM
function declarations.

*Both fixes together* allow the implementation to drop its redundant `TYPE`
section and rely solely on the interface's declarations, which is the intended
shape.

**How to verify.**

1. Remove the duplicate `TYPE PIXELS = ...` from `mandelbrot.pas`; confirm it
   still compiles to correct PTX — this is the direct regression test.
2. Add a unit test that compiles a device implementation with no `TYPE` section
   that references a type alias defined only in its interface, and asserts clean
   compilation and correct IR.
3. Add a negative test: a type alias defined in the implementation but *not* the
   interface that is referenced in the implementation body should still resolve
   (implementation-private types remain valid).
4. Keep the existing suite green — the fix must not affect host `UNIT`/`USES`
   paths, which go through separate code.

**Relationship to item 2.** See note at the end of item 2.

**Shipped** (`79c032a`). Both `check_implementation_unit` and `codegen_implementation`
now seed TYPE and CONST aliases from the interface before processing implementation
declarations; impl wins on name conflicts (no error). The duplicate
`TYPE PIXELS = ...` has been removed from `mandelbrot.pas`. Covered by
`tests/test_device_interface_type_seeding.py` (6 tests including the mandelbrot
no-duplicate-type regression test).

---

## 4. AdsExpr value form and coerce_arg segment round-trip still emit {ptr, i16} [OPEN]

**Where.** Captured from `docs/ads-implementation-plan.md` Step 4b (now archived).
Two touch points:
- `codegen/exprs.py` — `AdsExpr` value form: `ADS g` where `g` has `[SPACE(GLOBAL)]`
  still produces a `{T addrspace(1)*, i16}` struct with seg=0 instead of a bare
  `T addrspace(1)*`. The *type* lowering (`llvm_type` for `ADS(GLOBAL) OF T`) is
  correct (Step 4a); only the *expression* emission is stale.
- `codegen/types_map.py` line ~238 — `coerce_arg` seg→flat rule silently drops the
  segment when passing a `{ptr, i16}` ADS value to a flat-pointer parameter. Per
  design §6.3, cross-space should be a type error (data movement, not a cast); the
  struct path still has the old silent-drop, marked `# Step 4b / design S6.3`.

**Why it matters.** Device-kernel code (buffer parameters, FILLSC/MOVESL/MOVESR) is
already correct and runnable because `_device_seg_bridge` constructs addrspace
loads/stores from variable residence directly, bypassing the `AdsExpr` value form.
Step 4b is a cleanup: once done, a full `[SPACE(GLOBAL)] g: T; p := ADS g; p^`
round-trip in a `DEVICE MODULE` will produce a bare `addrspace(1)` load with no
`{ptr, i16}` intermediary in the IR.

**Suggested resolution.** Fix `AdsExpr` codegen to emit the bare addrspace pointer
directly when `is_device_module` and the operand has a non-HOST `[SPACE]` residence.
Fix `coerce_arg` to treat the seg→flat path as a type error in device context.

**How to verify.** Add a test that compiles `ADS g` on a `[SPACE(GLOBAL)]` variable
in a DEVICE MODULE (nvptx64 triple) and asserts the emitted IR contains no
`{ptr, i16}` struct — only a bare `addrspace(1)` pointer value.
