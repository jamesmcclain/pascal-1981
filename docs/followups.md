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

## 2. `is_root_compiland` makes every PROGRAM *and* MODULE a strong owner of `@input`/`@output` [OPEN]

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

**Suggested resolution.** Decide the ownership rule deliberately rather than by the
`not isinstance(...)` default. Options: (a) make *only* `ProgramUnit` a strong
owner and have `ModuleUnit` declare-external like a UNIT (correct if a MODULE is
never the program's entry point); or (b) keep MODULE as a root but add a
link-time/compile-time check that forbids linking two strong owners. (a) is
simpler and matches the "the PROGRAM owns the program-wide singletons" intent.

**How to verify.** Add an integration test that compiles a `PROGRAM` and a
separately-compiled `MODULE` that both touch `INPUT`/`OUTPUT`, links them, and
asserts a clean link (no multiple-definition error) and correct run output. That
test should fail under today's rule and pass after the ownership rule is tightened.

---

## 3. Super-array remediation residue and device-heap boundary [OPEN]

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
