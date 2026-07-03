## 9. docs/device-code claims need evidence grading before they drive work [OPEN]

**Where.** `docs/device-code/KERNEL_ANALYSIS.md`,
`docs/device-code/OPTIMIZATION_GUIDE.md`,
`docs/device-code/DETAILED_COMPARISONS.md`.

**What.** The analysis mixes observed artifacts (PTX listings, instruction
counts) with unsourced performance narrative: cycle-count models for a virtual
ISA that `ptxas` re-schedules, a "15-20x" pipelining projection walked back to
10-25% in the same section, and CUDA-comparison ratios whose nvcc
version/flags are not recorded. Since then, the compiler gained stock LLVM
optimization plumbing, language-level loop unrolling, tuning hints, kernel
parameter attributes, and range metadata support. That leaves only a narrower
set of potentially useful optimization threads: shared-memory tiling/caching,
deeper LICM/register-reuse opportunities, and a check on whether LLVM/NVPTX
already covers any remaining scheduling wins.

**Why it matters.** Per the repo's own anti-confabulation discipline
(OBSERVED / DOCUMENTED / INFERRED), the guide currently reads as more
authoritative than its evidence supports, and its costliest recommendations
(hand-rolled software pipelining, backend unroller) are superseded by newer
compiler features.

**Suggested resolution.** Annotate each claim with an evidence grade; record
the nvcc version and flags behind the comparison tables or regenerate them;
strike or demote the sections superseded by running the stock LLVM pipeline;
and carry forward only the remaining live ideas above as explicit follow-ups if
they still matter after a current benchmark pass.

**How to verify.** A pass over the three files leaves no ungraded quantitative
claim; comparison tables are reproducible from a committed script; any retained
optimization thread is backed by a current benchmark or an open compiler gap.

---

## 4. CLI progress chatter is emitted even without --verbose [DONE]

**Where.** `src/pascal1981/compile_to_llvm.py::main` (the `Parsing ...`,
`Type checking...`, `Generating LLVM IR...`, `Wrote ...` prints to stderr) and
`src/pascal1981/compile_to_ptx.py::main` (the `Wrote ...` print).

**What.** Every invocation printed four progress lines to stderr regardless of
`-v`. The `-v/--verbose` help text said it enabled per-declaration logging and
tracebacks, implying the default was quiet.

**Why it mattered.** Harmless interactively, but noisy in Makefiles and scripted
pipelines, and it made stderr unusable as a pure diagnostics channel.

**Resolution.** All four progress prints gated behind `-v` in `compile_to_llvm.py`;
the `Wrote` print in `compile_to_ptx.py` similarly gated. `--target ptx` path in
`compile_to_llvm.py` also updated. On success without `-v`, stderr is clean.

**How verified.** `PYTHONPATH=src python3 -m pascal1981.compile_to_llvm ok.pas
out.ll 2>err.txt` leaves `err.txt` empty on success. With `-v` the progress
lines appear. Full suite green: `1045 passed, 1 skipped, 138 subtests passed`.

---

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

---

## 5. MAXWORD32 / MAXWORD64 parity constants [DONE]

**Where.** `builtins_registry.py` (constant registration), `codegen/base.py`
(`self.constants` / `self.constant_types` seeding), `codegen/constfold.py`
(`_const_ir` width selection), and `codegen/io_write_read.py` (`_pas_type`, the
WRITE signed/unsigned format selector).

**What.** The wide *signed* types shipped with `MAXINT32`/`MAXINT64`, but the wide
*unsigned* types `WORD32`/`WORD64` had no `MAXWORD32` (`4294967295`) /
`MAXWORD64` (`18446744073709551615`) predeclared constants. They are now seeded
on both the checker and codegen sides, gated on `wide-integers` exactly like
`MAXINT32`/`MAXINT64`, and carry full `WORD32`/`WORD64` type identity.

**Why it mattered.** Minor parity gap only — the types were already fully usable
without them. The deferral was about the unsigned-constant width selection in the
codegen const path: `MAXWORD64` is `2**64-1`, which exceeds the signed i64 max,
so it cannot fall through to the i32 default in `_const_ir`.

**Resolution.** `_is...`/registration mirrors `MAXINT32`/`MAXINT64`:
`builtins_registry.py` registers both constants under `wide-integers` (with the
WORD32/WORD64 types now imported), and `codegen/base.py` seeds their magnitudes
into `self.constants`. `_const_ir` emits `MAXWORD64` at i64 alongside `MAXINT64`
(the all-ones bit pattern); `MAXWORD32` emits at the i32 default, which already
held its value. One step beyond the original touchpoints was required: WRITE
picks signed vs unsigned formatting from the argument's Pascal type via
`_pas_type`, and builtin constants are not seeded into the codegen scope, so
`_pas_type` returned `None` and both constants formatted signed (printing `-1`).
A small `self.constant_types` tag map (seeded alongside `self.constants`, gated
identically) now lets `_pas_type` recover the `WORD32`/`WORD64` tag so the wide
unsigned max constants print unsigned. (`MAXWORD` only ever printed correctly by
luck — `65535` fits in a positive signed i32 — which is why the high-bit-set wide
constants exposed the gap.)

**How verified.** New `TestWideMaxConstants` (gating + WORD32/WORD64 type
identity: same-type ACCEPT, WORD32->WORD64 widen ACCEPT, INTEGER assign REJECT,
WORD64->WORD32 narrow REJECT) and `TestWideMaxConstantsRun` (build-and-run:
`WRITELN(MAXWORD32)` prints `4294967295`, `WRITELN(MAXWORD64)` prints
`18446744073709551615`, and a round-trip through WORD32/WORD64 variables) in
`tests/test_wide_unsigned_types.py`. Full suite green: `971 passed, 1 skipped,
115 subtests passed`.

---

## Duplicate parser-fixture number: two `16_*` files in should_pass [DONE]

**Where.** `tests/fixtures/parser/should_pass/16_concat_string_procedure.pas`
and (formerly) `tests/fixtures/parser/should_pass/16_for_static.pas`.

**What.** The should_pass corpus is otherwise numbered uniquely; `16_` was used
twice (the string-procedure trio 16/17/18 collided with an earlier `16_for_static`).

**Why it mattered.** Cosmetic, but the numbering is meant as a stable index for
referring to fixtures in notes and reviews; a duplicated index invited "fixture
16" ambiguity in docs and commit messages.

**Resolution.** `git mv` renumbered `16_for_static.pas` to `19_for_static.pas`
(19 was the next free slot; 17/18 already belonged to the string-procedure
trio). Updated the one reference in `tests/test_parser.py`
(`test_for_loop_with_static_bounds` or equivalent fixture path).

**How verified.** `ls tests/fixtures/parser/should_pass | cut -d_ -f1 | sort |
uniq -d` prints nothing. `pytest -k parser` green (75 passed).

---

## Build-and-run tests hard-fail unless `make -C runtime` was run manually [DONE]

**Where.** `tests/support.py` (`RUNTIME_LIB = runtime/build/libpascalrt.a`, used
by the clang link step) and the README's testing instructions.

**What.** On a fresh checkout, `pytest tests` used to fail 61 build-and-run
tests with `clang failed: no such file or directory:
.../runtime/build/libpascalrt.a`. After `make -C runtime`, the same suite was
green. Nothing in the test harness built the runtime or explained the failure.

**Why it mattered.** New contributors saw a wall of 61 failures whose root
cause (missing prerequisite) was buried inside a clang stderr string; the
natural misread was "the compiler is broken."

**Resolution.** `tests/support.py` now defines `_ensure_runtime_lib_built()`,
called once at import time: if `RUNTIME_LIB` is missing and `clang` is
available, it runs `make -C runtime` (the same idempotent build the README
already documented) and only raises if that build itself fails, with an
explicit message pointing at `make -C runtime` and the captured stderr. The
README's testing section was updated to describe the automatic build instead
of listing it as a required manual step.

**How verified.** `git clean -xfd runtime/build && PYTHONPATH=src python3 -m
pytest tests/ -q` on a checkout with no prebuilt archive: the archive is built
automatically and the full suite passes (`971 passed, 1 skipped, 115 subtests
passed`).

---

## PTX golden-text assertions are brittle across llvmlite/LLVM versions [DONE]

**Where.** `tests/test_compile_to_ptx.py` (`st.global.u32`, and by extension the
other exact-mnemonic asserts), `tests/integration/test_device_ptx_artifact.py`,
`tests/integration/test_device_mandelbrot_ptx.py`.

**What.** These tests asserted the exact NVPTX mnemonic `st.global.u32`. With
llvmlite 0.48 (LLVM 20), which satisfies the declared `llvmlite>=0.47.0` range,
the backend emits `st.global.b32` instead, so 3 tests failed on a fresh,
in-range install even though the generated kernel was correct (the store,
guard, and index math were all present in the emitted PTX).

**Why it mattered.** CI/users tracking the newest llvmlite saw spurious
failures; the failures asserted nothing wrong with the compiler itself. There
is still no upper bound on llvmlite in `pyproject.toml`, so a resilient
assertion was preferred over a version pin.

**Resolution.** All three `assertIn('st.global.u32', ...)` call sites were
replaced with `assertRegex(..., r'st\.global\.[ub]32')`, matching either the
size-typed (`u32`) or bit-typed (`b32`) spelling — the tests check "a global
32-bit store to the buffer exists," not the exact type suffix.

**How verified.** Full suite green under both llvmlite 0.47.0 and 0.48.0
(`pip install --no-deps --force-reinstall llvmlite==0.47.0` /
`llvmlite==0.48.0`, then `PYTHONPATH=src python3 -m pytest tests/ -q`: `971
passed, 1 skipped, 115 subtests passed` under each). Environment restored to
0.47.0 afterward.

---

## Packaging metadata claims Python 3.8+ but the package cannot run below 3.10 [DONE]

**Where.** `pyproject.toml` (`requires-python = ">=3.8"` plus the 3.8/3.9
classifiers) and `src/pascal1981/compile_to_ptx.py`.

**What.** Two independent facts pinned the real floor at Python 3.10. First,
the declared dependency `llvmlite>=0.47.0` itself requires Python >= 3.10 on
PyPI, so `pip install pascal1981` on 3.8/3.9 could not resolve. Second,
`compile_to_ptx.py` uses PEP 604 union syntax in a function signature
(`emit_llvm_path: str | None = None`); at the time this was filed it lacked
`from __future__ import annotations` and would have raised `TypeError` on
import under 3.8/3.9.

**Why it mattered.** The metadata advertised support the package did not have;
users on 3.8/3.9 would have gotten a confusing resolver or import-time failure
instead of a clear "unsupported Python" message from pip.

**Resolution.** `pyproject.toml` now declares `requires-python = ">=3.10"` and
only lists the `Programming Language :: Python :: 3.10/3.11/3.12` classifiers
(the 3.8/3.9 entries are gone). `compile_to_ptx.py` was found to already carry
`from __future__ import annotations` (fixed independently before this item was
picked up), so no source change was needed there — the only remaining gap was
the metadata. A sweep of the rest of the tree for `X | None` annotations
without the future import (`grep -rl '| None' ... | xargs grep -L 'from
__future__'`) found only a false positive: `codegen/c_abi.py` has `| None`
inside a comment string, not live PEP 604 syntax.

**How verified.** `python3 -c "import tomllib; ...` confirms `requires-python
== '>=3.10'` and the classifier list. `python3 -c "import
pascal1981.compile_to_ptx"` succeeds. Full suite green:
`PYTHONPATH=src python3 -m pytest tests/ -q` → `971 passed, 1 skipped, 115
subtests passed`.

## Super-array remediation residue and device-heap boundary [DONE]

*(Moved from `docs/followups.md` item 1 when the dynamic-bound ABI shipped;
original text below, then the resolution.)*

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

**Resolution.** Done by the dynamic-bound ABI, design record
`docs/super-array-bounds-abi.md`:

- Long-form `NEW(p, u)` now allocates an 8-byte header before the element
  data, records `u` there as an i64, and points `p` at the data — so
  indexing, dereference, parameter passing, and the pointer's LLVM type are
  all unchanged.
- The dereferenced bound queries `UPPER(p^)` / `LOWER(p^)` are implemented
  end to end (parser-level `^` after the identifier in the intrinsic's
  argument, AST `deref` flag, type checking, codegen). `UPPER(p^)` reads the
  header back at run time for heap super arrays; `LOWER(p^)` and fixed-array
  pointee bounds stay static.
- `DISPOSE(p)` for super-array pointers frees from the header, not the data
  pointer (verified under AddressSanitizer).
- `$INDEXCK` on `p^[i]` previously aborted for any `i` above the declared
  lower bound (the static-bound helper guessed `(low, low)` for `[low..*]`);
  it now checks the static lower bound plus the dynamic header upper bound,
  and super arrays are excluded from the static `(low, high)` check path.
- The device-heap boundary is kept and sharpened: `NEW`/`DISPOSE` remain
  rescinded in DEVICE code, and `UPPER(p^)` on a super array is now a
  device-code type error directing the programmer to explicit bound
  parameters — matching the drop-in CUDA pointer-ABI split recorded in
  `docs/old/mandelbrot-ptx-substitution-plan.md`. No device codegen path
  changed; the committed `fill_indices` PTX regenerates byte-identical.
- Multi-dimensional super arrays, variant-record long-form `NEW`, and
  super-array *parameter* bounds remain out of scope pending new differential
  probes, per the original item; the design record carries the guidance.

**How verified.** `tests/test_super_array_bounds.py` (parser, typecheck,
IR-shape, and native run tests: bound round-trip, nonzero lower bounds,
full-range writes, dynamic out-of-bounds aborts, independent bounds across
allocations, header-relative DISPOSE, device-code rejection); existing
regression suites for string bounds, long-form `NEW`, and DEVICE heap
recission stay green; PTX artifact diff is empty.

## No source-level channel for launch bounds or per-loop hints [DONE]

*(Moved from `docs/followups.md` item 8 when the tuning-hints feature shipped;
original text below, then the resolution. The original's cross-references to
"item 9" mean the PTX optimization-pipeline item, still open.)*

**Where.** Kernel-entry emission in `codegen/decls.py` (no
`!nvvm.annotations` beyond kernel marking); the `$`-metacommand tier in
`lexer.py`/parser (currently `$if`/`$message`/push-pop only).

**What.** Two hint channels that LLVM cannot invent because they encode
programmer intent: (a) launch bounds — `maxntid` / `reqntid` / `minctasm`
annotations that let the backend budget registers for a known block size; and
(b) per-loop transform hints — `llvm.loop.unroll.count` etc., the `#pragma
unroll` equivalent. Neither has any surface syntax today.

**Why it matters.** Occupancy tuning (OPTIMIZATION_GUIDE §5/§6) is impossible
without (a); (b) gives users the guide's §1 unrolling benefits selectively
without a bespoke unroller, once item 9 lands. Both are pure hint plumbing —
the transforms remain LLVM's.

**Suggested resolution.** Reuse existing extension syntax: a bracket attribute
on exported device procedures (e.g. `[MAXNTID(256)]`, mirroring the current
attribute grammar) lowered to `!nvvm.annotations`, and a `{$unroll N}`
metacommand attaching `llvm.loop` metadata to the following loop. Gate both
behind a registered feature (they are not vintage IBM Pascal), consistent with
the `--dialect`/`-f` machinery.

**Resolution.** Done by the `tuning-hints` feature; design note
`docs/tuning-hints.md`:

- `[MAXNTID(x[,y[,z]])]`, `[REQNTID(x[,y[,z]])]`, and `[MINCTASM(n)]` parse in
  the existing bracket attribute grammar (contextual identifiers, so vintage
  programs using those names as ordinary identifiers survive). The type
  checker restricts them to exported device kernel PROCEDUREs with positive
  integer literal dimensions.
- One deviation from the suggested lowering, forced by evidence: the LLVM 20
  bundled with llvmlite 0.48 no longer reads maxntid/reqntid from
  `!nvvm.annotations` — only the newer `"nvvm.maxntid"="x[,y,z]"` function
  string attributes produce the `.maxntid`/`.reqntid` PTX directives
  (`.minnctapersm` still honors the annotation form). Codegen dual-emits both
  encodings, so old and new LLVM each read the one they understand; with both
  present, each PTX directive appears exactly once.
- `{$UNROLL n}` joins the metacommand tier: the count is stamped one-shot onto
  the next token and must immediately precede a FOR/WHILE/REPEAT (a misplaced
  stamp is a parse error, not a silently dropped hint). Loops carry the count
  on the AST; codegen attaches `llvm.loop.unroll.count(n)` metadata to the
  back-edge branch.
- llvmlite cannot express the *distinct self-referential* loop-ID node LLVM's
  unroll pass requires (a null first operand verifies but the hint is
  ignored — established empirically), so `compile_to_llvm` runs a targeted
  textual pass rewriting exactly the null-headed loop-ID nodes into
  `distinct !{ !N, ... }`. End to end, an `{$UNROLL 4}` loop calling an opaque
  EXTERN shows 4 call sites after LLVM's O2 pipeline vs 1 without the hint.
- Both channels sit behind the registered `tuning-hints` feature
  (in-extended), so they are rejected under the faithful vintage default,
  enabled by `-f tuning-hints` in host code, and on by default inside DEVICE
  code, whose feature baseline is the extended umbrella.
- Drop-in PTX discipline preserved: hint-free modules are byte-identical at
  the IR and PTX level (the committed `fill_indices` artifact regenerates
  unchanged), and on the x86 CPU-device parity path the attributes are inert.

**How verified.** `tests/test_tuning_hints.py`: parser accept/reject
(including the misplaced-`$UNROLL` and contextual-identifier cases),
type-check gating (vintage reject / `-f` accept / device auto-accept) and
placement/arity/value validation, IR-shape tests for both launch-bound
encodings and the self-referential loop metadata, PTX tests asserting
`.maxntid`/`.reqntid`/`.minnctapersm` (the item's stated verification), an
O2-pipeline test proving the unroll hint fires, and hint-free byte-identity
checks. Full suite green; `fill_indices` PTX diff empty.

---

## PTX path runs no LLVM IR optimization pipeline [DONE]

**Where.** `src/pascal1981/compile_to_ptx.py::llvm_ir_to_ptx` (and the
`--target ptx` path in `compile_to_llvm.py`).

**What.** The device path is parse → verify → `create_target_machine(cpu=...)`
→ `emit_assembly`. No mid-level pass pipeline (O2/O3) is ever run over the IR,
so LLVM's loop unrolling, LICM, GVN, instruction combining, and load/store
vectorization never fire. The recommendations in
`docs/device-code/OPTIMIZATION_GUIDE.md` §1 (unrolling), §2 (software
pipelining), and §4 (address hoisting) describe hand-implementing transforms
that the stock LLVM pipeline already provides.

**Why it matters.** The kernels we ship are effectively -O0 IR handed straight
to the NVPTX backend. Most of the guide's projected wins are available for the
cost of pipeline plumbing rather than weeks of bespoke backend passes — and a
bespoke unroller/pipeliner would be a maintenance liability duplicating opt.

**Suggested resolution.** After `parse_assembly`/`verify`, run llvmlite's new
pass manager (`PipelineTuningOptions` + `PassBuilder`, O2 default, flag-tunable
via e.g. `--opt-level`) before `emit_assembly`. Note that PTX is virtual
assembly and `ptxas` performs final scheduling/register allocation, so IR-level
cleanup is the right layer; do not hand-implement software pipelining or
PTX-level scheduling (OPTIMIZATION_GUIDE §2/§5) — see the kernel-parameter-facts
item for the frontend facts the pipeline needs to be effective on memory ops.

**Resolution.** `llvm_ir_to_ptx()`/`compile_file_to_ptx()` in
`compile_to_ptx.py` gained an `opt_level: int = 0` parameter, and both
`compile_to_ptx.py`'s CLI and `compile_to_llvm.py`'s `--target ptx` branch
gained `--opt-level {0,1,2,3}` (rejected with `--target host`, where it has no
meaning). Default 0 is an exact no-op — verified byte-identical to the
pre-flag output — so the existing exact-mnemonic PTX tests
(`test_device_ptx_artifact.py`, `test_device_mandelbrot_ptx.py`) needed no
changes. The implementation uses llvmlite's new-pass-manager binding
(`create_pipeline_tuning_options` / `create_pass_builder` /
`ModulePassManager.run`), the same API already exercised by
`test_tuning_hints.py::test_unroll_hint_fires_under_o2` before this item
existed — so no new API surface had to be discovered, only promoted from a
test fixture to a real, flagged production path.

While validating this item, a real pre-existing bug surfaced in the
already-shipped launch-bounds feature (see the entry immediately above this
one): the legacy `!nvvm.annotations` keys were misspelled with a spurious
underscore (`maxntid_x` instead of `maxntidx`), silently dropping the
`.maxntid`/`.reqntid` PTX directives on the LLVM 20.1.8 bundled with the
pinned `llvmlite==0.47.0`. That bug is unrelated to this item's own scope but
was fixed as part of the same working session since item "Device index
intrinsics lack !range metadata" and "Kernel entries carry no parameter
facts" both land adjacent NVVM-annotation-shaped metadata and depend on this
pipeline to make their benefit observable.

**How verified.** `tests/integration/test_device_ptx_o2.py`: byte-identity of
`--opt-level 0` vs the pre-flag call signature; argparse choice validation;
`--opt-level` rejected with `--target host`; the single-CLI `--target ptx`
path exercises the flag too; a mandelbrot O0-vs-O2 diff proves the pipeline
actually changes emitted PTX (register renumbering, guard hoisting) while
ABI-level facts (entry names, void return, no `func_retval`, parameter shape)
survive optimization. Deliberately does not pin exact O2 instruction
selection — this repo already has one scar from asserting exact mnemonics
across an LLVM version bump. Full suite green (1022 passed, 1 skipped).

---

## Device index intrinsics lack !range metadata [DONE]

**Where.** `codegen/exprs.py`, where `THREADIDX_*` / `BLOCKIDX_*` /
`BLOCKDIM_*` / `GRIDDIM_*` lower to `llvm.nvvm.read.ptx.sreg.*` calls.

**What.** The intrinsic calls carry no `!range` metadata. Clang attaches ranges
(e.g. tid.x ∈ [0, 1024), ntid.x ∈ [1, 1025)) so LLVM can prove grid-stride
index math is non-negative and non-overflowing. Without them the backend must
allow negative indices, blocking sign-extension elimination, `mul.wide.u32`
selection, and trip-count reasoning in exactly the loops our kernels use.

**Why it matters.** Frontend-only information, roughly ten lines of codegen,
zero semantic risk, and it feeds every downstream pass.

**Suggested resolution.** Attach `!range` to each sreg call using the CUDA
architectural limits keyed off `--sm` (conservative sm_70 defaults are fine).
Optionally emit `llvm.assume` for the derived global index when both factors
are range-annotated.

**How to verify.** IR test asserting `!range` on the sreg calls; PTX diff
showing e.g. `mul.wide.u32`/dropped `cvt` instructions in the fill_indices
kernel at O2.

**Resolution.** `codegen_device_index_builtin`'s nvptx branch (`exprs.py`)
attaches `set_metadata('range', ...)` to every sreg call: `tid`/`ctaid` reads
get `[0, max)`, `ntid`/`nctaid` reads get `[1, max+1)`, using conservative
CUDA-architectural ceilings graded DOCUMENTED (CUDA C Programming Guide
"Compute Capabilities" appendix, not measured against this repo) —
threadIdx/blockDim x,y ≤ 1024, z ≤ 64; blockIdx/gridDim x ≤ 2³¹−1, y,z ≤
65535. Applies uniformly regardless of `--sm`, since these are the hardware's
own register-width ceilings, not a property of a specific launch.

**Anti-confabulation correction to the followup's own "how to verify."** The
suggested verification (a PTX diff at O2 showing `mul.wide.u32` or dropped
`cvt` instructions) was tested empirically — on both shipped examples
(`fill_indices`, `mandelbrot`) *and* on a minimal synthetic repro built
directly against llvmlite outside this codebase, replicating the exact
`tid + ctaid*ntid` indexing pattern — and **did not hold** on the LLVM 20.1.8
bundled with this repo's pinned `llvmlite==0.47.0`: PTX output at
`--opt-level 2` is byte-identical with vs. without the `!range` metadata in
all three cases. The metadata is present, valid (round-trips through
`parse_assembly`/`verify`), and semantically correct, but this toolchain's
default O2 pipeline does not visibly act on it for this specific
instruction-selection question. This is recorded rather than hidden: the
original followup's claim was INFERRED (a plausible expectation from how
Clang/NVVM's own `!range` annotations are known to help elsewhere), not
OBSERVED, and the empirical result falsifies the specific mechanism (not the
metadata's correctness or its value as a frontend-only fact for other/future
passes or other LLVM builds).

**How verified.** `tests/test_device_index_intrinsics.py`: every sreg
intrinsic call carries the correct `(lo, hi)` `!range` pair; the CPU-device
TLS-global path carries none. `tests/integration/test_device_range_metadata_ptx.py`:
the metadata survives IR→PTX emission at opt-level 0 and 2 on both examples
without breaking the module; the mandelbrot O2 case is asserted to be
*byte-for-byte identical* with vs. without the metadata, documenting the
negative empirical result explicitly rather than silently dropping the
followup's original (falsified) verification claim. Full suite green (1026
passed, 1 skipped).

---

## Kernel entries carry no parameter facts: noalias / readonly / align / dereferenceable [DONE]

**Where.** `codegen/decls.py` (kernel-entry emission around
`calling_convention = 'ptx_kernel'`); contrast with `codegen/c_abi.py`, which
already sets attributes on the host C-ABI path.

**What.** Device kernel buffer parameters (`ADS(GLOBAL)` pointers) are emitted
as bare pointers. LLVM cannot itself infer that two buffers do not alias, that
a buffer is never written through, or its alignment — those are facts only the
frontend (Pascal semantics + the LAUNCH contract) can assert. Without them the
optimizer must stay conservative: no `ld.global.v4.f32` vectorization, no
read-only-cache (`ld.global.nc`) selection, limited load reordering.

**Why it matters.** This is the highest-leverage device codegen item and is
orthogonal to LLVM: it is precisely the information LLVM lacks. It also
multiplies the O2-pipeline item — an O2 pipeline over attribute-free pointers
leaves most memory-op wins on the table.

**Suggested resolution.** (a) `readonly`: the type checker can already prove a
kernel never assigns through a given buffer parameter; plumb that through to a
`readonly` (+ `nocapture`) attribute. (b) `align`/`dereferenceable(n)`: derive
from element type and, where the launch contract fixes a length parameter,
from bounds. (c) `noalias`: define it as part of the LAUNCH contract (distinct
buffer arguments must not overlap), document it, gate behind a feature flag if
there is any doubt about vintage-faithful semantics.

**How to verify.** Unit tests asserting the attributes appear in kernel-entry
IR; PTX-level test that a provably-readonly streamed buffer compiles to
`ld.global.nc.*` at O2; differential run of the mandelbrot/fill examples
confirming identical output.

**Correction to the followup's own premise.** "The type checker can already
prove a kernel never assigns through a given buffer parameter" turned out to
be wrong as stated: `_param_device_passable` rejects VAR/CONST-moded
parameters outright for a kernel entry (they lower to host-space addrspace-0
pointers a device entry cannot dereference), so there is no pre-existing
CONST-mode flag to read for a buffer parameter — kernel buffer parameters are
always value-mode `ADS(space) OF T`. `(a)` therefore needed a real (if
purely syntactic and deliberately conservative) analysis, not a lookup: see
Resolution below.

**Resolution.**

- `(b) align`/`dereferenceable`: `align` was already done by a prior item.
  `dereferenceable(n)` is now derived directly from the LLVM pointee type: a
  statically-sized `ir.ArrayType` pointee (a fixed `ARRAY[lo..hi] OF T`) gets
  `dereferenceable(count * element_size)` (via `c_abi.py::_size_of`); a
  `SUPER ARRAY [lo..*] OF T` pointee (bare element type, no static count) gets
  none — deliberately out of scope, since there is no compiler-enforced link
  between such a parameter and whichever sibling parameter might carry its
  runtime length, and guessing one would be an unproven inference this
  project's discipline avoids.
- `(a) readonly`/`nocapture`: `_kernel_readonly_param_names` walks the
  procedure's own body (a generic recursive dataclass-field walk over
  `decl.body`, since `ASTNode` subclasses are plain dataclasses) looking for
  (i) any assignment whose target dereferences the parameter (any selector
  chain containing DEREF), which disqualifies it; (ii) the parameter passed
  as a bare argument to any other call (FuncCall/ProcCallStmt), which is
  conservatively disqualified too, since there is no interprocedural proof
  the callee doesn't write through it; (iii) any WITH statement anywhere in
  the body, which conservatively disqualifies every parameter of that
  procedure (WITH's field designators aren't tied back to the originating
  pointer by this walk). This only ever *withholds* readonly, never wrongly
  grants it — a gap here is a missed optimization, not a correctness bug.
  llvmlite's `ArgumentAttributes` has no native `readonly` entry (only
  `noalias`/`nocapture` are whitelisted there); it is added by shadowing the
  instance's `_known` mapping the same way `_apply_launch_bound_attrs`
  shadows `FunctionAttributes._known` for launch-bound string attributes,
  adapted to the dict-shaped (not frozenset-shaped) `_known` argument
  attributes use — confirmed to round-trip through
  `parse_assembly`/`verify`/`emit_assembly`.
- `(c) noalias`: gated behind a new registered feature,
  `noalias-kernel-params` (`features.py`), deliberately **not** part of the
  `extended` umbrella (`in_extended=False`) and so, unlike `tuning-hints`,
  does **not** auto-enable inside `DEVICE` code — it asserts a contract about
  the *caller* (distinct `ADS(GLOBAL)`/`ADS(CONSTANT)` buffer parameters of a
  kernel entry do not overlap) that this compiler cannot itself verify at a
  `LAUNCH` call site, and getting it wrong is a silent miscompilation. The
  LAUNCH contract itself is now documented in
  `docs/device-kernel-orientation.md` §3.

**Empirical correction to the followup's own "how to verify."** The suggested
PTX-level check (a provably-readonly buffer compiling to `ld.global.nc.*` at
O2) was tested against the actual attribute-plumbed IR on this repo's pinned
`llvmlite==0.47.0`/LLVM 20.1.8 and **did not fire**: `readonly`+`nocapture`
alone, run through the same `PassBuilder`/`ModulePassManager` pipeline item 5
added, produced no `ld.global.nc` selection at `--opt-level 2` on a
synthetic read/write-buffer kernel built for this check. Plausible
explanation (not confirmed): NVPTX's `ld.global.nc` selection may depend on
target-specific IR passes (e.g. `NVPTXLowerArgs`) that a full
`TargetMachine::addPassesToEmitFile` codegen pipeline runs but a bare
mid-level `PassBuilder::buildPerModuleDefaultPipeline` does not. Likewise,
`noalias` alone produced no observable PTX difference (vectorization or
otherwise) on the mandelbrot example at O2 — expected in that specific case
anyway, since that kernel has no actually-overlappable buffer pair to
vectorize across. Recorded honestly rather than claimed: the attributes are
correct, safe, and present; their downstream backend payoff on this exact
toolchain configuration is unconfirmed and is left as a candidate follow-up
(try routing through the full target-machine codegen pipeline rather than
the bare IR pass manager) rather than asserted.

**How verified.** `tests/test_kernel_param_attrs.py`: readonly/nocapture
present only on a body-provably-unwritten buffer, absent when written through
or passed to another call, withheld entirely by a WITH statement (direct unit
test against `_kernel_readonly_param_names` on a hand-built AST fixture,
since a realistic parser round trip needs a record-typed ADS buffer,
orthogonal to what that unit checks); `dereferenceable` present for fixed
arrays, absent on the CPU-device parity path (no kernel entry at all);
`noalias` absent by default under both vintage and extended dialects, absent
even under the full extended umbrella without the explicit feature flag,
present (on every buffer param) only with `-f noalias-kernel-params`; a PTX
round-trip test confirming `parse_assembly`/`verify`/`emit_assembly` accept
the combined attribute set. Full suite green (1036 passed, 1 skipped).

---

## 3. ODD(WORD) is rejected but should be accepted [DONE]

**Where.** `builtins_registry.py` registered `ODD` as
`FunctionType('ODD', [('n', INTEGER_TYPE)], BOOLEAN_TYPE)`; the generic
builtin-function argument check in `type_checker.py::infer_expression_type`
rejected a WORD actual.

**What.** The manual states "the ODD function for INTEGER and WORD values"
(Elementary Types, BOOLEAN, p.6-6), but `ODD(w)` for `w: WORD` was a type error
("expected INTEGER, got WORD").

**Why it mattered.** A small vintage-conformance gap: a faithful program that
calls `ODD` on a WORD was wrongly rejected. It was intentionally left out of
the WORD/INTEGER strictness change set to keep that change coherent, and was
pinned as a KNOWN GAP in `tests/test_conversion_matrix.py::TestManualKnownGaps`.

**Resolution.** `ODD` is now special-cased in
`type_checker.py::infer_expression_type`, modeled on the `HIBYTE`/`LOBYTE`
siblings: it accepts `INTEGER_TYPE` and `WORD_TYPE`, returns `BOOLEAN_TYPE`,
and rejects other types (REAL, CHAR, etc.) with a clear mismatch message.
Because it is a custom branch (not the generic builtin path), no WORD/INTEGER
mix warning fires -- correct, since `ODD` does no signed arithmetic; it only
tests the low bit. The codegen lowering in `codegen/exprs.py` was already
signedness-independent (`val & 1` then `icmp_signed('!=', 0)` -- the
`!= 0` comparison is identical for signed and unsigned interpretation), so no
lowering change was required. The registered `FunctionType` in
`builtins_registry.py` is left as-is (INTEGER parameter); the special-case
branch is what widens acceptance to WORD, matching how the other
ordinal-flexible intrinsics (`ORD`, `SUCC`, `PRED`, `HIBYTE`, `LOBYTE`) are
handled.

**How verified.** `tests/test_conversion_matrix.py`: the pinned known-gap test
was flipped from REJECT to ACCEPT and renamed to `test_odd_accepts_word`; a
regression guard `test_odd_accepts_integer` and an over-widen guard
`test_odd_rejects_real_and_char` were added. `tests/test_codegen.py` gained
`test_odd_word_integer_parity`, a build-and-run test asserting `ODD(WORD)` and
`ODD(INTEGER)` agree at runtime for the same bit pattern (7 -> odd). Suite
green: `tests/test_conversion_matrix.py` (11 passed, 44 subtests passed),
`tests/test_word_int_strictness.py` (25 passed), the new codegen test passes.

---

## 2. WORD/INTEGER constant exemption: fold constant expressions [DONE]

**Where.** `type_checker.py::_is_constant_integer_expr` (consulted by
`_check_word_int_assign` and `_check_word_int_mix`).

**What.** The IBM Pascal 2.0 manual (Elementary Types, p.6-5) exempts INTEGER
*constants* from the WORD/INTEGER assignment and expression-mix restrictions:
"INTEGER type constants change to WORD type if necessary, but not INTEGER
variables." The constant detector previously recognized only integer *literals*
(including unary `+`/`-`) and direct references to named integer `CONST`s. It
did **not** fold constant *expressions* such as `k + 1`, `2 * SIZE`, or
`SUCC(k)`, so those were treated as non-constant and required an explicit
`WRD(...)` when crossing into WORD.

**Why it mattered.** This was slightly *stricter* than the vintage compiler,
which would accept any compile-time-constant INTEGER in a WORD context. It was a
conservative, safe deviation (it never accepted something it should reject), but
it could force a `WRD(...)` the genuine 1981 compiler would not have required.

**Resolution.** A new `_fold_const_int(expr) -> Optional[int]` was added to the
type checker alongside `_is_constant_integer_expr`. It folds a constant INTEGER
*expression* to its compile-time value: integer literals, unary `+`/`-`,
arithmetic `BinOp` (`+`, `-`, `*`, `DIV`, `MOD`) over foldable operands (DIV/MOD
by zero returns `None` rather than raising), `Identifier`/bare `Designator`
naming an integer-family `CONST` whose folded value was stashed on the Symbol,
and `ORD`/`SUCC`/`PRED` of a foldable operand. It returns `None` (never raises)
for anything it cannot fold, so non-constant and REAL/boolean/set operands fall
through cleanly. The named-CONST values are stashed in `check_const_decl` under a
dedicated `const_int` attribute (not `Symbol.value`, which is the codegen LLVM
value); decl-order checking guarantees earlier CONSTs are folded before later
ones reference them. `_is_constant_integer_expr` keeps its literal and
named-CONST fast paths (a CONST is exempt even when its value is not foldable)
and falls through to the fold for composite expressions. The
`_check_word_int_assign` and `_check_word_int_mix` bodies are unchanged and pick
up the widening uniformly across assignment, value-argument passing, function
return, and the equal-width WORD/INTEGER mix diagnostic. Codegen was unaffected:
its own `eval_const_expr` already folded these expressions; the gap was purely
the type-checker rejecting too eagerly. Range-checking of folded values against
INTEGER bounds (e.g. `30000 + 5000 > MAXINT`) is deliberately out of scope.

**How verified.** `tests/test_conversion_matrix.py` gained rows asserting ACCEPT
for `w := k + 1`, `w := 2 * size`, `w := SUCC(k)`, and `f(k + 1)` into a WORD
value parameter, plus a REJECT regression guard for `w := k + i` (constant plus a
variable is not a compile-time constant). The `named_const_to_word` row was
corrected to actually exercise a named CONST (`CONST k = 5; w := k`) rather than
a bare literal. `tests/test_word_int_strictness.py` adds
`test_constant_expression_exemption_is_clean` (`w + (k + 1)` is clean and
compiles under `strict-word-int`),
`test_constant_expression_into_word_assign_is_clean` (`w := k + 1`), and
`test_const_plus_variable_is_not_constant`. `tests/test_codegen.py` gained
`test_constant_expression_into_word_lowers_correctly`, a build-and-run test
asserting `w := k + 1` with `k = 5` yields 6. Suite green: 231 passed, 60
subtests passed across `test_conversion_matrix.py`, `test_word_int_strictness.py`,
and `test_codegen.py`.
