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
