# Implementation notes — `DEVICE UNIT` Phase 2.3 (emit entry points, not just device functions)

Rationale companion to the Phase-2.3 diff of
`device-unit-migration-checklist.md` (§2.3.1 export = entry, §2.3.2 kernel
calling convention, §2.3.3 entry-shape rules, §2.3.4 acceptance). Builds on the
Phase-2.1 + 2.2 tree.

## Verdict

§2.3 is implemented. In a `DEVICE UNIT`, each routine the interface exports
lowers to a GPU kernel (`ptx_kernel` / `amdgpu_kernel` calling convention),
which PTX renders as a `.visible .entry`; non-exported implementation routines
stay device-internal `.func`s; a `DEVICE MODULE` (no interface) keeps emitting
plain device functions. Verified end-to-end by emitting **real PTX** and
asserting `.visible .entry`/`.func`. Host, vintage, plain `MODULE`, `DEVICE
MODULE`, and x86 CPU-device IR are byte-identical to the Phase-2.2 tree (golden
compare). Full suite green (`658 passed, 54 subtests`).

## 1. (Key finding) The entry-shape rules cannot live in the checker unconditionally

§2.3.3 calls them "entry-point **checker** rules," but enforcing them in the
type checker as written would reject the existing parity milestone. Two facts
collide:

- The Phase-1.6 device-primes parity unit (`tests/integration/test_device_primes.py`)
  **exports two FUNCTIONs** (`prime_count`, `nth_prime`) and runs on the **x86
  CPU-device**, where returning a value is perfectly fine. A blanket "exported
  device routine must be a PROCEDURE" check would reject it and break a green
  milestone the non-goals forbid disturbing.
- The type checker is **triple-blind**. The device triple is a codegen concern
  (`--device-triple`), and most paths call `PascalTypeChecker().check(ast)`
  *without* a triple, handing it only to `compile_to_llvm` afterward. So the
  checker cannot even tell whether a real GPU entry will be formed.

The shape rules ("must be a PROCEDURE", "params device-passable") only bite when
a routine actually becomes a GPU `.entry` — i.e. device code on a GPU triple.
That is exactly where both the constraint and the triple are known: **codegen**.
So the work is split the way the §2.3.2 caveat already points:

- **Checker** marks *which* routines are exports (`is_exported_entry`), which it
  can do for any unit, triple or not.
- **Codegen** enforces the shape rules and sets the kernel convention, gated on
  `is_device_module and _is_gpu_triple(self.device_triple)`.

On x86 CPU-device the whole thing is inert: exported FUNCTIONs and VAR-param
procedures compile and run serially, so the primes parity port is untouched.
(This mirrors the Phase-2.1/2.2 pattern: the literal checklist anchor needed
adjustment because of *where* the deciding information is available.)

## 2. (Caveat handled exactly as prescribed) Marking survives separate compilation

The §2.3.2 caveat is real: codegen's `current_interface_decls` is only populated
when interface + implementation are parsed together; under normal separate
compilation `unit.interface is None` and codegen cannot see the export list. The
checker, by contrast, loads the interface from disk in `check_implementation_unit`
(`load_interface`).

So — as the checklist recommends — the checker marks `decl.is_exported_entry =
True` on each implementation `ProcDecl`/`FuncDecl` whose name is in the loaded
interface's export list (`InterfaceUnit.params`), and codegen reads that flag.
Codegen does **no** disk I/O and does **not** rely on `current_interface_decls`.
The flag rides the same AST object the checker and codegen share, so it persists
across the phase boundary. A dedicated test compiles an implementation *alone*
and asserts only `vecadd` (exported) is flagged, not `helper`.

## 3. (Decision) Calling convention alone suffices; metadata skipped

§2.3.2 lists `nvvm.annotations` metadata as optional. It is not needed: setting
`func.calling_convention = "ptx_kernel"` already yields a real `.visible .entry`
(confirmed by emitting PTX through llvmlite's NVPTX target — `vecadd` becomes
`.visible .entry vecadd`, `helper` stays `.visible .func helper`). Skipping the
metadata keeps the change minimal and avoids the metadata-API surface. The
convention is chosen off the triple: `amdgpu_kernel` for `amdgcn*`, `ptx_kernel`
otherwise.

## 4. (Decision) Device-passability rule, and its leniency

`_param_device_passable` rejects parameters that lower to an addrspace-0
(host-space) pointer a device entry cannot dereference:

- **reference-mode params** (`VAR`/`CONST`/`VARS`/`CONSTS`) — these are
  host-space pointers by construction;
- **plain `^T` heap / `ADR` pointers**, and **`ADS(HOST)`** (or an ADS with no
  space) — also host-space.

Value scalars and non-HOST `ADS(space) OF T` pass. One deliberate leniency: if
the space ordinal cannot be constant-folded, the param is *allowed* rather than
rejected — the checker has already validated the unit, so a fold hiccup should
not block a compile it passed. The rule is a guard rail (`should`), and erring
toward compiling a checker-valid program is safer than a false reject.

## 5. (Honest note) The rules invalidated one of my own earlier fixtures — as intended

The Phase-2.1 trap test exported `go(VAR x: INTEGER)` purely as a convenient
"device routine." Once §2.3.3 landed, that became an invalid kernel entry (a
`VAR` param is a host-space pointer), and the test's `nvptx64` case correctly
failed. This is the rule working, surfaced by the green gate — not a regression.
The §2.3 diff updates that fixture to a value parameter and points the CASE arms
at the local `y` (value params are immutable in this dialect), which keeps the
test exercising MATHCK/INDEXCK/RANGECK while being a valid entry. The 2.2
vector-add fixture already used a value parameter, so it needed no change.

## Things checked and found OK (so they are not re-litigated later)

- **Byte-identical for everything that isn't an exported GPU routine.** Golden
  compare (Phase-2.2 tree vs Phase-2.3 tree) of a host `PROGRAM`, a plain
  `MODULE`, a `DEVICE MODULE` on `nvptx64`, and a device **unit** on x86: all
  identical. §2.3 moves only the calling convention of exported routines on a
  GPU triple.
- **`DEVICE MODULE` has no entries.** No interface ⇒ nothing exported ⇒ no
  routine is flagged ⇒ no kernel convention even on `nvptx64`. Green gate.
- **Non-exported helpers stay `.func`.** Verified in both IR (no convention) and
  emitted PTX (`.func`, never `.entry`).
- **Both GPU families covered.** `nvptx64` ⇒ `ptx_kernel`; `amdgcn` ⇒
  `amdgpu_kernel`, off the single `_is_gpu_triple` predicate introduced in 2.2.
- **AST field is inert elsewhere.** `is_exported_entry` defaults `False` and is
  only ever set for device units, so host/vintage parsing, checking, and codegen
  are unaffected; the suite delta (`647 -> 658`) is exactly the new acceptance
  assertions.
