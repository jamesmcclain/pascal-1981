# ADS and Multiple Memory Spaces — Design Rationale (archived)

Archived companion to `docs/ads-memory-spaces-design.md`. The reference doc
upstairs records *how the ADS memory-space machinery works* (the enum, the
mapping table, the grammar rails, the type rules); this file holds the
time-bound design-conversation material that originally framed it — the
status-tag legend, the core idea and its origin, the v1 constraint envelope,
the pre-change codebase survey, the default-space decision, the explicitly
deferred items, the out-of-scope notes, and the rehydration summary. The
build sequence itself lives in `docs/old/ads-implementation-plan.md`.

**Project:** `pascal1981` — a modern reimplementation of IBM Pascal 2.0 that
compiles to LLVM IR via `llvmlite`, with the long-term goal of also targeting
LLVM GPU backends (NVPTX for Nvidia, AMDGPU for AMD).

**Scope of this document:** the `ADS` (segmented-address) type and the
machinery for **multiple memory spaces** (address spaces) inside a future
device dialect (the `DEVICE MODULE` of §1.2). This is *one slice* of a larger
GPU-targeting conversation; see §9 Out of Scope for the parts deliberately
excluded here.

**Purpose:** a rehydration brief. Another instance should be able to read this
cold and continue the design without re-deriving anything.

**Terminology (use consistently):** every `ADS` pointer has two spaces, never
conflated:
- **pointer space** — where the address-holding *variable itself* resides (set
  by a `[SPACE(s)]` residence attribute on the pointer's own declaration).
- **pointee space** — what memory the pointer *addresses*; this is the thing
  LLVM's `addrspace(k)*` encodes (set by `ADS(s) OF T`).

A common kernel pointer has pointer space `LOCAL` (a register/private
variable) and pointee space `GLOBAL` (it addresses global memory). Always say
which one you mean.

**Revision note:** this pass replaced the earlier `GENERIC` space with an
explicit `HOST` space at ordinal 0, **removed `GENERIC` entirely** (to
eliminate its runtime fuzziness), **struck `RESPACE`** (it had no legal
operation left), and set the default space to `HOST`. See §3.1, §3.3, §5.2, §7
of the reference doc.

---

## 0. How to read the status tags

Every non-obvious claim is tagged so you can tell ratified decisions from
gap-fillers:

- **[DECIDED]** — explicitly resolved in conversation (by the user, or proposed
  and carried forward without objection). Treat as settled unless the user
  reopens it.
- **[DEFAULT]** — a *reasonable default chosen by the assistant* to make this
  document self-contained, for a question we did **not** explicitly resolve.
  **Not yet ratified.** Flag these for the user before building on them heavily.
- **[SURVEY]** — a factual finding about the *current* codebase (file:line).
  Verify against the tree; line numbers may drift.
- **[DEFERRED]** — explicitly postponed; in scope eventually, out of scope for
  v1.

---

## 1. The core idea

**[DECIDED]** A segmented address is structurally `{offset, selector}` — a
pointer plus a number that says *which memory this points into*. That is
exactly the shape an address space needs. So instead of deleting the vintage
segmented-address machinery as a dead real-mode artifact, we **reinterpret the
segment word as an address-space tag** inside device code (see §1.2).

- Faithful (vintage / host) mode — outside any `DEVICE MODULE`: the selector
  means an 8086 physical segment (and in practice is degenerate; see §6).
- Device mode — inside a `DEVICE MODULE` (§1.2): the selector means a *memory
  space* — host, global, shared, constant, local.

Same surface type, **context-parametric interpretation** (module kind picks
the rules; the device triple picks the lowering). This mirrors how the dialect
already treats numeric width as a dialect-controlled knob.

**Origin note (for rehydration):** the segment→space reinterpretation was the
*user's* insight. The assistant had initially (and wrongly) proposed rescinding
`ADS`/`ADSMEM` wholesale. Do not re-propose deleting it.

### 1.1 near/far ↔ implicit/explicit space

**[DECIDED]** The vintage near/far pointer distinction maps cleanly onto space
handling:

- `ADR` (near pointer, offset only) = a pointer whose pointee space is
  **implicit**, inferred from the operand's pointer space. "The default/ambient
  space."
- `ADS` (far pointer, carries the selector) = a pointer that **names its
  pointee space explicitly**.

So `ADR` is the convenient inferred-space form; `ADS(space)` is the explicit
form.

### 1.2 The host/device split: `DEVICE MODULE` and two triples

**[DECIDED 2026-06-17]** The extended device dialect (the `SPACE` machinery,
address spaces, and the recissions) lives **only inside a `DEVICE MODULE`**.
This supersedes the earlier flat `--target {host,nvptx,amdgpu}` flag with a
cleaner two-axis model:

- **Module kind picks the language rules.** A regular `MODULE` is host code
  (faithful/extended host dialect). A `DEVICE MODULE` is device code (extended
  − recissions + the address-space surface). The boundary is *lexical and
  static*, so "is this device code?" needs no reachability analysis — it is
  simply "is this inside a `DEVICE MODULE`?".
- **Two triples pick the lowering.** There are two compilation targets, `host`
  and `device`, **both defaulting to `x86_64-pc-linux-gnu`**, each independently
  overridable. You override `device` to `nvptx64-nvidia-cuda` /
  `amdgcn-amd-amdhsa` when actually targeting a GPU — but you need not: a
  `DEVICE MODULE` compiled with `device=x86` is device-*dialect* code lowered
  to the *CPU*, where every space collapses to addrspace 0 (the host column of
  §3.2). This is the **OpenCL-on-CPU** case, and it makes the address-space
  discipline a *portability fiction* enforced even on the CPU — correct,
  because it keeps the code portable to a real GPU, and it gives you free CPU
  execution of device modules for development/debugging.

Grammar (augments the existing `module_unit`; keyword modifier, parses the
superset, gated in the checker by module kind):

```ebnf
module_unit = [ include_directive ] [ "DEVICE" ] "MODULE" identifier ";"
              [ uses_clause ] module_block "." ;
```

Consequences (mostly deferred, noted so they are not over-read):
- **Multi-target build.** A program with both module kinds produces *two*
  artifacts — a host object and a device object/PTX — bundled fatbinary-style;
  codegen selects the triple per module. (Implementation: plan Step 4.)
- **Host orchestration surface.** "Device dialect only in `DEVICE MODULE`s"
  governs device *execution*; the host side still needs a thin API to *drive*
  devices — launch a kernel, allocate a device buffer, run the
  host-orchestrated transfer (it holds a `GLOBAL` handle it cannot
  dereference). Deferred with kernels.
- **`uses` is kind-aware.** A host `MODULE` may `uses` a `DEVICE MODULE` (to
  get launchable kernels); a `DEVICE MODULE` may `uses` another `DEVICE
  MODULE` (device libraries); a `DEVICE MODULE` `uses`-ing a host `MODULE` is
  illegal. Deferred.
- **Granularity tradeoff (accepted):** no single function shared as both host
  and device code without living in a shared/duplicated form. Idiomatic for a
  module-organized Pascal, and good discipline; a deliberate v1 constraint.

---

## 2. The constraint envelope for v1

The user deliberately narrowed the near-term design. These constraints are
what make the rest of the spec small:

- **[DECIDED] Static spaces only.** An address space is **compile-time**
  information. There is no runtime-variable space tag in v1, and **no
  `GENERIC`/flat space is exposed at all** (its only value was the
  runtime-resolved case we are deliberately excluding).
- **[DECIDED] No mixing.** A pointer/reference is monomorphic in its space.
  Space is part of type identity; differing spaces are incompatible types and
  do **not** implicitly convert.
- **[DECIDED] Fully aware.** Every space is traceable to an explicitly spelled
  token. No hidden coercions, no silent upcasts.
- **[DECIDED] Cross-space happens only by data copy.** With no `GENERIC`, there
  is no legal `addrspacecast` to expose (concrete→concrete is physically
  meaningless on these targets). So two spaces meet only through an explicit
  *data movement* — the `MOVESL`/`MOVESR` bridge primitives (§5.4) or a
  host-orchestrated transfer — never a pointer reinterpretation. This is why
  `RESPACE` was struck (§5.2).
- **[DECIDED] Dereferenceability invariant.** A pointee space determines
  *where the pointer may be dereferenced*, scoped by **module kind** (§1.2):
  `HOST` → host `MODULE` code only; `GLOBAL`/`SHARED`/`CONSTANT`/`LOCAL` →
  `DEVICE MODULE` code only. The type checker enforces it, so host code cannot
  dereference device memory and device code cannot dereference host memory.
  Because the module boundary is lexical, this needs no reachability analysis.
  This bakes the two-worlds (host/device) model into the type system. (See
  §3.3.)
- **[DEFERRED]** The near/far parameter *monomorphization* lattice discussed
  earlier (§8).

Consequence: because LLVM encodes the address space in the pointer *type*
(`addrspace(k)*`), and we require it static, the address space lives entirely
in the type system. This fits the dialect's existing strict type-equality
discipline.

---

## 6. Survey of the current `ADS` surface (pre-change baseline)

**[SURVEY]** Cataloged so the next instance can find every touch point. Line
numbers may drift.

> **Foundation verification pass — 2026-06-17.** The load-bearing survey claims
> were re-checked against the tree (re-extracted from `goto.zip`). Results: the
> segment is **dead end-to-end** as claimed — `ADS` lowers to `{ptr, i16}`
> (`types_map.py:117`), `ADS x` emits a literal seg=0 (`exprs.py:73`), the
> three `coerce_arg` segment rules are exactly as described
> (`types_map.py:217-228`), far param modes degenerate to plain pointers
> (`types_map.py:144-150`), and `runtime/{movesl,movesr,fillsc}.c` each
> document ignoring the segment. `pointer_type` shape and the `STRING(n)`
> parameterization precedent are confirmed (grammar 398-400, 440-441).
> llvmlite emits `addrspace(k)*` and accepts a custom triple (spike passed).
> **Two corrections** were folded in: the attribute_section attachment points
> (§4.1 — not fields/params) and the `equivalent_to` wildcard wrinkle (§5.1).
> Everything else verified as written.

**Carrier syntax (keep):**
- `lexer.py:63` — `ADS` keyword, code `0x005C` (`ADR` is `0x0032`).
- `parser.py:761` — `ADS x` address-of factor.
- `parser.py:958-962` — `ADS OF <type>` → `PointerType(base, 'ADS')`.
- `ast_nodes.py:412`, `type_system.py:257-260` — `flavor` discriminator
  (`POINTER`/`ADR`/`ADS`).
- `type_checker.py:1747-1752` — `ads var` expr →
  `PointerType(..., flavor='ADS')`.
- `type_checker.py:2277-2281` — `ADSMEM` named type →
  `PointerType(CHAR_TYPE, flavor='ADS')`.

**Lowering:**
- `types_map.py:47,63` — `ADSMEM` → `{i8*, i16}`.
- `types_map.py:117` — `ADS` pointer → `{ptr, i16}`.
- `types_map.py:144-150` — far reference param modes `VARS`/`CONSTS`; segment
  described as **degenerate**, lowered to ordinary pointers.
- `types_map.py:213-228` — `coerce_arg` segment reconciliation (see §6.3).
- `types_map.py:507-514`, `exprs.py:201` — RETYPE treats `ADR`/`ADS` factors
  as pointer values.
- `base.py:188-189` — comment on segmented variants.

**Consumers:** `FILLSC`, `MOVESL`, `MOVESR` (`builtins_registry.py:66-71`).

**Runtime:** `runtime/fillsc.c`, `movesl.c`, `movesr.c` — read only the
pointer; **ignore the segment**.

**Tests:** `test_parser.py:173-178`; `test_typecheck.py:482-483`, `619-644`;
`test_codegen.py:150-220`, `819-821` (ADS lowers with **segment = 0**),
`1924-2024` (end-to-end seg-move ABI vs the C `{char*, unsigned short}`
struct).

### 6.1 Key finding

**[SURVEY]** The segment field is **degenerate everywhere today**: `ADS x`
always emits seg=0, `coerce_arg` zeroes/drops it, the far param modes treat it
as degenerate, and the C runtime ignores it. The `i16` tag slot is already
plumbed through the type system, lowering, ABI, and runtime — and always holds
zero. **We are not adding a field; we are giving meaning to a field that
already exists and currently carries nothing.**

### 6.2 Rescinded as redundant

**[DECIDED]** In GPU mode the boring segment-ignoring behavior of the three
seg builtins is replaced (§5.4). In faithful mode it stays.

### 6.3 Rescinded as *newly dangerous*

**[DECIDED]** `coerce_arg`'s silent segment rules (`types_map.py:213-228`)
become bugs under the repurpose and must be removed/replaced:
- "flat→seg sets segment 0" *accidentally* stays correct (0 = `HOST`).
- "seg→flat **drops** the segment" now means *silently discarding the address
  space* → becomes a **type error** (there is no cast to fall back on).
- "seg→seg bitcast across tags" → same-space is a no-op; different-space is a
  **type error** (cross-space requires a data copy, §5.2/§5.4, not a
  reinterpretation).

---

## 7. Default space of unannotated declarations

**[DECIDED]** A declaration with no `[SPACE(...)]` attribute, and a pointer
with no explicit pointee space, default to **`HOST`**. This supersedes an
earlier "default everything to `GENERIC`" idea — `GENERIC` no longer exists.
`HOST` is fully static, is the only space in vintage mode, and matches the
always-zero degenerate segment, so vintage code needs no annotation and behaves
exactly as before.

**[DEFERRED — revisit with kernels]** `HOST` is the correct default for
host/vintage code, but it is the *wrong* default *inside a kernel*, where an
unannotated local physically lives in `LOCAL` (private). Since kernels are out
of scope here (§9), v1 keeps the default at `HOST` everywhere. A pleasant
fail-safe falls out of this: until the device-side default is decided, a
defaulted (zero-tag) pointer is `HOST`, and the dereferenceability invariant
(§3.3) makes it a compile error to dereference it in device code — so nothing
unsafe can slip through. When kernels land, the device-side default will most
likely flip to `LOCAL`.

---

## 8. Explicitly deferred (in scope eventually, not v1)

- **[DEFERRED] Near/far reference-parameter model.** Earlier design sketched:
  near (`VAR`/`CONST`) = a universal-pointer ABI with a cast at the call site;
  far (`VARS`/`CONSTS`) = **space-polymorphic, monomorphized** (cloned per
  concrete space). **Note for the next instance:** the "universal pointer"
  half relied on `GENERIC`, which is now removed — so without re-introducing a
  marked generic space, this narrows to **per-space monomorphization only** (a
  callee specialized for each concrete space it is called with, no generic
  fallback). The `VARS`/`CONSTS` modes (currently degenerate) are the eventual
  home for it.
- **[DEFERRED] Re-introducing a dynamic/`GENERIC` space.** Deliberately
  excluded now. If a real dynamic-pointer use-case appears, add a single
  clearly-marked `GENERIC` device space (device addrspace 0) and the
  narrowly-scoped concrete↔generic cast it enables — but only then, and only as
  a marked escape hatch.
- **[DEFERRED] The four-cell proof-of-static-space lattice** at call boundaries
  (depends on the parameter model above).

---

## 9. Out of scope for this document

These were part of the broader GPU-targeting conversation but are **not**
covered here. Noted so the next instance knows they exist and were discussed:

- Device-sublanguage framing via **`DEVICE MODULE`** (§1.2). **[DECISION —
  registered 2026-06-17, updated]** The dialect relationship is asymmetric and
  now scoped by module kind, not a target flag: `extended` does *not* imply
  device; a `DEVICE MODULE` *is* the device dialect = **extended minus a
  recission set, plus the address-space surface**. Because the whole module is
  device code, the recissions are **module-scoped** (no reachability analysis
  needed). Candidate recissions (not frozen; decided per-construct by
  implementation cost): recursion (likely drop); set **I/O** and dynamic
  set-range construction (but *keep* the bitvector set core — it is
  GPU-friendly); `NEW`/heap; host I/O; nonlocal/irreducible `GOTO`; general
  pointer-chasing into a flat heap. See the implementation plan's Step 0.5.
- **Host orchestration surface & kind-aware `uses`** (§1.2) — the host-side
  launch/allocate/transfer API and the cross-kind `uses` rules. Deferred.
- **Kernel marking** via a trailing `KERNEL` directive (sibling of
  `EXTERN`/`FORWARD`); launch/grid semantics. **[VERIFIED 2026-06-17 — extra
  home available]** `proc_decl_header` and `func_decl_header` already carry an
  `attribute_section` (grammar 196/209), so `KERNEL` could equally be a
  *header attribute* (`PROCEDURE k(...); [KERNEL]`) instead of, or in addition
  to, a trailing directive in the `EXTERN`/`EXTERNAL`/`FORWARD` slot.
- **Thread/block index intrinsics and barriers** as predeclared builtins.
- A **parallel-iteration statement** (`FORALL`-style).
- **Vector types** (`VECTOR[n] OF T` → `<n x T>`).
- **Width changes** under the GPU umbrella: 16-bit `INTEGER` → 32-bit;
  `REAL`=f64 → f32, plus `REAL32`/`HALF`. (Re-costed because f64 is throttled
  on GPUs.)
- Target **triple/datalayout** swap and kernel **calling convention** emission.
- The **feature-flag seam** in `features.py`. **[VERIFIED 2026-06-17 — real,
  with one structural gap]** `resolve_features(dialect, overrides)` produces a
  flat `Dict[str,bool]` threaded into the type checker (`type_checker.py:100`)
  and codegen (`base.py:81/99`), and into
  `register_builtins(symbol_table, features)` (`type_checker.py:98`) — which is
  the ready hook for conditionally registering the `SPACE` enum and space-aware
  builtins. **But:** (1) only two dialects exist (`vintage` = all-off,
  `extended` = **all-on**), with no general umbrella abstraction; (2) there is
  **no target axis** — the triple is a hardwired constant (`base.py:90`
  `"x86_64-pc-linux-gnu"`); and (3) the **parser/lexer never see features**
  (`Parser.__init__` takes only tokens), so grammar cannot be feature-gated at
  parse time. The existing features (e.g. `readset-set-literal`) follow a
  **parse-the-superset, gate-semantics** pattern, which is the precedent the
  space grammar should follow. Net: the seam is the right mechanism, but the
  address-space work is fundamentally **target**-gated, not just dialect-gated,
  and a target axis must be added. See the implementation-plan sketch (Step 0)
  for the resolution.

---

## 10. One-paragraph rehydration summary

We are reinterpreting the vintage `ADS` segmented-address type so its
always-zero segment word becomes a **static memory-space tag** inside device
code — the user's insight, exploiting that the `i16` selector slot is already
plumbed end-to-end but carries no information today. A predeclared `SPACE =
(HOST, GLOBAL, SHARED, CONSTANT, LOCAL)` enum supplies space constants (ordinal
→ addrspace via a target table). `HOST`=0 is the only space in vintage mode,
matches the degenerate past exactly, and removes all runtime fuzziness;
**`GENERIC` is deliberately not present**, so every space is statically
concrete. Each `ADS` pointer has a **pointer space** (where the pointer
variable lives, set by `[SPACE(s)]`) and a **pointee space** (what it addresses,
set by `ADS(s) OF T`), drawn from the same lattice but independent. Space is
part of pointer-type identity: **static only, no mixing, fully explicit**, with
a **dereferenceability invariant** (`HOST` dereferenceable only in host
modules, device spaces only in device modules) baked into the type checker. The
device dialect lives **only inside a `DEVICE MODULE`** (one new keyword on
`module_unit`); there are **two triples, `host` and `device`, both defaulting to
x86** and independently overridable, so a `DEVICE MODULE` runs on the CPU
(spaces collapse to addrspace 0, OpenCL-style) until you point `device` at a
GPU. Two one-line grammar extensions carry the spaces — `[SPACE(s)]` (sibling
of `ORIGIN`) and `ADS(s) OF T` (sibling of `STRING(n)`) — with **no new reserved
words**. In a device module the runtime `i16` collapses because the space lives
in the LLVM pointer type. **There is no `RESPACE`/cast**: with no `GENERIC`, no
`addrspacecast` is legal, so crossing spaces is always a *data movement* — the
repurposed `FILLSC`/`MOVESL`/`MOVESR` bridge primitives on-device, or a
host-orchestrated transfer across the host/device line. The old silent
`coerce_arg` segment rules are rescinded (cross-space is now a type error). The
near/far parameter model and any re-introduction of a dynamic/`GENERIC` space
are explicitly deferred.
