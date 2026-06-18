# ADS and Multiple Memory Spaces — Design Record

**Project:** `pascal1981` — a modern reimplementation of IBM Pascal 2.0 that compiles to
LLVM IR via `llvmlite`, with the long-term goal of also targeting LLVM GPU backends
(NVPTX for Nvidia, AMDGPU for AMD).

**Scope of this document:** the `ADS` (segmented-address) type and the machinery for
**multiple memory spaces** (address spaces) under a future "GPU-extended" dialect. This is
*one slice* of a larger GPU-targeting conversation; see **§9 Out of Scope** for the parts
deliberately excluded here.

**Purpose:** a rehydration brief. Another instance should be able to read this cold and
continue the design without re-deriving anything.

**Terminology (use consistently):** every `ADS` pointer has two spaces, never conflated:
- **pointer space** — where the address-holding *variable itself* resides (set by a
  `[SPACE(s)]` residence attribute on the pointer's own declaration).
- **pointee space** — what memory the pointer *addresses*; this is the thing LLVM's
  `addrspace(k)*` encodes (set by `ADS(s) OF T`).

A common kernel pointer has pointer space `LOCAL` (a register/private variable) and pointee
space `GLOBAL` (it addresses global memory). Always say which one you mean.

**Revision note:** this pass replaced the earlier `GENERIC` space with an explicit `HOST`
space at ordinal 0, **removed `GENERIC` entirely** (to eliminate its runtime fuzziness),
**struck `RESPACE`** (it had no legal operation left), and set the default space to `HOST`.
See §3.1, §3.3, §5.2, §7.

---

## 0. How to read the status tags

Every non-obvious claim is tagged so you can tell ratified decisions from gap-fillers:

- **[DECIDED]** — explicitly resolved in conversation (by the user, or proposed and
  carried forward without objection). Treat as settled unless the user reopens it.
- **[DEFAULT]** — a *reasonable default chosen by the assistant* to make this document
  self-contained, for a question we did **not** explicitly resolve. **Not yet ratified.**
  Flag these for the user before building on them heavily.
- **[SURVEY]** — a factual finding about the *current* codebase (file:line). Verify against
  the tree; line numbers may drift.
- **[DEFERRED]** — explicitly postponed; in scope eventually, out of scope for v1.

---

## 1. The core idea

**[DECIDED]** A segmented address is structurally `{offset, selector}` — a pointer plus a
number that says *which memory this points into*. That is exactly the shape an address
space needs. So instead of deleting the vintage segmented-address machinery as a dead
real-mode artifact, we **reinterpret the segment word as an address-space tag** when the
"emit GPU code" target bit is flipped.

- Faithful (vintage / host) mode: the selector means an 8086 physical segment (and in
  practice is degenerate; see §6).
- GPU-extended mode: the selector means a *memory space* — host, global, shared, constant,
  local.

Same surface type, **target-parametric interpretation**. This mirrors how the dialect
already treats numeric width as a dialect-controlled knob.

**Origin note (for rehydration):** the segment→space reinterpretation was the *user's*
insight. The assistant had initially (and wrongly) proposed rescinding `ADS`/`ADSMEM`
wholesale. Do not re-propose deleting it.

### 1.1 near/far ↔ implicit/explicit space

**[DECIDED]** The vintage near/far pointer distinction maps cleanly onto space handling:

- `ADR` (near pointer, offset only) = a pointer whose pointee space is **implicit**, inferred
  from the operand's pointer space. "The default/ambient space."
- `ADS` (far pointer, carries the selector) = a pointer that **names its pointee space
  explicitly**.

So `ADR` is the convenient inferred-space form; `ADS(space)` is the explicit form.

---

## 2. The constraint envelope for v1

The user deliberately narrowed the near-term design. These constraints are what make the
rest of the spec small:

- **[DECIDED] Static spaces only.** An address space is **compile-time** information. There
  is no runtime-variable space tag in v1, and **no `GENERIC`/flat space is exposed at all**
  (its only value was the runtime-resolved case we are deliberately excluding).
- **[DECIDED] No mixing.** A pointer/reference is monomorphic in its space. Space is part of
  type identity; differing spaces are incompatible types and do **not** implicitly convert.
- **[DECIDED] Fully aware.** Every space is traceable to an explicitly spelled token. No
  hidden coercions, no silent upcasts.
- **[DECIDED] Cross-space happens only by data copy.** With no `GENERIC`, there is no legal
  `addrspacecast` to expose (concrete→concrete is physically meaningless on these targets).
  So two spaces meet only through an explicit *data movement* — the `MOVESL`/`MOVESR` bridge
  primitives (§5.4) or a host-orchestrated transfer — never a pointer reinterpretation. This
  is why `RESPACE` was struck (§5.2).
- **[DECIDED] Dereferenceability invariant.** A pointee space determines *which processor may
  dereference the pointer*: `HOST` → CPU (host) code only; `GLOBAL`/`SHARED`/`CONSTANT`/
  `LOCAL` → GPU (device) code only. The type checker enforces it, so host code cannot
  dereference device memory and a kernel cannot dereference host memory. This bakes the
  two-worlds (host/device) model into the type system. (See §3.3.)
- **[DEFERRED]** The near/far parameter *monomorphization* lattice discussed earlier (§8).

Consequence: because LLVM encodes the address space in the pointer *type*
(`addrspace(k)*`), and we require it static, the address space lives entirely in the type
system. This fits the dialect's existing strict type-equality discipline.

---

## 3. The `SPACE` enum and the tag→addrspace mapping

### 3.1 The enum

**[DECIDED]** A predeclared enumerated type supplies the space constants:

```pascal
TYPE SPACE = (HOST, GLOBAL, SHARED, CONSTANT, LOCAL);
```

- **[DECIDED]** Registered as predeclared identifiers (the `builtins_registry` mold,
  shadowable like `MAXINT`), **only under the GPU-extended dialect**. In vintage mode the only
  member that exists is `HOST` (there is one memory, and it is the host's).
- **[DECIDED]** `HOST` is ordinal 0. This is intentional and better than the earlier
  `GENERIC`=0: the degenerate always-zero segment (§6) now denotes `HOST`, which is *exactly
  correct* for vintage code (the sole memory is host memory), so existing `ADS` code is
  forward-compatible with no reinterpretation, **and** `HOST` maps to host-target address
  space 0. Nothing about ordinal 0 is fuzzy anymore.
- **[DECIDED]** `GENERIC` is **not** in the enum. It was removed to eliminate runtime
  resolution; see §2. (If a genuine dynamic-pointer need ever appears, it would be re-added at
  a higher ordinal mapping to device addrspace 0, clearly marked as the one fuzzy space — but
  not by default.)
- **[DEFAULT]** The exact membership `(HOST, GLOBAL, SHARED, CONSTANT, LOCAL)` — five spaces.
  Covers host plus the common NVPTX/AMDGPU device spaces a kernel author touches. Adding
  region/GDS or param spaces later is possible.

### 3.2 The mapping table (target-parametric)

**[DECIDED that there is a target-parametric table; the exact device numbers are [DEFAULT]
pending a toolchain check]**

Pascal enums are dense (ordinals 0..4), so the **enum ordinal is the in-language tag**, and a
per-target table maps ordinal → LLVM address space. Device addrspace numbers are **not**
identical to the ordinals (they keep their natural gaps because device-generic addrspace 0 is
deliberately unused — we dropped `GENERIC`):

| `SPACE` member | ordinal | host target | device target | dereferenceable in |
|----------------|:------:|:-----------:|:-------------:|--------------------|
| `HOST`         | 0      | addrspace 0 | — (deref is an error) | host code |
| `GLOBAL`       | 1      | opaque handle | addrspace 1   | device code |
| `SHARED`       | 2      | —           | addrspace 3   | device code |
| `CONSTANT`     | 3      | —           | addrspace 4   | device code |
| `LOCAL`        | 4      | —           | addrspace 5   | device code |

`GLOBAL` is an "opaque handle" in host code because the launcher *holds* a device-buffer
address to hand to a kernel but never dereferences it itself. In **vintage mode** only the
`HOST` row exists and everything is addrspace 0 — faithful to today.

> **[SURVEY-caveat] Naming hazard for the next instance:** AMDGPU calls addrspace-3 the
> "Local Data Share (LDS)" — but in *this* design that space is named **`SHARED`**, and our
> **`LOCAL`** is the per-thread private/scratch space (addrspace 5). Do not let AMDGPU's
> "local" terminology collide with our `LOCAL`. Our names follow the CUDA/NVPTX convention.
>
> **[DEFAULT-caveat]** The exact device addrspace integers are the standard, stable values for
> these backends but should be confirmed against the targeted LLVM version's datalayout before
> being written into the grammar reference.

### 3.3 The dereferenceability invariant

**[DECIDED]** Because spaces are concrete and static, a pointee space says *which processor
may dereference the pointer*. `HOST` is dereferenceable only in host (CPU) code; the four
device spaces only in device (GPU) code. The type checker enforces both directions: a kernel
dereferencing a `HOST` pointer is a compile error, and host code dereferencing a `GLOBAL`
pointer is a compile error. This is the two-worlds (host/device) model encoded as a type rule
rather than a convention, and it is a direct dividend of removing `GENERIC` — there is no
longer any "could be either" space to blur the line.

---

## 4. Grammar

Two existing productions are each extended by exactly one alternative. **No new top-level
forms, no new globally-reserved keywords.**

### 4.1 Rail 1 — residence qualifier (storage location)

**[VERIFIED 2026-06-17 — with corrections]** `attribute_section` (grammar
`ebnf_grammar.md`, defined ~line 147) is a bracketed, comma-separated storage-attribute list
already carrying `READONLY`, `PUBLIC`, `STATIC`, and the parameterized `ORIGIN(constant)`.
**Correcting the earlier survey:** it attaches to **variables** (`var_item`, grammar line 125;
parser `parse_attribute_section_optional` called at `parser.py:257`) and to **procedure /
function headers** (`proc_decl_header`/`func_decl_header`, grammar lines 196/209; parser
337/349) — *not* to record fields or parameters. The earlier draft mis-cited lines 196/209 as
"fields" and "parameters"; they are the proc/func headers. Confirmed absent from:
- **record fields** — `field_decl = identifier_list ":" type` (grammar line 396): no attribute slot.
- **parameters** — `parameter_group = [ VAR|CONST|VARS|CONSTS ] identifier_list ":" type`
  (grammar line 214); `parser.py:parse_parameter_group` (361) never calls the attribute parser.

Consequences for the design (small, mostly good):
- `[SPACE(...)]` on a **variable** works as planned — that is the main case (declaring where a
  buffer lives). **[VERIFIED]**
- A **record** therefore cannot carry a per-field space, *by construction* — see the simplified
  §5.5. This is the outcome we wanted anyway, now free.
- Putting a **pointer space (residence) on a parameter** is *not* free: it needs a grammar add
  (`[ attribute_section ]` on `parameter_group`). But the common case — a parameter that
  *points into* a given space — rides Rail 2 (the param's type is `ADS(GLOBAL) OF T`), which
  works today. So only the rarer "where does the parameter variable itself live" needs new
  grammar, and that is deferred with the parameter model (§8).
- **Bonus finding:** because proc/func headers already carry `attribute_section`, the deferred
  `KERNEL` marker (§9) has a ready home as a header attribute — in addition to the
  trailing-directive slot beside `EXTERN`/`EXTERNAL`/`FORWARD`.

**[DECIDED]** Add a residence attribute as a sibling of `ORIGIN` (`ORIGIN(addr)` binds
storage to an absolute address; this binds storage to an address *space*):

```ebnf
attribute_item = ... | "ORIGIN" "(" constant ")"
                     | "SPACE" "(" constant ")" ;   (* new: residence of storage *)
```

```pascal
VAR [SPACE(GLOBAL)] g: ARRAY[0..255] OF REAL;
```

- The `constant` must fold to a `SPACE` member.
- `SPACE` here is **contextual** (special only inside `[ ]`, exactly like `ORIGIN`), so it is
  **not** a globally reserved word.
- The bracket syntax is deliberately *loud*: it cannot be applied by accident, which is how
  the "fully aware" constraint is expressed in syntax rather than policy.

### 4.2 Rail 2 — pointee space (the pointer type)

**[SURVEY]** `pointer_type` (grammar lines ~398–400) is currently:

```ebnf
pointer_type = "^" type | "ADR" "OF" type | "ADS" "OF" type ;
```

**[DECIDED]** Add a parameterized `ADS`, parallel to how `STRING(n)`/`LSTRING(n)` already
parameterize a type keyword in parens:

```ebnf
pointer_type = "^" type
             | "ADR" "OF" type
             | "ADS" "OF" type
             | "ADS" "(" constant ")" "OF" type ;   (* new: pointee space *)
```

```pascal
TYPE GReal = ADS(GLOBAL) OF REAL;   { pointer INTO global space }
```

- The `constant` folds to a `SPACE` member (parsed like `STRING`'s `n`, which may be a named
  const).
- This is the qualifier that maps directly onto LLVM `addrspace(k)*`.

### 4.3 Pointer space vs pointee space, never conflated

**[DECIDED]** The two spaces (see the Terminology note up top) are spelled separately:

- **pointer space** — where the pointer variable itself resides → a `[SPACE(...)]` residence
  attribute on the pointer's own declaration (Rail 1).
- **pointee space** — what the pointer addresses → the `ADS(...) OF` on its type (Rail 2).

They are drawn from the same `SPACE` lattice but are independent: e.g. a kernel-local pointer
into global memory has pointer space `LOCAL` and pointee space `GLOBAL`.

**[DEFAULT]** Most v1 users leave the pointer space at the default (`HOST`; see §7) and only
spell the pointee space.

### 4.4 The `ADS x` / `ADR x` expression forms

**[SURVEY]** `unary` address forms exist at grammar lines ~324–325 (`ADR identifier`,
`ADS identifier`).

- **[DECIDED] `ADS x` reads its operand's pointer space:** the result is an `ADS(S) OF T`
  where `S` is `x`'s `[SPACE(...)]` residence (or `HOST` if unannotated, per §7) and `T` is
  `x`'s type. The pointee space is inferred from the operand, not respelled at the `ADS` site.
  Because the default residence is `HOST` and there is no fuzzy `GENERIC`, this inference is
  always a single, statically-known concrete space.
- **[DECIDED] `ADR x`** is the near/implicit-space form: a pointer into `x`'s residence with
  the pointee space left implicit.
- **[DECIDED]** If `ADS x` is assigned into a slot typed `ADS(S2) OF T` with `S2 ≠ x`'s
  pointer space, that is a **type error** (no mixing). There is no cast to rescue it; you must
  move the data (§5.4) or declare `x` in the intended space to begin with.

---

## 5. Type rules and lowering

### 5.1 Space is part of type identity

**[VERIFIED 2026-06-17]** Pointer-type identity is decided by `PointerType.equivalent_to`
(`type_system.py:263-267`) — a single per-class method, so adding space is localized to one
edit. It currently reads:

```python
return self.flavor == other.flavor or self.flavor == 'POINTER' or other.flavor == 'POINTER'
```

**Wrinkle to respect:** the `'POINTER'` arms are a *wildcard* — a plain `^T` heap pointer is
equivalent to any flavor. The `space` field lives only on `ADS` (and `ADR`) pointers; plain
`^T` is spaceless (implicitly `HOST`). So the extension is **not** a blanket "also compare
space"; it is: *when both sides are `ADS`, additionally require equal space; leave the
`POINTER` wildcard intact.* Result: `ADS(GLOBAL) OF REAL` and `ADS(SHARED) OF REAL` are
distinct, incompatible types; a bare `^REAL` still matches flexibly as today.

**[DECIDED]** This is where the rescinded implicit coercions go (see §6.3): a cross-space
assignment is a **type error**, not a silent bitcast/segment-drop.

### 5.2 `RESPACE` — struck from the design

**[DECIDED]** There is **no** explicit space-change operator. `RESPACE` would have lowered to
`addrspacecast`, but `addrspacecast` is only legal between a processor's *generic* space and
its concrete spaces — and we removed `GENERIC` (§2, §3.1). Concrete→concrete
(`GLOBAL`→`SHARED`) is physically meaningless on these targets. With nothing legal left to
cast, `RESPACE` has no operation to perform and is dropped.

Crossing spaces is therefore always a **data movement**, never a pointer reinterpretation:
- on-device staging between concrete spaces → the `MOVESL`/`MOVESR` bridge primitives (§5.4);
- host↔device → a host-orchestrated transfer (a DMA/`memcpy`-equivalent), which is what that
  boundary physically is anyway.

*(If a future need forces a dynamic pointer back into the design, re-introducing a single
marked `GENERIC` device space would also bring back a narrowly-scoped concrete↔generic cast.
Not in v1.)*

### 5.3 Lowering: the runtime `i16` disappears in GPU mode

**[DECIDED]** The `{ptr, i16}` ADS struct existed only to carry a *runtime* selector. v1
bans runtime selectors, so in GPU-extended mode the space rides the LLVM pointer type and the
`i16` collapses:

- GPU-extended mode: `ADS(GLOBAL) OF REAL` lowers to a **bare** `double addrspace(1)*`.
- Faithful mode: unchanged — `{ptr, i16}` with the segment held at 0 (see §6).

Same surface type, two lowerings, target-selected.

### 5.4 `FILLSC` / `MOVESL` / `MOVESR`

**[SURVEY]** These three are the only `ADS`-consuming builtins
(`builtins_registry.py:66-71`), extern seams to `runtime/fillsc.c`, `movesl.c`, `movesr.c`.
Today the runtime **ignores the segment** and they do nothing their flat siblings
(`FILLC`/`MOVEL`/`MOVER`) don't.

**[DECIDED]**
- **Faithful mode:** keep their existing uninteresting (segment-ignoring) behavior verbatim.
- **GPU-extended mode:** give them genuine **cross-space block-copy** semantics. Their two
  `ADSMEM` parameters may carry **different** concrete spaces, e.g.
  `MOVESL(dst: ADS(SHARED) OF CHAR; src: ADS(GLOBAL) OF CHAR; len)`.
- These primitives are the **sanctioned on-device cross-space bridge**: under "no mixing,"
  they are the only place two concrete device spaces legally meet — because bridging spaces is
  the primitive's entire job (e.g. staging `GLOBAL`→`SHARED`). (Host↔device crossing is a
  separate, host-orchestrated transfer; see §5.2.)

### 5.5 Records and `NIL`

- **[VERIFIED 2026-06-17 — supersedes earlier plan]** A record field **cannot** carry
  `[SPACE(...)]` at all: `field_decl` has no attribute slot (grammar line 396), so it will not
  even parse. The earlier "let it parse, reject in the checker" plan is therefore unnecessary —
  there is nothing to reject. A record's space is the residence of the whole record *variable*
  (set via `var_item`'s `[SPACE(...)]`); fields have no independent space, by construction. This
  is exactly the intended semantics (a record resides in one space as a unit), now obtained for
  free with no checker rule.
- **[DEFAULT]** `NIL` defaults to a `HOST` null (`ADS(HOST) OF T`). A concrete-space null is
  just a `NIL`-valued pointer of that space's static type (e.g. `ADS(GLOBAL) OF T`); no cast
  is involved, since the space is fixed by the declared type.

---

## 6. Survey of the current `ADS` surface (pre-change baseline)

**[SURVEY]** Cataloged so the next instance can find every touch point. Line numbers may
drift.

> **Foundation verification pass — 2026-06-17.** The load-bearing survey claims were
> re-checked against the tree (re-extracted from `goto.zip`). Results: the segment is **dead
> end-to-end** as claimed — `ADS` lowers to `{ptr, i16}` (`types_map.py:117`), `ADS x` emits a
> literal seg=0 (`exprs.py:73`), the three `coerce_arg` segment rules are exactly as described
> (`types_map.py:217-228`), far param modes degenerate to plain pointers (`types_map.py:144-150`),
> and `runtime/{movesl,movesr,fillsc}.c` each document ignoring the segment. `pointer_type`
> shape and the `STRING(n)` parameterization precedent are confirmed (grammar 398-400, 440-441).
> llvmlite emits `addrspace(k)*` and accepts a custom triple (spike passed). **Two
> corrections** were folded in: the attribute_section attachment points (§4.1 — not fields/params)
> and the `equivalent_to` wildcard wrinkle (§5.1). Everything else verified as written.

**Carrier syntax (keep):**
- `lexer.py:63` — `ADS` keyword, code `0x005C` (`ADR` is `0x0032`).
- `parser.py:761` — `ADS x` address-of factor.
- `parser.py:958-962` — `ADS OF <type>` → `PointerType(base, 'ADS')`.
- `ast_nodes.py:412`, `type_system.py:257-260` — `flavor` discriminator (`POINTER`/`ADR`/`ADS`).
- `type_checker.py:1747-1752` — `ads var` expr → `PointerType(..., flavor='ADS')`.
- `type_checker.py:2277-2281` — `ADSMEM` named type → `PointerType(CHAR_TYPE, flavor='ADS')`.

**Lowering:**
- `types_map.py:47,63` — `ADSMEM` → `{i8*, i16}`.
- `types_map.py:117` — `ADS` pointer → `{ptr, i16}`.
- `types_map.py:144-150` — far reference param modes `VARS`/`CONSTS`; segment described as
  **degenerate**, lowered to ordinary pointers.
- `types_map.py:213-228` — `coerce_arg` segment reconciliation (see §6.3).
- `types_map.py:507-514`, `exprs.py:201` — RETYPE treats `ADR`/`ADS` factors as pointer values.
- `base.py:188-189` — comment on segmented variants.

**Consumers:** `FILLSC`, `MOVESL`, `MOVESR` (`builtins_registry.py:66-71`).

**Runtime:** `runtime/fillsc.c`, `movesl.c`, `movesr.c` — read only the pointer; **ignore the
segment**.

**Tests:** `test_parser.py:173-178`; `test_typecheck.py:482-483`, `619-644`;
`test_codegen.py:150-220`, `819-821` (ADS lowers with **segment = 0**), `1924-2024`
(end-to-end seg-move ABI vs the C `{char*, unsigned short}` struct).

### 6.1 Key finding

**[SURVEY]** The segment field is **degenerate everywhere today**: `ADS x` always emits
seg=0, `coerce_arg` zeroes/drops it, the far param modes treat it as degenerate, and the C
runtime ignores it. The `i16` tag slot is already plumbed through the type system, lowering,
ABI, and runtime — and always holds zero. **We are not adding a field; we are giving meaning
to a field that already exists and currently carries nothing.**

### 6.2 Rescinded as redundant
**[DECIDED]** In GPU mode the boring segment-ignoring behavior of the three seg builtins is
replaced (§5.4). In faithful mode it stays.

### 6.3 Rescinded as *newly dangerous*
**[DECIDED]** `coerce_arg`'s silent segment rules (`types_map.py:213-228`) become bugs under
the repurpose and must be removed/replaced:
- "flat→seg sets segment 0" *accidentally* stays correct (0 = `HOST`).
- "seg→flat **drops** the segment" now means *silently discarding the address space* →
  becomes a **type error** (there is no cast to fall back on).
- "seg→seg bitcast across tags" → same-space is a no-op; different-space is a **type error**
  (cross-space requires a data copy, §5.2/§5.4, not a reinterpretation).

---

## 7. Default space of unannotated declarations

**[DECIDED]** A declaration with no `[SPACE(...)]` attribute, and a pointer with no explicit
pointee space, default to **`HOST`**. This supersedes an earlier "default everything to
`GENERIC`" idea — `GENERIC` no longer exists. `HOST` is fully static, is the only space in
vintage mode, and matches the always-zero degenerate segment, so vintage code needs no
annotation and behaves exactly as before.

**[DEFERRED — revisit with kernels]** `HOST` is the correct default for host/vintage code, but
it is the *wrong* default *inside a kernel*, where an unannotated local physically lives in
`LOCAL` (private). Since kernels are out of scope here (§9), v1 keeps the default at `HOST`
everywhere. A pleasant fail-safe falls out of this: until the device-side default is decided,
a defaulted (zero-tag) pointer is `HOST`, and the dereferenceability invariant (§3.3) makes it
a compile error to dereference it in device code — so nothing unsafe can slip through. When
kernels land, the device-side default will most likely flip to `LOCAL`.

---

## 8. Explicitly deferred (in scope eventually, not v1)

- **[DEFERRED] Near/far reference-parameter model.** Earlier design sketched: near
  (`VAR`/`CONST`) = a universal-pointer ABI with a cast at the call site; far (`VARS`/`CONSTS`)
  = **space-polymorphic, monomorphized** (cloned per concrete space). **Note for the next
  instance:** the "universal pointer" half relied on `GENERIC`, which is now removed — so
  without re-introducing a marked generic space, this narrows to **per-space monomorphization
  only** (a callee specialized for each concrete space it is called with, no generic
  fallback). The `VARS`/`CONSTS` modes (currently degenerate) are the eventual home for it.
- **[DEFERRED] Re-introducing a dynamic/`GENERIC` space.** Deliberately excluded now. If a
  real dynamic-pointer use-case appears, add a single clearly-marked `GENERIC` device space
  (device addrspace 0) and the narrowly-scoped concrete↔generic cast it enables — but only
  then, and only as a marked escape hatch.
- **[DEFERRED] The four-cell proof-of-static-space lattice** at call boundaries (depends on
  the parameter model above).

---

## 9. Out of scope for this document

These were part of the broader GPU-targeting conversation but are **not** covered here. Noted
so the next instance knows they exist and were discussed:

- Device-sublanguage framing; **reachability-scoped recissions** (host I/O, `NEW`, recursion,
  nonlocal/irreducible `GOTO`) forbidden in kernel-reachable code.
- **Kernel marking** via a trailing `KERNEL` directive (sibling of `EXTERN`/`FORWARD`);
  launch/grid semantics. **[VERIFIED 2026-06-17 — extra home available]** `proc_decl_header`
  and `func_decl_header` already carry an `attribute_section` (grammar 196/209), so `KERNEL`
  could equally be a *header attribute* (`PROCEDURE k(...); [KERNEL]`) instead of, or in
  addition to, a trailing directive in the `EXTERN`/`EXTERNAL`/`FORWARD` slot.
- **Thread/block index intrinsics and barriers** as predeclared builtins.
- A **parallel-iteration statement** (`FORALL`-style).
- **Vector types** (`VECTOR[n] OF T` → `<n x T>`).
- **Width changes** under the GPU umbrella: 16-bit `INTEGER` → 32-bit; `REAL`=f64 → f32, plus
  `REAL32`/`HALF`. (Re-costed because f64 is throttled on GPUs.)
- Target **triple/datalayout** swap and kernel **calling convention** emission.
- The **dialect umbrella / feature-flag** seam in `features.py` (`resolve_features`, the
  `vintage`/`extended` umbrellas) — the natural home for a `gpu` umbrella that bundles all of
  the above. **[DEFAULT]** working name for the gate covering *this* document's contents:
  a `gpu-spaces` feature under a `gpu` dialect umbrella.

---

## 10. One-paragraph rehydration summary

We are reinterpreting the vintage `ADS` segmented-address type so its always-zero segment word
becomes a **static memory-space tag** when a GPU target is selected — the user's insight,
exploiting that the `i16` selector slot is already plumbed end-to-end but carries no
information today. A predeclared `SPACE = (HOST, GLOBAL, SHARED, CONSTANT, LOCAL)` enum supplies
space constants (ordinal → addrspace via a target table). `HOST`=0 is the only space in vintage
mode, matches the degenerate past exactly, and removes all runtime fuzziness; **`GENERIC` is
deliberately not present**, so every space is statically concrete. Each `ADS` pointer has a
**pointer space** (where the pointer variable lives, set by `[SPACE(s)]`) and a **pointee
space** (what it addresses, set by `ADS(s) OF T`), drawn from the same lattice but independent.
Space is part of pointer-type identity: **static only, no mixing, fully explicit**, with a
**dereferenceability invariant** (`HOST`=CPU-only, device spaces=GPU-only) baked into the type
checker. Two one-line grammar extensions carry it — `[SPACE(s)]` (sibling of `ORIGIN`) and
`ADS(s) OF T` (sibling of `STRING(n)`) — with **no new reserved words**. In GPU mode the runtime
`i16` collapses because the space lives in the LLVM pointer type. **There is no `RESPACE`/cast**:
with no `GENERIC`, no `addrspacecast` is legal, so crossing spaces is always a *data movement* —
the repurposed `FILLSC`/`MOVESL`/`MOVESR` bridge primitives on-device, or a host-orchestrated
transfer across the host/device line. The old silent `coerce_arg` segment rules are rescinded
(cross-space is now a type error). The near/far parameter model and any re-introduction of a
dynamic/`GENERIC` space are explicitly deferred.
