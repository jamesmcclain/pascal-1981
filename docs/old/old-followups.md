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
