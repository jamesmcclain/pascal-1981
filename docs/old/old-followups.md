## 1. `runtime_extern` has a dual function-creation path (and a linear-scan safety net) [DONE]

**Where.** `codegen/base.py` — `runtime_extern(name)` and `_build_extern_factories`;
the bypassing creators are `io_write_read.py` (`_read_helper`) and the
`_runtime_func`/`_declare_libm_func` helpers.

**What.** The lazy-extern scheme materialises host-runtime externs from a factory
registry on first reference and caches them in `_root_scope`. But not every
runtime function goes through that registry: `_read_helper` and the libm/runtime
helpers still create `ir.Function`s directly, outside the factory path. To cope,
`runtime_extern` has a **middle tier** that linearly scans `self.module.functions`
for a name the registry/scope did not already have, then adopts it into
`_root_scope`. So there are two ways a runtime function can come into being, and
`runtime_extern` papers over the gap with an O(n) scan.

**Why it matters.** It is correct and cached (the scan runs at most once per
name), so there is no behavioural or real performance problem today. But the dual
creation path is a latent inconsistency: a future signature change to one of the
`_read_helper`-created functions would not be reflected in the factory registry
(or vice-versa), and the two could silently drift. The linear scan is also a
small smell that signals the migration to the registry was not completed.

**Resolution.** Done by `Centralize runtime extern creation`: the remaining
host-runtime/libc/libm direct creators now route through `_build_extern_factories`
and `runtime_extern()`. The old `module.functions` linear-scan safety net was
removed. Runtime extern caching now uses a private cache rather than the Pascal
symbol table, avoiding name collisions with predeclared identifiers such as
`ABORT`.

**How verified.** `rg "ir\\.Function\\(" src/pascal1981/codegen` now leaves
only generated Pascal routines, device intrinsics, and the factory itself as
direct creators. `tests/test_lazy_externs.py` includes private-cache,
unknown-extern, string, libm, and runtime-check guards. Full suite passed:
`768 passed, 63 subtests passed`.

---

## 2. `is_root_compiland` makes every PROGRAM *and* MODULE a strong owner of `@input`/`@output` [DONE]

**Where.** `codegen/__init__.py` — `is_root_compiland = not isinstance(ast,
(InterfaceUnit, ImplementationUnit))`; consumed by
`codegen/base.py:_register_predeclared_files`.

**What.** The S4.1 fix makes the root compiland emit a *strong* definition of the
`@input`/`@output` global singletons and makes UNIT compilands (interface /
implementation) emit *external* declarations, so a linked program has exactly one
owner. "Root" is currently "anything that is not an interface or implementation"
— which is both `ProgramUnit` **and** `ModuleUnit`.

**Why it matters.** The real link scenarios in the suite are PROGRAM + one or more
UNITs, where exactly one root (the PROGRAM) defines the globals — no collision,
and this is what let us drop `-Wl,--allow-multiple-definition`. But if a
standalone `MODULE` were ever compiled to its own object and linked **alongside a
PROGRAM**, both would be "root", both would emit strong `@input`/`@output`
definitions, and the multiple-definition collision would return. Nothing exercises
this today (modules are not separately linked into programs in the current tests),
so it is a latent boundary, not an active bug.

**Resolution.** Option (a) was implemented: only `ProgramUnit` is a strong owner.
`ModuleUnit`, `InterfaceUnit`, and `ImplementationUnit` now declare `@input` and
`@output` externally. Plain `MODULE`s are library-like, not independently
runnable; earlier "launchable MODULE" wording was stale/confused with DEVICE
kernel entry-point discussion.

**How verified.** `tests/test_lazy_externs.py` now asserts that MODULEs declare
`@input` / `@output` externally and that combining PROGRAM IR with separately
compiled MODULE IR yields exactly one strong definition of each singleton.

---

## 3. Phantom `.extern .global input/output` in device PTX [DONE]

*(Originally item 2 of `docs/followups.md`; archived here once shipped.)*

**Resolution.** `compile_to_llvm` now derives `is_device_compiland` from the AST
root's `is_device` flag and threads it through `Codegen` into `codegen/base.py`.
The constructor only calls `_register_predeclared_files` for non-device
compilands, so a `DEVICE` unit/module emits neither the `input`/`output` globals
nor their scope entries. Host `PROGRAM` (strong definition) and host `MODULE`/
`UNIT` (declare-only external) compilands are unchanged. This is the
construction-time analogue of the lazy-extern suppression already used for
host-runtime functions: device code never references the host streams, so they
never appear. Verified: device IR/PTX (MODULE and UNIT, on the nvptx64/amdgcn GPU
triples and the x86 CPU-device triple) carries no `input`/`output`; host paths
keep theirs.

**How to verify.** `tests/integration/test_device_mandelbrot_ptx.py::
test_no_phantom_input_output_externs` asserts no `.extern .global ... input/
output` in the emitted PTX (and no host-stream global in the IR).
`tests/test_device_no_host_externs.py::TestDeviceNoPhantomInputOutput` adds the
IR-level guard for device MODULE/UNIT across all three triples plus the host-path
regression checks.

The original analysis is preserved below for context.

---

### Original note

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
(`docs/old/mandelbrot-ptx-substitution-plan.md`, "Hardware validation result").

**Suggested resolution.** Suppress emission of the INPUT/OUTPUT (and any other
host-stream) globals when the compiland is a `DEVICE` unit/module, the same way
host-runtime externs are already suppressed there. Confirm zero unreferenced
globals in device PTX.

**How to verify.** Extend `tests/integration/test_device_mandelbrot_ptx.py` (or a
device-no-host-externs guard test) to assert no `.extern .global` for `input` /
`output` appears in the emitted PTX. Keep host INPUT/OUTPUT ownership unchanged.

---

## 4. Device codegen-quality gap vs `nvcc` (predication, FMA, alignment) [DONE]

**Where.** Expression/statement lowering for `DEVICE` code (`codegen/exprs.py`,
`codegen/stmts.py`) and pointer-parameter typing (`codegen/types_map.py`,
`codegen/decls.py`).

**What.** A PTX diff of the Mandelbrot kernels (`nvcc` 12.8 vs this toolchain)
found only below-the-ABI-line differences. Three codegen-quality gaps:

- **Branch vs predication on the bounds guard.** `IF width > 1 THEN ... ELSE ...`
lowered to real control flow (`bra`); `nvcc` predicates it into a branchless
`selp.f32`.
- **No FMA fusion.** `2*x*y + y0` lowered to a discrete multiply/add; `nvcc`
fuses it into one `fma.rn`.
- **Conservative pointer alignment.** Pointer parameters were emitted as
`.ptr .global .align 1`; the element type is known (`int`), so `.align 4` is the
tighter, correct hint.

**Why it mattered.** None affected correctness, ABI, or memory layout — the
kernel was a faithful drop-in as-is. They were the difference between "runs
correctly" and "indistinguishable from `nvcc`'s output," and the FMA/predication
points have real performance and edge-case-precision implications on large
renders.

**Resolution.** Done across three independent commits:

1. **Alignment** — `codegen/types_map.py::natural_alignment` computes the
   pointee type's natural byte alignment, and `codegen/decls.py::_apply_kernel_entry`
   annotates each device kernel-entry pointer arg with `arg.attributes.align`, so
   `ADS(GLOBAL) OF INTEGER32` emits `.ptr .global .align 4`. Correctness-neutral
   hint; inert on host, x86 CPU-device, and non-pointer params.
2. **Predication** — `codegen/stmts.py::codegen_if_stmt` gains a conservative
   peephole (`_try_select_if`) that lowers `IF c THEN x := a ELSE x := b` on a
   scalar `x` with pure, side-effect-free, non-faulting RHS to a branchless LLVM
   `select` (PTX `selp`). It bails to branch lowering on anything ambiguous
   (aggregate/string targets, mismatched targets, multi-statement arms, function
   calls in the RHS, dividing ops that could trap, indexed/dereferenced reads
   that could fire INDEXCK/NILCK, or selector-bearing targets). The scalar
   assignment coercion was extracted into a shared `_coerce_assign_value` helper
   so both select arms are coerced through the same int/float/pointer path.
3. **FMA** — `codegen/exprs.py::_fp_binop` emits device fp binops
   (fadd/fsub/fmul/fdiv) with the LLVM `contract` fast-math flag, so the NVPTX
   backend fuses `a*b + c` into `fma.rn`, matching `nvcc --fmad=true`. This is a
   **deliberate device-only choice**: host code keeps the strict, flag-free path
   byte-identical, so device float results may differ from the strict-IEEE host
   path in the last bit.

**How verified.** `tests/integration/test_device_mandelbrot_ptx.py` now asserts
`.ptr .global .align 4` on the output param, two `selp` guards per kernel (the
`wd`/`hd` bounds guards), and `fma.rn.f32`/`fma.rn.f64` in the respective
kernels. `tests/test_device_if_select.py` pins the select peephole hit plus the
bail-outs (mismatched targets, multi-statement arms, division in RHS, call in
RHS). `tests/test_device_fma_contraction.py` pins that device fp ops carry the
`contract` flag and emit `fma.rn`, and that the host path stays strict (no
`contract`/`fast` flags leak onto host fp ops). The full suite (806 tests)
passes. Re-diff `mandelbrot.ptx` against the `nvcc` reference to confirm the
three gaps are closed.

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

## 4. AdsExpr value form and coerce_arg segment round-trip still emit {ptr, i16} [DONE]

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

**Shipped.** On re-investigation the AdsExpr value-form half was already
correct: inside a DEVICE MODULE `ADS g` lowers to a bare `addrspace(k)` pointer
(verified — a `[SPACE(GLOBAL)] g; p := ADS g; p^` round-trip emits no
`{ptr, i16}` intermediary). The `coerce_arg` seg→flat branch turned out **not**
to be dead: it is the correct host vintage seg→flat collapse (e.g. passing
`ADS x` to an `ADRMEM`/flat-pointer parameter compiles, links, and runs), so it
was retained rather than deleted. Device code can no longer reach it (device ADS
values are bare addrspace pointers reconciled by the bare-pointer path, which
already rejects an addrspace mismatch), and it is now hardened to raise loudly
under `is_device_module` instead of silently dropping a segment — implementing
this item's "treat the seg→flat path as a type error in device context"
resolution. Covered by `tests/test_device_ads_no_segment.py`.

---

## 6. WORD32/INTEGER32 (same-width) signedness mix is undiagnosed [DONE]

**Where.** `type_checker.py::_check_word_int_mix` (the same-width unsigned/signed
diagnostic) and `type_system.py::binary_op_result_type` (which resolves a
same-width mix to the unsigned type).

**What.** The vintage WORD/INTEGER (16-bit) expression mix warned (and errored
under `-f strict-word-int`), but the analogous *wide* same-width mixes
(`WORD32`/`INTEGER32`, `WORD64`/`INTEGER64`) silently resolved to the unsigned
type with no diagnostic — even under `strict-word-int`. The check was hard-wired
to the rank-0 pair (`a_t == WORD_TYPE and b_t == INTEGER_TYPE`), so it never fired
for the wide extension types.

**Why it mattered.** The wide types are extensions outside the 1981 manual, so
leaving them undiagnosed was a safe default rather than a wrong result. But a
same-width unsigned/signed mix carries the identical "which signedness does the
arithmetic use?" ambiguity at every width, and a user who opted into
`-f strict-word-int` could reasonably expect the same signedness discipline
across the whole integer family.

**Resolution.** `_check_word_int_mix` now generalizes to the full
WORD-family/INTEGER-family at **equal rank** (WORD/INTEGER, WORD32/INTEGER32,
WORD64/INTEGER64) via small `_WORD_FAMILY_RANK`/`_INT_FAMILY_RANK` maps. The
behavior is uniform across widths: a warning by default, a hard error under
`-f strict-word-int`, and the INTEGER-constant exemption preserved at every
width. Unequal-width mixes are deliberately **not** flagged — there the wider
operand's signedness wins unambiguously (e.g. `WORD(16) + INTEGER32 ->
INTEGER32`), so there is no coin-flip to warn about. The 16-bit behavior is
byte-for-byte unchanged. The stale "wide-type mixes are not diagnosed" comment in
`binary_op_result_type` was corrected.

**How verified.** New rows in `tests/test_conversion_matrix.py`
(`word32_plus_int32_var_default` ACCEPT-with-warning, `word32_plus_int32_var_strict`
REJECT, `word64_plus_int64_var_strict` REJECT, the constant-exemption row, and an
unequal-width clean row) plus a new `TestWideSameWidthMix` class in
`tests/test_word_int_strictness.py` (warns by default, errors under strict, holds
the constant exemption, leaves unequal-width mixes clean). The existing 16-bit
WORD/INTEGER strictness tests remain green, confirming no regression.
