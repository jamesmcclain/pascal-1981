# ADS Memory Spaces — Implementation Plan (sketch)

**Companion to** `ads-memory-spaces-design.md` (the design record). This is the *build*
sequence: which files change, in what order, gated on what. It opens with the verified
dialect/target seam.

**Status:** sketch. Step 0 is **[VERIFIED]** against the tree (2026-06-17). Steps 1–5 are a
proposed ordering, not yet ratified.

**Guiding principle — the faithful path stays green.** Every step is gated so that the
default `vintage`/host build is byte-for-byte unchanged. Nothing below alters behavior until a
caller opts into the GPU feature *and* a GPU target. Each step is independently testable.

---

## Step 0 — Dialect/target seam (verified, with one decision to make)

**What's there (verified):**
- `features.py`: `resolve_features(dialect, overrides) -> Dict[str,bool]`. Two dialects only:
  `vintage` (all features off) and `extended` (**all features on**). A flat flag dict; no
  general "umbrella" abstraction.
- That dict is threaded into the **type checker** (`type_checker.py:100`), **codegen**
  (`base.py:81`, stored at `:99`, read via a `feature_enabled`-style helper at `:148`), and
  **`register_builtins(symbol_table, features)`** (`type_checker.py:98`) — the latter is the
  ready hook for conditionally registering the `SPACE` enum and space-aware builtins.
- The **target triple is hardwired**: `base.py:90` sets `"x86_64-pc-linux-gnu"`. There is no
  target parameter anywhere.
- The **parser/lexer never receive features** (`Parser.__init__(tokens)` only). Existing
  features gate *semantics*, not *grammar*: the parser accepts the superset (e.g.
  `readset-set-literal`) and the feature controls downstream behavior.

**The structural gap:** the seam models one axis — *language richness* (vintage↔extended) —
but the address-space work is gated on a *second, orthogonal axis*: **target** (host x86 vs.
NVPTX/AMDGPU). `extended` = all-features-on would otherwise silently switch on GPU spaces for
an x86 build, which is wrong.

**Decision (RATIFIED 2026-06-17 — supersedes the earlier `--target` flag):** a **two-axis
model** built on a new module kind.
- **Module kind is the dialect gate.** A regular `MODULE` is host code; a `DEVICE MODULE`
  (one new keyword on `module_unit`) is device code where the extended dialect, the
  address-space surface, and the recissions apply. The boundary is *lexical and static*, so
  "is this device code" needs no reachability analysis. There is **no separate `gpu-spaces`
  feature flag** — module kind subsumes it.
- **Two triples drive lowering: `host` and `device`, both defaulting to `x86_64-pc-linux-gnu`,
  independently overridable.** Override `device` to `nvptx64`/`amdgcn` for a real GPU; leave it
  at x86 to run device modules on the CPU (spaces collapse to addrspace 0 — OpenCL-on-CPU).
  Codegen selects the triple **per module** by kind.
- These compose via the **dereferenceability invariant**: device spaces are dereferenceable
  only inside `DEVICE MODULE`s, `HOST` only outside — so a mis-placed dereference is already a
  type error, no special casing. The device triple just drives the addrspace map; on `device=x86`
  the map is all-zero, which is correct.

*Alternatives considered & rejected:* a flat `--target {host,nvptx,amdgpu}` flag (conflated
"GPU dialect" with "GPU triple" — the module/two-triple split decouples them and gives free
CPU execution of device code); a third `gpu` dialect in `resolve_features` (conflated target
into dialect).

**Build-model consequence:** a program with both module kinds is a **multi-target build** —
host object + device object/PTX, bundled fatbinary-style (plan Step 4).

**Sub-checks (VERIFIED 2026-06-17):**
- *Can `register_builtins` register an enum with named members?* **Yes.** `FILEMODES`
  (`builtins_registry.py:82-91`) is a working template: build `EnumType(['HOST','GLOBAL',…],
  name='SPACE')`, register each member as a `'const'` of that type, register the name as a
  `'type'`. Member ordinals come from list order automatically (`EnumType` docstring). Feature-
  gating inside `register_builtins` already exists (the wide-integers block). `SPACE` is a
  copy-of-FILEMODES addition.
- *Does the constant folder fold an enum member to its ordinal?* **Yes — mechanism is correct,
  with one required wiring step.** `eval_const_expr` (`constfold.py:42`) resolves a bare
  identifier via `self.constants[name]`, and `decls.py:172-175` seeds
  `self.constants[member] = ordinal` for enum *declarations*. **But builtin enums do not
  auto-seed the codegen-side `self.constants`** — only the type-checker symbol table gets them;
  codegen hardcodes its builtin constants by hand (`base.py:111-112`, MAXINT32/64). So Step 1
  must **also seed the `SPACE` ordinals into codegen `self.constants`** (alongside the MAXINT
  precedent), or `ADS(GLOBAL)` type-checks but fails to fold. ~5 lines, required, non-obvious.
  *(Minor footgun: the AST `EnumType` uses `.values`; the type-system `EnumType` uses
  `.members`. Don't mix them.)*

## Step 0.5 — Dialect resolution by module kind (the DEVICE-MODULE = extended−recissions rule)

**[DECISION — registered 2026-06-17, updated to module model]** The dialect is resolved per
module, not from a global flag:
- A regular `MODULE` uses the host dialect (`vintage`/`extended` per `--dialect`, as today).
- A `DEVICE MODULE` uses the **device dialect = `extended_features()` − recission set + the
  address-space surface**, then user overrides. `extended` does *not* imply device; the
  `DEVICE MODULE` keyword *is* what selects the device dialect.
- **Resolution shape (where it lands):** `resolve_features` produces the host baseline; the type
  checker (and codegen) track the **current module kind** and, on entering a `DEVICE MODULE`,
  swap in the device feature set. So the active feature set becomes *module-scoped* — a small but
  real change from today's single global dict. Because the whole module is device code, the
  recissions are **module-scoped** (simpler than reachability analysis from kernel entry points).

**Recission candidate set (NOT frozen — decide per-construct by implementation cost):**
- **Recursion** — likely drop (no real call stack in fabric / tiny GPU stacks).
- **Sets** — keep the **bitvector core** (union/intersect/membership are branch-free bitwise
  ops, GPU-friendly; LLVM legalizes wide integers for free); drop the **runtime-helper-backed**
  parts — dynamic range construction (`['A'..x]`) and set **I/O** (`readset`/writing sets),
  which overlap with the host-I/O recission anyway.
- **`NEW`/heap**, **host I/O** (`WRITE`/`READ`/files — reinterpreted or dropped), **nonlocal/
  irreducible `GOTO`**, **general pointer-chasing into a flat heap** (already constrained by the
  space machinery).

All recissions are **module-scoped** (apply to the whole `DEVICE MODULE`), per the design
record's §1.2/§9. Owner stance: fine *having* or *dropping* any of
these; the set is a candidate list, frozen later per construct.

**Toolchain pin (RATIFIED 2026-06-17): `llvmlite>=0.47.0`.** Pinning llvmlite transitively fixes
the IR textual rules, the bundled LLVM's target set, and the addrspace/datalayout conventions.
Validated empirically against 0.47.0 (bundled LLVM ≈21; 0.48 moves to LLVM 22):
- **All three backends present** in the bundled LLVM — `x86-64`, `nvptx64`, `amdgcn` — so
  IR→PTX/GCN can be emitted *through llvmlite itself*; no external toolchain needed for that step.
- **End-to-end proof:** an `addrspace(1)` store lowered to `st.global.u64` in emitted PTX. The
  space→instruction thesis holds in the pinned toolchain.
- **Pointers stay typed.** 0.47.0 is opaque-pointer-era, but both typed (`T addrspace(k)*`) and
  opaque (`ptr addrspace(k)`) parse + verify. The existing compiler uses typed pointers
  throughout, so the ADS work stays typed — design §5.3 examples remain valid, no opaque
  migration forced. Opaque is available later if the whole compiler migrates.

---

## Step 1 — `PointerType.space` + the `SPACE` enum (types only, no codegen)

- `type_system.py`: add a `space` field to `PointerType` (default `HOST`/`None` for plain
  pointers). Extend `PointerType.equivalent_to` (`:263-267`) per design §5.1 — **ADS↔ADS
  requires equal space; leave the `'POINTER'` wildcard intact** so bare `^T` still matches
  flexibly.
- `builtins_registry.py`: register the predeclared `SPACE = (HOST, GLOBAL, SHARED, CONSTANT,
  LOCAL)` enum (copy the `FILEMODES` pattern, `:82-91`). Registration can be unconditional; the
  *meaning* is gated by module kind in Step 3, so this is harmless outside device code.
- `codegen/base.py`: **also seed the `SPACE` ordinals into `self.constants`** (HOST=0…LOCAL=4),
  alongside the MAXINT32/64 seeding (`:111-112`). Without this the folder can't resolve
  `ADS(GLOBAL)` even though the type checker accepts it (see Step 0 sub-check).
- **Tests:** type-identity (two ADS spaces are inequivalent; `^T` still matches), enum constants
  resolve *and fold* (`eval_const_expr('GLOBAL') == 1`). No IR yet.
- **Green check:** `PointerType.space` defaults so equivalence is unchanged for existing code;
  `SPACE` constants are inert until a `DEVICE MODULE` uses them.

> **Execution-grade note (Steps 2–5).** Edit sites are given **by symbol** with a line anchor in
> parentheses; *re-grep the symbol before editing* — line numbers drift (this whole project has
> proven that). The **green gate** for every step is: the full existing test suite stays green and
> the faithful/host path is unchanged (golden-compare codegen output where noted). Survey-first,
> then edit.

## Step 2 — Grammar + AST: `DEVICE MODULE`, `[SPACE(s)]`, `ADS(s) OF T` (parsed ungated)

**Corrections this step rests on (verified 2026-06-17):**
- `parse_attribute_item` (`parser.py:382`) handles **bare keywords only**, and **`ORIGIN(constant)`
  is *not* implemented** (grammar-doc only). So `SPACE(GLOBAL)` is the **first parameterized
  attribute** — it needs new parsing machinery, not a copy of an existing one.
- Attributes are `List[str]` (`ast_nodes.py:89/114/123`; read as a string-set at
  `type_checker.py:579/683/752`). A parameterized attribute cannot be a bare string.
- To keep vintage green, `DEVICE` and `SPACE` are **contextual keywords** (recognized by the
  parser from `IDENTIFIER` lexemes), **not** lexer-reserved words — a vintage program with a
  variable named `device` or `space` must still compile.

**Data-model edits (`ast_nodes.py`):**
- `ModuleUnit` (`:36`): add `is_device: bool = False`.
- `PointerType` (`:410`): add `space: Optional[Expression] = None` (pointee space constant; `None`
  ⇒ unspecified/HOST).
- New `Attribute` dataclass `{name: str, arg: Optional[Expression] = None}`; change
  `attributes: List[str]` → `List[Attribute]` on `VarDecl` (`:89`), `ProcDecl` (`:114`),
  `FuncDecl` (`:123`).

**Parser edits (`parser.py`):**
- `parse_module_unit` (`:96`) + unit dispatch (`:69`): accept an optional leading contextual
  `DEVICE` before `MODULE`; pass `is_device=True` to `ModuleUnit`.
- `parse_attribute_item` (`:382`): recognize contextual `SPACE`, then `expect('LPAREN')`, parse a
  constant expression, `expect('RPAREN')`, return `Attribute('SPACE', arg)`. Bare keywords return
  `Attribute(kind)`. Change `parse_attribute_item`→`Attribute` and
  `parse_attribute_section_optional` (`:371`)→`List[Attribute]`.
- `parse_type` ADS branch (`:958`): after consuming `ADS`, accept an optional `(constant)` (the
  space) before `expect('OF')`; build `PointerType(base, 'ADS', space=<const>)`.
- `AdsExpr`/`AdrExpr` factor parse (`:761`/`:757`): **unchanged** — the space is inferred from the
  operand in Step 3.

**Acceptance tests (new; mirror `test_parser.py:173`):**
- `DEVICE MODULE Foo; … .` ⇒ `ModuleUnit.is_device is True`; `MODULE Foo;` ⇒ `False`.
- `VAR [SPACE(GLOBAL)] g: REAL;` ⇒ the decl's attributes contain `Attribute('SPACE', <GLOBAL>)`.
- `TYPE p = ADS(GLOBAL) OF REAL;` ⇒ `PointerType.space` carries `GLOBAL`; bare `ADS OF REAL` ⇒
  `space is None`.

**Green gate:** full parser suite passes; **regression test**: a program using `device` and
`space` as ordinary identifiers still parses (contextual-keyword safety).

## Step 3 — Type checker: module-kind context, space binding, dereferenceability

**Edit sites (`type_checker.py`):**
- `check_module_unit` (`:424`) / `check_program_unit` (`:400`): set `self.in_device_module`
  (True only inside a `DEVICE MODULE`; programs + plain modules ⇒ False) and swap in the device
  feature set (Step 0.5) on entry.
- `resolve_type` ASTPointerType branch (`:2368-2371`): when the AST `PointerType.space` is set,
  fold it via the constant folder to a `SPACE` ordinal and attach to the type-system
  `PointerType.space`. **Reject** a spaced `ADS(s)` when `not self.in_device_module`
  ("address spaces require a DEVICE MODULE").
- `AdsExpr` handling (`:1746-1752`): set result `PointerType.space` from the operand symbol's
  residence (its `[SPACE(...)]`), defaulting to HOST (design §4.4).
- Attribute readers (`:579/:683/:752`): update for the `Attribute` dataclass (`a.name.upper()`);
  extract `[SPACE(...)]` residence; reject it outside a device module and on non-applicable decls.
- Deref sites (`:2208-2214` selector loop; `:1830`): enforce the **dereferenceability invariant**
  — dereferencing a HOST-space pointer inside a device module, or a device-space pointer outside
  one, is an error (design §3.3).
- `PointerType.equivalent_to` (`type_system.py:263-267`): ADS↔ADS requires equal `space`; leave
  the `'POINTER'` wildcard intact (no-mixing; design §5.1).

**Acceptance tests (mirror `test_typecheck.py:482/619`):**
- In a `DEVICE MODULE`: `ADS(GLOBAL) OF REAL` accepted; assigning `ADS(GLOBAL)` into an
  `ADS(SHARED)` slot ⇒ error.
- Outside a device module: any `ADS(s)`/`[SPACE(s)]` ⇒ "requires a DEVICE MODULE" error.
- `GLOBAL`-pointer deref in a host module ⇒ error; `HOST`-pointer deref in a device module ⇒ error.
- `ADS x` where `x` is `[SPACE(GLOBAL)]` ⇒ result type `ADS(GLOBAL) OF typeof(x)`.

**Green gate:** full typecheck suite passes; programs with no spaces and no device modules are
unaffected.

## Step 4 — Codegen: per-module triple, addrspace lowering, multi-target build

**Survey-first:** the per-unit codegen dispatch (the codegen counterpart of `check_module_unit`)
is *not* pinned here — re-survey how codegen iterates units before wiring the per-module triple.

**Edit sites:**
- `codegen/base.py` (`:88-90`): the single `ir.Module` + hardwired `x86_64-pc-linux-gnu` triple
  become **per-unit** — host units use the host triple; `DEVICE MODULE` units use the device
  triple. Both kinds present ⇒ two output modules (fatbinary bundling is later; emitting the two
  modules is the Step-4 deliverable).
- `codegen/types_map.py` ADS lowering (`:117`): in a device-triple module, lower `ADS(s) OF T` →
  `ir.PointerType(T, addrspace=map[space])` (typed; validated), collapsing the `{ptr,i16}` pair;
  host modules keep `{ptr,i16}`. With `device=x86`, `map[s] == 0`.
- `coerce_arg` (`:217-228`): remove the silent seg rules (set-seg-0 / drop-seg / cross-tag
  bitcast) → same-space no-op, cross-space ⇒ error (design §6.3).
- `codegen/exprs.py` ADS-x (`:73`): stop emitting literal seg=0; carry the operand's space (device
  module ⇒ the addrspace pointer; host ⇒ unchanged).
- Param lowering (`types_map.py:144-150`): in device modules a spaced ADS param lowers to the
  addrspace pointer (the far modes are currently degenerate).

**Acceptance tests (mirror `test_codegen.py:819/1924`; use the validated emit-and-read harness):**
- Device module, `device=nvptx64`: load through `ADS(GLOBAL) OF i32` ⇒ `ld.global`; `SHARED` ⇒
  `ld.shared`; `CONSTANT` ⇒ `ld.const`; `LOCAL` ⇒ `ld.local`.
- Device module, `device=x86`: same source ⇒ plain addrspace-0 loads.
- Host module: `ADS x` ⇒ `{ptr,i16}` seg=0 — byte-identical to today.

**Green gate:** host-module codegen output is **byte-for-byte unchanged** (golden-file compare
against current output). Device chain already **[VALIDATED 2026-06-17]**: `addrspace(k)` →
space-specific PTX/GCN; §3.2 table locked. Wiring, not discovery.

## Step 5 — `FILLSC`/`MOVESL`/`MOVESR` cross-space bridge (device modules)

**Edit sites:**
- `builtins_registry.py` (`:67/:70/:71`): host registration unchanged; in device modules relax the
  typing so the two `ADSMEM` params may carry **different** concrete spaces (the one sanctioned
  cross-space op — design §5.4).
- `codegen/runtime_builtins.py` `_runtime_fillmove` (`:77/:80`): add a device-module branch — when
  src/dst spaces differ, emit a load-from-src-space / store-to-dst-space loop (or a memcpy with the
  two addrspaces). Host keeps the extern `movesl`/`movesr` call (declared `base.py:207/:210` with
  the `{i8*,i16}` segmented ABI).

**Acceptance tests:**
- Device module: `MOVESL(dst: ADS(SHARED) OF CHAR; src: ADS(GLOBAL) OF CHAR; len)` ⇒ loads from
  `addrspace(1)` and stores to `addrspace(3)` (NVPTX: `ld.global`/`st.shared`).
- Host module: `MOVESL` unchanged — extern call with `{i8*,i16}` args.

**Green gate:** the existing segmented-move regression (`test_codegen.py:1924`) still passes;
host/vintage `FILLSC`/`MOVESL`/`MOVESR` behavior is byte-identical.

---

## Ordering rationale

Types → grammar → checker → codegen → builtins. The cheap, isolated, fully-testable front-end
changes (Steps 1–3) come first and can land without touching codegen. The per-module triple /
addrspace work (Step 4) is last among the core steps; its one external dependency — the device
addrspace integers and datalayout (§3.2) — is now **locked** against the pinned toolchain, so it
is wiring, not discovery. Step 5 is additive and can follow whenever the bridge semantics are
wanted.

## Decisions ratified (2026-06-17)

1. **Module model, not a target flag:** the device dialect lives only inside a `DEVICE MODULE`
   (module kind is the dialect gate); lowering is driven by **two triples, `host` and `device`,
   both defaulting to x86** and independently overridable. `extended` does *not* imply device;
   there is no separate `gpu-spaces` feature. A both-kinds program is a multi-target build.
2. **Toolchain pin:** `llvmlite>=0.47.0`; validated to carry x86/NVPTX/AMDGPU and to lower
   `addrspace` pointers to space-specific PTX/GCN instructions. Pointers stay typed.
3. **Recissions are module-scoped:** a `DEVICE MODULE` is `extended` minus a (not-yet-frozen)
   recission set plus the address-space surface (Step 0.5).

## Verified, nothing open

- **Tag→addrspace table locked (§3.2).** Confirmed live against `llvmlite 0.47.0` by emitting a
  load through each space: NVPTX gives a clean 1:1 (`ld.global`/`ld.shared`/`ld.const`/
  `ld.local` for addrspace 1/3/4/5); AMDGPU confirms distinct spaces (`global_load`/`ds_read`/
  `buffer_load`). No `[VERIFY]` items remain in the design record's core path.

With Step 0 fully settled and validated, the next concrete work is **Step 1** (add
`PointerType.space` + register the `SPACE` enum behind the feature gate) — wiring, not discovery.

---

## Build log — plan vs. reality (discrepancies found during execution)

Things the tree did *not* match the plan/design on, recorded as they were hit so the next
instance doesn't re-trip them. Line numbers below are from the execution pass, not the plan.

**Steps 0 / 0.5 (commit `115c3f0`, 2026-06-17):**
- *Toolchain pin lived in `pyproject.toml` only* (`dependencies = ["llvmlite>=0.40"]`), **not**
  `setup.py` (which carries no llvmlite dep). The plan implied a pin without naming the file;
  there is exactly one site. Bumped to `>=0.47.0`.
- *Steps 0/0.5 are decisions, not code.* The only green-safe artifacts were the pin + an **inert**
  `device_features()` scaffold. The recission set is **empty/NOT FROZEN** (owner decision pending),
  and — confirming the design — the recission candidates are *language constructs*, not entries in
  `_FEATURES`, so they cannot be feature toggles; they land as checker bans in Step 3. No
  `gpu-spaces` flag and no `'device'` dialect added, per the ratified two-axis model.

**Step 1 (commit `b458bfb`):**
- *`CodegenBase` is not standalone-instantiable* — it calls subclass mixin methods
  (`set_llvm_type`, etc.). The concrete entry point is `codegen.Codegen` (a mixin composite,
  `codegen/__init__.py:39`). Tests that need a codegen object must build `Codegen()`, not
  `CodegenBase()`.
- *`SymbolTable.lookup` returns the `Symbol`*; check `.type`/`.kind` on it. Confirmed the
  hand-seed of `SPACE` ordinals into `Codegen.constants` is required (the plan's Step 0 sub-check
  held exactly).

**Step 2 (commit `83af82e`) — the big one:**
- *The attribute-reader edit list was incomplete.* The plan named **three** checker sites
  (`type_checker.py:579/683/752`). Reality: **seven** sites read `attributes`, because four more
  go through `getattr(decl, 'attributes', [])` in **codegen**, which a literal `.attributes` grep
  misses — `codegen/decls.py:313/376/449` (the `attr.upper()` set-comprehensions) plus the
  proc/func reconstruction at `decls.py:136-137`. Changing `List[str]`→`List[Attribute]` breaks
  all of them; every `attr.upper()` becomes `attr.name.upper()`. **Lesson: grep the symbol
  `attributes` *and* `getattr(... 'attributes'`, across `codegen/` too, before this refactor.**
- *`next_kind(offset)` is the lookahead primitive* (`parser.py:28`); there is no `peek`. Used it
  for the contextual `DEVICE`/`MODULE` two-token check.
- *Two existing tests asserted the old `List[str]` attribute shape* and had to move to the
  `Attribute` contract: `test_parser.py::test_confirmed_attributes_parse` and the readonly-local
  codegen test (the latter only failed *through* the codegen reader sites above, not the test
  body). Legitimate contract change, not a regression.
- *Confirmed as written:* `DEVICE`/`SPACE` are absent from the lexer (safe to treat as contextual);
  a bare identifier operand (e.g. `GLOBAL`) folds to an `Identifier` node via `parse_expression`.

**Step 3 (commit `0166f59`):**
- *Recission bans deliberately NOT implemented.* The recission set is still unfrozen (owner
  decision pending), so this step wires the device *context* and the space machinery but enforces
  **zero** construct bans. The `device_features()` swap on entering a `DEVICE MODULE` is in place
  (save/restore around the body), ready to subtract recissions the moment the set is frozen.
- *Residence needed a Symbol field.* `[SPACE(s)]` binds storage to a space, but `Symbol` had
  nowhere to carry it — added `Symbol.space: Optional[int] = None` (`symbol_table.py`). `ADS x`
  reads it back as the result pointee space (`AdsExpr` handling), defaulting to HOST/0.
- *Deref invariant gated to the ADS flavor.* §3.3 is stated for pointee space generally, but
  applying it to every `^`/ADR dereference would prejudge the (unfrozen) heap recission and risk
  the faithful path. `_check_deref_space` fires **only for `flavor == 'ADS'`**, so plain heap
  pointers in host code are byte-identically unaffected. Both deref sites checked
  (`type_checker.py` selector loop + designator path).
- *Folding reuses the Step 1 enum.* `_fold_space` resolves a bare member name against the
  unconditionally-registered `SPACE` `EnumType` in the symbol table (ordinal = `members.index`),
  so no separate space table is needed in the checker. Works in host modules too, but
  `resolve_type`/`check_var_decl` reject any *use* of a space outside a `DEVICE MODULE`.
- *No new imports leaked.* `EnumType` was already imported; `device_features` is imported lazily
  inside `check_module_unit` to keep the module-load graph unchanged.
