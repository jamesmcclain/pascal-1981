# Checklist — add a `DEVICE UNIT` construct (alongside, not replacing, `DEVICE MODULE`)

**Goal.** Introduce a `DEVICE UNIT` device compiland (an `INTERFACE` + `IMPLEMENTATION OF`
pair marked as device code) that reaches **parity** with the existing `DEVICE MODULE`
implementation, then **exceeds** it on three fronts: no compiler-inserted runtime checks in
device code, no unconditional host-runtime extern dump, and emission of launchable **entry
points** rather than only device functions.

**Non-goal / explicit constraint.** Do **not** remove `DEVICE MODULE`. It stays as the
single-file, device-*function*-emitting form (useful later). Every change below is additive
and gated; the `vintage`/host path and the existing `DEVICE MODULE` path must stay
byte-for-byte unchanged.

**Why this is less work than it looks.** The device machinery — the recissions, the
`SPACE`/addrspace lowering, the `FILLSC`/`MOVESL`/`MOVESR` cross-space bridge, the
dereferenceability invariant — all already key off a **single boolean**: `in_device_module`
in the type checker (`type_checker.py:89`) and `is_device_module` in codegen
(`codegen/base.py:101`). Most of "parity" is **setting that same boolean from device-unit
contexts**; the rules themselves are already written and will fire unchanged once the flag is
set. The new *engineering* is mostly in the three "beyond" items.

**How to read this file.**
- `[ ]` items are ordered; earlier phases gate later ones.
- **Anchor** = `file.py:symbol (~line)`. **Line numbers drift — re-grep the symbol before
  editing.** (This codebase has repeatedly proven that.)
- **Green gate** = the condition that must hold before the item is "done." The universal green
  gate, in addition to any stated one: **full suite stays green** (`PYTHONPATH=src python3 -m
  pytest tests/ -q`, currently `634 passed, 52 subtests`) **and** host/vintage + existing
  `DEVICE MODULE` output is unchanged.

**Companion docs.** `docs/cuda-kernel-prescription.md` §1.5 (the decision and the
initializer-block rescission), §2 (the runtime-check + extern-dump gaps), §3 (entry points).
This checklist is the build sequence for those.

---

## Phase 0 — Decide the surface syntax (one owner decision, then proceed)

A `UNIT` is two separately-compiled files (an `INTERFACE` and an `IMPLEMENTATION OF`). Under
separate compilation, **each file must self-declare device-ness** — when the implementation is
compiled alone, the parser/checker/codegen must know it is device code *without* reading the
interface. So the marker appears on both files.

- [x] **0.1 Choose the marker placement.** Two coherent options:
  - **(A, recommended) Contextual `DEVICE` prefix on the compilation unit**, mirroring the
    existing `DEVICE MODULE` exactly: `DEVICE INTERFACE; UNIT name (exports); … END;` and
    `DEVICE IMPLEMENTATION OF name; … .`. Pros: a near-copy of `_at_device_module`
    (`parser.py:99`); uniform; minimal new machinery.
  - **(B) `DEVICE` before `UNIT`**: `INTERFACE; DEVICE UNIT name (…);`. Reads closer to the
    name "DEVICE UNIT", but the marker sits in a different place in the implementation file
    (still needs a `DEVICE IMPLEMENTATION OF`), so it is less uniform.
  - **Recommendation:** option A. The rest of this checklist assumes A; if the owner picks B,
    only Phase 1.1/1.2 anchors shift.
- [x] **0.2 Confirm `DEVICE` stays a contextual keyword** (not lexer-reserved), so vintage code
  may still use `device` as an identifier — same property the current `DEVICE MODULE` relies on
  (`parser.py:_at_device_module`, grammar note at `ebnf_grammar.md:39`).

---

## Phase 1 — Parity with `DEVICE MODULE`

### 1.1 AST

- [x] **1.1.1 Mark device-ness on the unit AST nodes.** Add `is_device: bool = False` to
  `InterfaceUnit` (`ast_nodes.py:56`) and `ImplementationUnit` (`ast_nodes.py:64`), mirroring
  `ModuleUnit.is_device` (`ast_nodes.py:41`).
- [x] **1.1.2 Record initializer-block presence for the ban (Phase 1.5).** `ImplementationUnit`
  already carries `init_body`; `InterfaceUnit` does **not** store its optional `BEGIN … END`
  block. Either add a `has_init: bool = False` field to `InterfaceUnit`, or plan to reject the
  init block at parse time for device interfaces (see 1.5.2). Pick one now. **Chosen and
  implemented:** `InterfaceUnit.has_init: bool = False`.

### 1.2 Parser + EBNF grammar

- [x] **1.2.1 Dispatch the new device units.** In `parse_compilation_unit` (`parser.py:66`),
  add detection for a contextual `DEVICE` preceding `INTERFACE` / `IMPLEMENTATION`, parallel to
  the existing `_at_device_module` branch (`parser.py:72`). Factor a small helper
  (`_at_device_prefix(next_kind)`) or add `_at_device_interface` / `_at_device_implementation`
  beside `_at_device_module` (`parser.py:99`).
- [x] **1.2.2 Thread the flag through the unit parsers.** Give `parse_interface_unit`
  (`parser.py:137`) and `parse_implementation_unit` (`parser.py:170`) an `is_device: bool =
  False` parameter (mirroring `parse_module_unit(is_device=)`, `parser.py:110`), and pass
  `is_device` into the constructed `InterfaceUnit` / `ImplementationUnit`.
- [x] **1.2.3 Update the EBNF grammar.** In `docs/ebnf_grammar.md`, extend `interface_unit`
  (`:45`) and `implementation_unit` (`:55`) with an optional leading `[ "DEVICE" ]`, mirroring
  the `module_unit` treatment (`:32`) and its contextual-keyword note (`:39`). State that a
  `DEVICE INTERFACE`/`DEVICE IMPLEMENTATION` is the device dialect (extended − recissions +
  address-space surface), exactly as the `DEVICE MODULE` note says.
- [x] **1.2.4 Parser acceptance tests.** `DEVICE INTERFACE; UNIT U (f); … END;` ⇒
  `InterfaceUnit.is_device is True`; plain `INTERFACE;` ⇒ `False`. Same for
  `DEVICE IMPLEMENTATION OF U; … .`. **Regression:** a vintage program using `device` as an
  ordinary identifier still parses (contextual-keyword safety) — mirror the existing
  `DEVICE MODULE` regression test.
- **Green gate:** parser suite green; the `DEVICE MODULE` parse path untouched.

### 1.3 Type checker — device context for units (this is where parity mostly happens)

The device dialect is gated by `in_device_module` (`type_checker.py:89`). `check_module_unit`
(`type_checker.py:530`) wraps its body with the device-context dance (`:554-570`): save
`in_device_module` / `features` / `_device_callgraph`, set `in_device_module = True`, swap in
`device_features()`, reset the callgraph, run the body, call `_detect_device_recursion()` at
the end, then restore in `finally`.

- [x] **1.3.1 Factor the device-context wrapper.** Extract the save/set/restore logic from
  `check_module_unit` into a reusable helper (e.g. a context manager `_device_context(active:
  bool)` or a `_enter_device_dialect()` / `_exit_device_dialect()` pair). This avoids copying
  the dance three times and keeps `DEVICE MODULE` and `DEVICE UNIT` semantically identical by
  construction.
- [x] **1.3.2 Wrap `check_interface_unit`** (`type_checker.py:574`) in the device context when
  `iface.is_device`. Run the existing declaration checks inside it. Call
  `_detect_device_recursion()` at the end (interfaces are mostly headers, but routines with
  bodies can appear; keep it symmetric).
- [x] **1.3.3 Wrap `check_implementation_unit`** (`type_checker.py:594`) in the device context
  when `impl.is_device`. The implementation is where bodies live, so this is the main site the
  recissions (host I/O, `NEW`/heap, recursion) will actually fire — and they already do, off
  `in_device_module`, with **no new recission code** (`_check_device_recission`
  `:148`, `_detect_device_recursion` `:173`). Ensure the callgraph is reset/scoped per
  implementation.
- [x] **1.3.4 Device-consistency check.** When `check_implementation_unit` loads/validates
  against its interface (`validate_implementation_against_interface`, called at `:604`), assert
  that `impl.is_device == iface.is_device`. A `DEVICE IMPLEMENTATION OF` a non-device interface
  (or vice versa) is an error ("device-ness of implementation must match its interface").
- [x] **1.3.5 Generalize the `in_device_module` *name* (optional, low-risk).** The flag now
  means "in device code," not specifically a module. Either leave the name (cheapest; add a
  one-line comment that "module" now reads "device compiland") or rename `in_device_module` →
  `in_device_code` and `is_device_module` → `is_device_code` across the tree. **Recommendation:
  leave the names** to minimize diff and risk; the semantics already generalize.
- **Green gate:** type-check suite green; a `DEVICE IMPLEMENTATION OF` body that does `WRITELN`
  / `NEW` / recurses is rejected with the *same* messages a `DEVICE MODULE` produces; a
  non-device `INTERFACE`/`IMPLEMENTATION` is byte-identically unaffected.

### 1.4 Codegen — device lowering for units

`codegen_module` (`codegen/decls.py:71`) flips `is_device_module = True` and swaps
`self.module.triple = self.device_triple` for a device module (`:78-84`), restored in
`finally`. The addrspace lowering (`_space_addrspace`, `base.py:170`), `[SPACE(s)]` residence
storage (`decls.py:330`), and the `_device_seg_bridge` (`runtime_builtins.py:330`) all key off
`is_device_module` — so they work unchanged once the flag is set.

- [x] **1.4.1 Factor the codegen device-gating** the same way as 1.3.1 (a small
  `_enter_device_codegen()` / `finally` restore, or a context manager) so module and unit share
  it.
- [x] **1.4.2 Gate `codegen_interface`** (`codegen/decls.py:87`) on `unit.is_device`: set
  `is_device_module = True` + device triple for the body, restore in `finally`.
- [x] **1.4.3 Gate `codegen_implementation`** (`codegen/decls.py:93`) likewise on
  `unit.is_device`. This is where device routine bodies, `[SPACE]` residence, and the seg-bridge
  actually lower; confirm they emit addrspace-correct IR exactly as the `DEVICE MODULE` primes
  example does.
- [x] **1.4.4 Codegen parity tests.** A `DEVICE IMPLEMENTATION OF` with `[SPACE(SHARED)]` /
  `[SPACE(GLOBAL)]` arrays and a `MOVESL` bridge, compiled with `--device-triple
  nvptx64-nvidia-cuda`, emits the same `addrspace(3)`/`addrspace(1)` + `ld.shared`→`st.global`
  shape the device-primes `DEVICE MODULE` does. With default `device=x86`, spaces collapse to
  addrspace 0 and it runs on the CPU.
- **Green gate:** plain `INTERFACE`/`IMPLEMENTATION` codegen byte-identical; `DEVICE MODULE`
  codegen byte-identical; device-unit IR matches the device-module IR for equivalent source.

### 1.5 The initializer-block rescission (decided: `docs/cuda-kernel-prescription.md` §1.5.3)

A `DEVICE UNIT` may **not** carry an initializer block — neither the interface's optional
`BEGIN … END` nor the implementation's `BEGIN … END.` body. This is a new module-scoped
rescission in the same family as recursion / heap / host-I/O.

- [x] **1.5.1 Reject the implementation init body.** In `check_implementation_unit`, when
  `impl.is_device` and `impl.init_body` is non-empty, `self.error("initializer code is not
  available in a DEVICE UNIT", …)`.
- [x] **1.5.2 Reject the interface init block.** Using the 1.1.2 indicator (`has_init`) — or, if
  you chose parse-time rejection, in `parse_interface_unit` (`parser.py:137`) when `is_device`
  and a `BEGIN` is seen — raise the same "initializer code is not available in a DEVICE UNIT"
  error. **Recommendation:** checker-level for consistency with the other recissions; that needs
  the `has_init` field from 1.1.2.
- [x] **1.5.3 Record it in the recission registry.** Note this ban alongside the others
  (`features.py:_DEVICE_RECISSIONS` comment / `type_checker.py:_check_device_recission`) so the
  recission set's documentation stays truthful. (It is construct-shaped, so it is a checker ban,
  not a feature flag — same as recursion.)
- **Green gate:** a `DEVICE IMPLEMENTATION OF U; … BEGIN … END.` is rejected; a non-device
  implementation with an init body still compiles and runs unchanged.

### 1.6 Parity acceptance

- [x] **1.6.1** Port the device-primes example to the `DEVICE UNIT` shape (interface exporting
  the three routines; implementation with the bodies; **no init block**) and confirm it
  type-checks, lowers, links via the **fixed `USES` path** (`uses-fix.patch` must be applied),
  and runs on the CPU device — producing the same 25 primes. This is the parity milestone.
  Covered by `tests/test_device_unit_primes.py` (temp interface + implementation + `USES`
  program, separate IR generation, clang link, expected-primes output match).

---

## Phase 2 — Beyond `DEVICE MODULE`

These three are real new work (they are *not* free from setting the flag). All gate on
`is_device_module`, so they leave host/vintage and the existing `DEVICE MODULE` device-function
behavior untouched unless you choose to apply them there too.

### 2.1 No compiler-inserted runtime checks in device code

Today the math-overflow check (`mathck`) and friends emit host `fflush`+`abort` calls into
device IR via `emit_runtime_abort` (`runtime_builtins.py:45`, called `:201`); the choke point
for "is this check active" is `check_enabled` (`codegen/base.py`). (Prescription §2.3.A1.)

- [ ] **2.1.1 Disable host-trapping checks in device code.** Make `check_enabled` return
  `False` for `MATHCK`/`RANGECK`/`INDEXCK`/`NILCK`/`STACKCK` when `is_device_module`. Cheapest,
  and matches GPU reality (those traps don't exist there).
- [ ] **2.1.2 (Optional, later) device trap instead of disable.** If guard rails are wanted
  on-device, emit `llvm.trap()` (NVPTX `trap;`, AMDGPU `s_trap`) instead of the `fflush`+`abort`
  host pair when `is_device_module`. Defer; do 2.1.1 first.
- **Green gate:** a `DEVICE UNIT` (or `DEVICE MODULE`) compiled to `nvptx64` contains **zero**
  `call … @abort` / `@fflush`; host IR byte-identical (the gate is `is_device_module`).

### 2.2 Stop dumping predeclared externs unconditionally

`_register_predeclared_externs` (`codegen/base.py:217`) adds `fillc/fillsc/movel/mover/
movesl/movesr/memmove/pas_read_int/…` to **every** module at construction, host or device, used
or not — so device IR carries dead host-runtime `declare`s. The seg-bridge intercept
(`_device_seg_bridge`) does **not** use these in device code. (Prescription §2.3.A2.)

- [ ] **2.2.1 Gate or lazily register the dump.** Either (preferred, wider) register these
  externs **on demand** at first reference, or (minimal, green-safe) **skip** the host-runtime
  set when `is_device_module` and the unit lowers to a GPU triple. Start with the gated skip.
- [ ] **2.2.2 Emitted-IR guard test (the durable check).** Compile the device-unit vector-add
  and primes to `nvptx64` and assert the module declares/references **none** of
  `{abort, fflush, memmove, movel, mover, movesl, movesr, fillc, fillsc, pas_read_int,
  pas_read_word, pas_read_real}`. Assert on the **artifact**, not the checker — this catches the
  whole class of leak, not just today's instance.
- **Green gate:** device IR has no host-runtime declares; host IR unchanged.

### 2.3 Emit entry points, not just device functions

A PTX `.func` cannot be launched; only a `.entry` can. The mechanism is verified: setting
`func.calling_convention = "ptx_kernel"` (NVPTX) / `"amdgpu_kernel"` (AMDGPU) on the
`ir.Function` yields a real `.visible .entry`. (Prescription §3; the `DEVICE UNIT` model makes
this clean — see below.)

- [ ] **2.3.1 Define "entry point" = exported routine.** In a `DEVICE UNIT`, the routines the
  **interface exports** are the launchable entries; everything in the implementation that is not
  exported stays a device-internal `.func`. The export list is `InterfaceUnit.params`
  (`UNIT U (add, …)`), already resolved by the checker. **No `[KERNEL]`/`[ENTRY]` annotation is
  needed for the `DEVICE UNIT` path** — this is the payoff of choosing units. (Keep the
  annotation route in mind only for a single-file `DEVICE MODULE`, where there is no interface.)
- [ ] **2.3.2 Set the kernel calling convention on entries.** In `codegen_proc_decl`
  (`codegen/decls.py:381`), when lowering an implementation routine that is **exported by its
  device interface** and the unit lowers to a GPU triple, set `func.calling_convention =
  "ptx_kernel"` / `"amdgpu_kernel"` (chosen off `self.device_triple`). Optionally also add the
  `nvvm.annotations` metadata entry.
  - **Separate-compilation caveat — read this.** Codegen's `codegen_implementation`
    (`decls.py:93`) reads `unit.interface.decls` into `current_interface_decls`, but
    `unit.interface` is only populated when interface + implementation are parsed **together**.
    When the implementation file is compiled **alone** (the normal case), `unit.interface is
    None` and codegen cannot see the export list. The checker, by contrast, *does* load the
    interface from disk in `check_implementation_unit` (`load_interface`, `:600`). **Recommended
    fix:** have the type checker mark exported routines on the AST during
    `check_implementation_unit` (e.g. set `decl.is_exported_entry = True` on each `ProcDecl` whose
    name is in the loaded interface's export list `InterfaceUnit.params`), and have codegen read
    that flag. This keeps codegen free of disk I/O and works under separate compilation. Do **not**
    rely on `current_interface_decls` being populated in codegen.
- [ ] **2.3.3 Entry-point checker rules.** An exported device routine intended as an entry
  should be a `PROCEDURE` (kernels return via `GLOBAL` pointers), and its parameters
  device-passable (scalars or `ADS(GLOBAL/CONSTANT) OF T`; reject `HOST`-space pointers —
  the dereferenceability invariant half-covers this).
- [ ] **2.3.4 Acceptance.** Compile a device unit exporting one routine to `nvptx64`, emit PTX,
  assert a `.visible .entry <name>` for the exported routine **and** that a non-exported helper
  in the same implementation stays `.func`. With `device=x86` the calling convention is inert
  and the logic still runs serially (CPU correctness check).
- **Green gate:** non-exported device routines still `.func`; `DEVICE MODULE` still emits
  device functions (it has no interface, so nothing becomes an entry — unchanged); host
  unaffected.

---

## Phase 3 — Tests and integration

- [ ] **3.1 Unit tests** for every Phase-1/2 item above (parser acceptance, checker
  accept/reject, codegen IR asserts), mirroring the existing `tests/test_ads_space_*` and
  `tests/test_parser.py` patterns. Where a behavior is single-file, it can be a normal in-process
  test.
- [ ] **3.2 Integration test (multi-file).** The `DEVICE UNIT` flow is inherently multi-file
  (interface + implementation + host program, separately compiled and linked), which an
  in-process unit test cannot reach. Stand up (or extend) an integration-test tier: compile N
  files → link → run → diff stdout. Seed it with: (a) the multi-file `USES` faux-graphics
  example already shipped (`uses-graphics-example.zip`), and (b) the Phase-1.6 device-primes
  `DEVICE UNIT` port. See `docs/cuda-kernel-prescription.md` §1.5.4.
- [ ] **3.3 Full suite green** (`PYTHONPATH=src python3 -m pytest tests/ -q`) and a deliberate
  **golden-compare** confirming host/vintage and `DEVICE MODULE` outputs are byte-identical to
  pre-change.

---

## Final acceptance (definition of done for this checklist)

- [ ] `DEVICE INTERFACE; UNIT … END;` and `DEVICE IMPLEMENTATION OF …;` parse, with `DEVICE`
  contextual and vintage `device`-as-identifier still working.
- [ ] A `DEVICE UNIT` enforces every recission the `DEVICE MODULE` does (host I/O, `NEW`/heap,
  recursion), **plus** the initializer-block ban — via the shared device-context machinery, not
  copied code.
- [ ] A `DEVICE UNIT` lowered to `nvptx64` emits **zero** host-runtime symbol references (no
  inserted `abort`/`fflush`, no dead extern dump) — proven by an emitted-IR guard test.
- [ ] The routines a device interface **exports** lower to PTX `.entry` points; non-exported
  implementation routines stay `.func`.
- [ ] `DEVICE MODULE` is **untouched** and still emits device functions; host/vintage output is
  byte-identical; full suite green.

---

## Appendix — anchor index (re-grep before editing; lines drift)

| Concern | Anchor |
|---|---|
| Unit dispatch / contextual DEVICE | `parser.py:parse_compilation_unit (~66)`, `_at_device_module (~99)` |
| Module/interface/impl parsers | `parser.py:parse_module_unit (~110)`, `parse_interface_unit (~137)`, `parse_implementation_unit (~170)` |
| AST unit nodes | `ast_nodes.py:ModuleUnit (~36, is_device ~41)`, `InterfaceUnit (~56)`, `ImplementationUnit (~64)` |
| Device flag (checker) | `type_checker.py:in_device_module (~89)` |
| Device-context dance (to factor) | `type_checker.py:check_module_unit (~554-570)` |
| Unit checkers | `type_checker.py:check_interface_unit (~574)`, `check_implementation_unit (~594)`, `validate_implementation_against_interface (called ~604)` |
| Recissions | `type_checker.py:_check_device_recission (~148)`, `_detect_device_recursion (~173)`, `_DEVICE_BANNED_IO/_HEAP (~141)` |
| Device flag (codegen) | `codegen/base.py:is_device_module (~101)` |
| Device-gating dance (to factor) | `codegen/decls.py:codegen_module (~71-84)` |
| Unit codegen | `codegen/decls.py:codegen_interface (~87)`, `codegen_implementation (~93)` |
| addrspace map / residence / bridge | `codegen/base.py:_space_addrspace (~170)`, `codegen/decls.py:codegen_var_decl residence (~330)`, `codegen/runtime_builtins.py:_device_seg_bridge (~330)` |
| Runtime-check insertion | `codegen/base.py:check_enabled`, `codegen/runtime_builtins.py:emit_runtime_abort (~45, call ~201)` |
| Extern dump | `codegen/base.py:_register_predeclared_externs (~217)` |
| Entry-point emission | `codegen/decls.py:codegen_proc_decl (~381)` |
| Feature swap / recission registry | `features.py:device_features (~87)`, `_DEVICE_RECISSIONS` |
| Grammar | `docs/ebnf_grammar.md:module_unit (~32)`, `interface_unit (~45)`, `implementation_unit (~55)` |
| `USES` prerequisite | apply `uses-fix.patch` before any multi-file device-unit link/run |
