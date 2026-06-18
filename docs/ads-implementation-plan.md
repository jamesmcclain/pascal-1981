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

**Decision (RATIFIED 2026-06-17):** add a **target axis** distinct from `--dialect`:
- `--target {host,nvptx,amdgpu}` (default `host`), carried alongside `features` into codegen.
- The **surface feature** `gpu-spaces` (a normal `features.py` entry) enables *parsing/checking*
  of `SPACE`/`ADS(s)` — the language accepting space annotations.
- The **target** selects the *lowering*: the triple, the datalayout, and the tag→addrspace
  numbers (§3.2 of the design record).
- These compose cleanly via the existing **dereferenceability invariant**: on `target=host`,
  only `HOST` is dereferenceable, so any device-space pointer is already a type error — no
  special "feature-on-but-host" handling needed. Target mostly just drives the triple and the
  addrspace map.

*Alternative considered:* a third `gpu` dialect in `resolve_features`. Rejected for now because
it conflates target into dialect; the orthogonal `--target` flag is cleaner and leaves
`vintage`/`extended` meaning exactly what they mean today.

**Follow-on sub-checks (cheap, do when reached):** confirm `register_builtins` can register an
enum type (Step 1) and that the constant folder (`eval_const_expr`) folds an enum member to its
ordinal (Step 3) — the latter is the "proof engine" for static space tags.

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
  LOCAL)` enum, **feature-gated** inside `register_builtins(symbol_table, features)`.
- **Tests:** type-identity (two ADS spaces are inequivalent; `^T` still matches), enum constants
  resolve. No IR yet.
- **Green check:** with the feature off, `SPACE` is absent and `PointerType.space` defaults so
  equivalence is unchanged.

## Step 2 — Grammar: parse `[SPACE(s)]` and `ADS(s) OF T` (ungated)

- `parser.py`: add `SPACE` as an `attribute_item` (in `parse_attribute_item`); add the
  `ADS "(" constant ")" "OF" type` variant to pointer-type parsing (parallel to `STRING(n)`).
  **Parse the superset always** — the parser has no features (Step 0), matching existing
  precedent.
- `ast_nodes.py`: carry the space constant on the attribute node and on `PointerType`.
- **Tests:** parse-level, mirroring the existing `test_parser.py` ADS cases (lines ~173).
- **Green check:** new syntax simply doesn't occur in vintage source, so nothing changes.

## Step 3 — Type checker: bind & enforce spaces (semantics feature-gated)

- `type_checker.py`:
  - feature **off** → reject any space annotation with a clear "gpu-spaces required" error.
  - feature **on** → fold the space constant via `eval_const_expr` (sub-check from Step 0),
    attach it to the `PointerType`; enforce no-mixing through the extended `equivalent_to`;
    apply the **dereferenceability invariant** (device-space deref illegal on `target=host`);
    `ADS x` reads the operand's pointer space (design §4.4); reject `[SPACE(...)]` where it can't
    apply.
- **Tests:** accept/reject pairs, mirroring `test_typecheck.py` ADS cases (lines ~482, ~619).
- **Green check:** feature off → every space annotation is rejected, vintage unaffected.

## Step 4 — Codegen: addrspace lowering (target-gated)

- `codegen/base.py`: replace the hardwired triple (`:90`) with a target-selected triple +
  datalayout.
- `codegen/types_map.py`: on a GPU target, lower `ADS(s) OF T` to `T addrspace(map[s])*` and
  **collapse the i16** (design §5.3; llvmlite capability already spiked); on host, keep
  `{ptr, i16}` exactly as today.
- `codegen/types_map.py:coerce_arg` (`:217-228`): rescind the silent segment rules → space-aware
  (same-space no-op; cross-space is an error, design §6.3).
- `codegen/exprs.py` (`:73`): `ADS x` stops emitting a literal seg=0 and instead carries the
  operand's space / lowers to the right addrspace.
- **Tests:** codegen, mirroring `test_codegen.py` ADS cases (lines ~819, ~1924), asserting the
  `addrspace(k)*` IR on a GPU target and unchanged `{ptr,i16}` on host.
- **Green check:** host target path is the existing path, untouched.
- **[VALIDATED 2026-06-17]** The full chain is proven against the pinned toolchain:
  `ir.PointerType(base, addrspace=1)` → bundled NVPTX backend → `st.global` PTX. This step is
  feasible end-to-end; what remains is wiring, not discovery.

## Step 5 — Repurpose `FILLSC`/`MOVESL`/`MOVESR` as the cross-space bridge (target-gated)

- `builtins_registry.py` / `codegen/runtime_builtins.py`: on a GPU target, the three primitives
  accept differing concrete spaces and lower to genuine cross-space block copies (design §5.4);
  host/vintage keeps the existing segment-ignoring behavior verbatim.
- **Tests:** cross-space copy typing + lowering; vintage behavior regression-locked.

---

## Ordering rationale

Types → grammar → checker → codegen → builtins. The cheap, isolated, fully-testable front-end
changes (Steps 1–3) come first and can land without touching codegen. The target/triple/
addrspace work (Step 4) is last among the core steps because it is the piece most likely to need
the design's one outstanding `[VERIFY]` — the exact device addrspace integers and datalayout
(§3.2) — which should be confirmed against the actual targeted LLVM at that point. Step 5 is
additive and can follow whenever the bridge semantics are wanted.

## Decisions ratified (2026-06-17)

1. **Target axis:** a `--target {host,nvptx,amdgpu}` flag, *not* a third `gpu` dialect.
2. **Toolchain pin:** `llvmlite>=0.47.0`; validated to carry x86/NVPTX/AMDGPU and to lower
   `addrspace` pointers to space-specific PTX instructions. Pointers stay typed.
3. **`extended` does not imply `gpu-spaces`** — `extended` is a host language-richness umbrella,
   not a target switch; the GPU features stay off under it.

## Verified, nothing open

- **Tag→addrspace table locked (§3.2).** Confirmed live against `llvmlite 0.47.0` by emitting a
  load through each space: NVPTX gives a clean 1:1 (`ld.global`/`ld.shared`/`ld.const`/
  `ld.local` for addrspace 1/3/4/5); AMDGPU confirms distinct spaces (`global_load`/`ds_read`/
  `buffer_load`). No `[VERIFY]` items remain in the design record's core path.

With Step 0 fully settled and validated, the next concrete work is **Step 1** (add
`PointerType.space` + register the `SPACE` enum behind the feature gate) — wiring, not discovery.
