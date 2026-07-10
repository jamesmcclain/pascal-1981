# ADS and Multiple Memory Spaces — Reference

Reference for the `ADS` (segmented-address) type and the multiple memory spaces
(address spaces) machinery inside `DEVICE MODULE` code. The design-conversation
material that originally framed this — status-tag legend, core idea and origin,
the v1 constraint envelope, the pre-change codebase survey, the default-space
decision, the explicitly deferred items, the out-of-scope notes, and the
rehydration summary — is archived in `docs/old/ads-design-rationale.md`. The
build sequence lives in `docs/old/ads-implementation-plan.md`.

**Terminology (use consistently):** every `ADS` pointer has two spaces, never
conflated:
- **pointer space** — where the address-holding *variable itself* resides (set
  by a `[SPACE(s)]` residence attribute on the pointer's own declaration).
- **pointee space** — what memory the pointer *addresses*; this is the thing
  LLVM's `addrspace(k)*` encodes (set by `ADS(s) OF T`).

A common kernel pointer has pointer space `LOCAL` (a register/private
variable) and pointee space `GLOBAL` (it addresses global memory). Always say
which one you mean.

## 3. The `SPACE` enum and the tag→addrspace mapping

### 3.1 The enum

**[DECIDED]** A predeclared enumerated type supplies the space constants:

```pascal
TYPE SPACE = (HOST, GLOBAL, SHARED, CONSTANT, LOCAL);
```

- **[DECIDED]** Registered as predeclared identifiers (the `builtins_registry` mold,
  shadowable like `MAXINT`); the `SPACE` surface is meaningful **only inside a `DEVICE MODULE`**
  (§1.2) — the type checker gates it on module kind. Outside device code the only space that
  exists is `HOST` (there is one memory, and it is the host's).
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
per-triple table maps ordinal → LLVM address space. The relevant triple is the **device
triple** for code inside a `DEVICE MODULE` and the **host triple** for host code (§1.2). Device
addrspace numbers are **not** identical to the ordinals (they keep their natural gaps because
device-generic addrspace 0 is deliberately unused — we dropped `GENERIC`):

| `SPACE` member | ordinal | host triple | device triple (GPU) | dereferenceable in |
|----------------|:------:|:-----------:|:-------------:|--------------------|
| `HOST`         | 0      | addrspace 0 | — (deref is an error) | host `MODULE` |
| `GLOBAL`       | 1      | opaque handle | addrspace 1   | `DEVICE MODULE` |
| `SHARED`       | 2      | —           | addrspace 3   | `DEVICE MODULE` |
| `CONSTANT`     | 3      | —           | addrspace 4   | `DEVICE MODULE` |
| `LOCAL`        | 4      | —           | addrspace 5   | `DEVICE MODULE` |

`GLOBAL` is an "opaque handle" in host code because the launcher *holds* a device-buffer
address to hand to a kernel but never dereferences it itself. When the **device triple defaults
to x86** (CPU-device / OpenCL-on-CPU, §1.2), every device-triple column collapses to addrspace 0
— the spaces become no-ops and device code runs correctly on the CPU, with the dereferenceability
discipline still enforced for portability. In **vintage mode** only the `HOST` row exists.

> **[SURVEY-caveat] Naming hazard for the next instance:** AMDGPU calls addrspace-3 the
> "Local Data Share (LDS)" — but in *this* design that space is named **`SHARED`**, and our
> **`LOCAL`** is the per-thread private/scratch space (addrspace 5). Do not let AMDGPU's
> "local" terminology collide with our `LOCAL`. Our names follow the CUDA/NVPTX convention.
>
> **[VERIFIED 2026-06-17]** The full tag→addrspace table is confirmed live against the pinned
> toolchain (`llvmlite 0.47.0`) by emitting a load through each space and reading the mnemonic:
>
> | space | addrspace | NVPTX (sm_70) | AMDGPU (gfx900) |
> |-------|:--------:|---------------|-----------------|
> | `GLOBAL`   | 1 | `ld.global`  | `global_load` |
> | `SHARED`   | 3 | `ld.shared`  | `ds_read` (LDS) |
> | `CONSTANT` | 4 | `ld.const`   | `global_load` (read-only path) |
> | `LOCAL`    | 5 | `ld.local`   | `buffer_load … offen` (scratch) |
>
> NVPTX is a perfect 1:1 (each space → its own instruction). AMDGPU confirms the spaces are
> distinct (global vs. LDS vs. scratch); `CONSTANT` shares the read-only global load path on this
> GFX, which is standard AMDGPU instruction selection, not a mapping error. The integer table
> above is now locked, not pending.
>
> **Pointer form:** the pinned LLVM is opaque-pointer-era, but both typed (`T addrspace(k)*`)
> and opaque (`ptr addrspace(k)`) parse and verify. The compiler uses typed pointers throughout,
> so this design **stays typed** (`T addrspace(k)*`); the §5.3 examples are valid as written.
> The address space rides the pointer either way, so the design is unaffected by an eventual
> opaque migration.

### 3.3 The dereferenceability invariant

**[DECIDED]** Because spaces are concrete and static, a pointee space says *where the pointer
may be dereferenced*, and **module kind (§1.2) provides the scope**. `HOST` is dereferenceable
only in host `MODULE` code; the four device spaces only in `DEVICE MODULE` code. The type
checker enforces both directions: device code dereferencing a `HOST` pointer is a compile error,
and host code dereferencing a `GLOBAL` pointer is a compile error. Because the `DEVICE MODULE`
boundary is lexical, "which world am I in" is answered syntactically — no reachability analysis.
This is the two-worlds (host/device) model encoded as a type rule rather than a convention, and
it is a joint dividend of removing `GENERIC` (no "could be either" space) and of the module split
(a clean, static context). Note it holds even when `device=x86` (CPU-device): the discipline is
enforced as a portability fiction so the same code ports to a real GPU unchanged.

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
- `SPACE` here is **contextual** (special only inside `[ ]`), so it is **not** a globally reserved
  word — a vintage program may still use `space` as an identifier.
- **[VERIFIED 2026-06-17 — implementation caveat]** The `ORIGIN(constant)` precedent above is in
  the *grammar reference* but is **not implemented** in `parse_attribute_item` (which currently
  handles bare keywords only). So `SPACE(constant)` is the **first parameterized attribute** in
  the parser, and attributes are currently `List[str]` — implementing it requires a richer
  attribute representation (an `Attribute` node) and updating the three string-set reader sites.
  See implementation plan Step 2; this is more than "copy `ORIGIN`."
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
bans runtime selectors, so inside a `DEVICE MODULE` the space rides the LLVM pointer type and the
`i16` collapses:

- Device module (GPU device triple): `ADS(GLOBAL) OF REAL` lowers to a **bare** `double addrspace(1)*`.
- Faithful mode: unchanged — `{ptr, i16}` with the segment held at 0 (see §6).

Same surface type, two lowerings, target-selected.

### 5.4 `FILLSC` / `MOVESL` / `MOVESR`

**[SURVEY]** These three are the only `ADS`-consuming builtins
(`builtins_registry.py:66-71`), extern seams to `runtime/fillsc.c`, `movesl.c`, `movesr.c`.
Today the runtime **ignores the segment** and they do nothing their flat siblings
(`FILLC`/`MOVEL`/`MOVER`) don't.

**[DECIDED]**
- **Faithful mode:** keep their existing uninteresting (segment-ignoring) behavior verbatim.
- **Inside a `DEVICE MODULE`:** give them genuine **cross-space block-copy** semantics. Their two
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

