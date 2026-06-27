# Calling C-ABI Foreign Functions from Host Pascal

Status: report + implementation plan • Phases 0–1 implemented in-tree • Scope: host `MODULE`/`PROGRAM` code • Tested target: GNU/Linux AMD64 (System V) • Intended targets: also Windows AMD64 and ARM Linux (AArch64)

## Implementation status

Phases 0 and 1 of the plan below have shipped:

- **Phase 0 (foreign-ABI guard).** The type checker now rejects by-value aggregate
  parameters and aggregate return types on `EXTERN`/`EXTERNAL` routines, converting the
  former silent miscompile into a clear compile-time error that points at `CONST`/`VAR`.
  A non-fatal warning fires when a bare 16-bit `INTEGER` is used in a foreign signature.
  (`type_checker.py`: `_check_foreign_abi` / `_is_foreign_routine`.)
- **Phase 1 (C-ABI surface).** A `[C]` attribute (with `[CDECL]` as an accepted synonym)
  parses in attribute position, and a set of predeclared fixed-width C type aliases —
  `CCHAR`, `CSHORT`, `CINT`, `CLONG`, `CSIZE_T`, `CDOUBLE`, `CPTR` — let foreign
  declarations spell exact C widths independent of the vintage 16-bit `INTEGER`, with no
  feature flag required. (`parser.py`, `builtins_registry.py::C_ABI_TYPE_ALIASES`,
  `codegen/base.py` alias seeding.)

A scalar/pointer `[C]` extern using the aliases type-checks, lowers, links against a
`clang`-compiled object, and runs correctly today; by-reference (`CONST`/`VAR`) aggregates
work as before. By-value aggregates and variadics remain unsupported and are the subject of
Phases 2–3. Coverage is in `tests/test_c_ffi.py`.

## TL;DR — what's the verdict?

The compiler already has a *foreign-function declaration mechanism* — `EXTERN`/`EXTERNAL`
procedures and functions, lowered to LLVM `declare external` and resolved by `clang`
at link time. For a **carefully chosen subset** of C signatures it works correctly
today: scalar arguments and returns (`int32_t`, `double`, pointers) and any aggregate
passed *by reference*.

It is **not** robust for *arbitrary* C functions. The gap is not in the link mechanism;
it is that the compiler emits LLVM IR types directly and performs **no C ABI lowering**.
LLVM and `llvmlite` do not do that lowering for you — the C ABI rules (struct
classification, `byval`/`sret`, integer extension, varargs) live in a compiler *front
end*, and this project's front end is Pascal, not C. So three classes of call are
broken or impossible right now:

- aggregates passed or returned **by value** — silently produce wrong results;
- **width-sensitive** scalar mappings (`INTEGER` is 16-bit, C `int` is 32-bit) — a latent ABI mismatch;
- **variadic** functions (`printf`-style) — not expressible in the source grammar at all.

This document explains the current state with reproducible evidence, then lays out a
phased plan to reach genuine arbitrary-C-ABI support. If the goal is only "call my own
hand-written C helpers," the existing mechanism plus a few discipline rules (below) is
enough and the plan is optional. If the goal is "bind to an unmodified third-party C
library," the plan's Phase 2 (a per-target aggregate classifier) is the load-bearing
piece.

All measurements here are on GNU/Linux AMD64, the only host we have exercised. The plan
is written to keep two further targets — Windows AMD64 and ARM Linux (AArch64) — reachable
without a redesign: the ABI logic is isolated behind a per-target seam from the start
rather than hard-coded to System V (see "Target portability" below).

---

## What "arbitrary C ABI" actually requires

A function-call ABI is a contract about *where the bits go*: which arguments land in
which registers or stack slots, how aggregates are decomposed, how return values come
back, and how small integers are extended. The contract is *target-specific*: on
GNU/Linux AMD64 it is the **System V AMD64 ABI** (the one detailed below and used for
every measurement in this report); Windows AMD64 uses the **Microsoft x64** convention
(different argument registers, a hard "structs larger than 8 bytes go by hidden pointer"
rule, shadow space); ARM Linux uses **AAPCS64** (eight `x`/`v` registers, its own
aggregate-in-register rules). To call an unmodified C function correctly, the caller must
reproduce *that target's* contract exactly. Using System V as the worked example, the
pieces that matter:

1. **Scalar register classes.** Integers/pointers go in `rdi, rsi, rdx, rcx, r8, r9`;
   floating-point in `xmm0..7`. Mixing matters: a `double` does not consume an integer
   register and vice versa.
2. **Integer width and extension.** Arguments narrower than 32 bits are passed in a
   32-bit slot, sign- or zero-extended (`signext`/`zeroext`). C `int` is exactly 32
   bits; C `long`/pointer are 64.
3. **Aggregate classification.** A struct ≤ 16 bytes is split into one or two
   "eightbytes," each independently classed INTEGER or SSE and passed in the
   corresponding register file. A struct > 16 bytes (or one containing unaligned
   fields) is class MEMORY and passed via a hidden pointer (`byval`). The same logic
   decides returns: small aggregates come back in `rax:rdx`/`xmm0:xmm1`; large ones use
   a caller-allocated hidden first argument (`sret`).
4. **Variadic calls.** `al` must hold the number of vector registers used; the callee
   reads the `va_list` save area accordingly.

LLVM expresses all of this through *typed IR plus parameter attributes* (`byval(T)`,
`sret(T)`, `signext`, `zeroext`, coercion to `i64`/`{i64,i64}`/`double`, and
`var_arg`). A C front end (`clang`) computes the classification and emits those
attributes. A non-C front end that wants C compatibility has to do the same work — there
is no "C ABI mode" switch in LLVM that does it automatically from the high-level types.

---

## Current state: how foreign calls work today

### The declaration surface

The grammar (`docs/ebnf_grammar.md`) admits foreign routines two equivalent ways:

```pascal
{ directive form }
FUNCTION cube(x: INTEGER32): INTEGER32; EXTERN;
PROCEDURE cnoise; EXTERNAL;

{ attribute form }
FUNCTION cube(x: INTEGER32): INTEGER32 [EXTERN];
```

`EXTERN` and `EXTERNAL` are complete synonyms. The parser records the absence of a body
and the directive on the AST node (`ast_nodes.py`: `ProcDecl.directive` /
`FuncDecl.directive`, with `body = None`).

### The lowering path

Codegen turns a body-less routine into an LLVM `declare`. In
`codegen/decls.py::codegen_func_decl` / `codegen_proc_decl`:

- parameter LLVM types come from `types_map.param_llvm_type` — value modes map to the
  scalar/aggregate LLVM type, and the reference modes (`VAR`/`VARS`/`CONST`/`CONSTS`)
  map to a *pointer* to that type;
- the directive (or a `PUBLIC`/`EXTERN`/`EXTERNAL` attribute) sets
  `func.linkage = 'external'`;
- with no body, the function is left as a declaration.

At the call site (`codegen/stmts.py::codegen_proc_call_stmt` and
`codegen/exprs.py::codegen_func_call`) each actual argument is lowered and run through
`types_map.coerce_arg`, which performs pointer bitcasts and integer width/float
adjustments *to the declared LLVM parameter type*, then a plain `builder.call` is
emitted. The LLVM default calling convention (`ccc`) is C, so the link step against a
`clang`-compiled object resolves the symbol by name.

Two conventions are baked in and worth noting:

- **Procedures are emitted as `i32`-returning**, not `void` (a harmless internal
  convention; the result is discarded). Against a C `void` function this is a technical
  return-type mismatch that is benign on x86-64 because the caller ignores `eax`.
- **No parameter attributes are ever attached** for host calls — no `signext`,
  `zeroext`, `byval`, or `sret`. (`byval`/`sret`/`signext`/`zeroext` appear *nowhere* in
  `src/`; the only `var_arg=True` in the tree is the compiler-internal `printf` and
  `pas_write_fmt` declarations used by `WRITE` lowering, in `codegen/base.py`, which are
  not reachable from Pascal source.)

### The scalar type map

This is the contract a programmer must reconcile against C by hand today
(`codegen/types_map.py::llvm_type`):

| Pascal type        | LLVM type        | Natural C counterpart            |
|--------------------|------------------|----------------------------------|
| `INTEGER`          | `i16`            | `int16_t` / `short` — **not** `int` |
| `INTEGER32`        | `i32`            | `int32_t` / `int`                |
| `INTEGER64`        | `i64`            | `int64_t` / `long`               |
| `WORD`             | `i16` (unsigned) | `uint16_t`                       |
| `BOOLEAN`          | `i8`             | `_Bool`/`char` (low bit)         |
| `CHAR`             | `i8`             | `char`                           |
| `REAL` / `REAL64`  | `double`         | `double`                         |
| `REAL32`           | `float`          | `float`                          |
| pointer / `ADRMEM` | `i8*`            | `void *` / `T *`                 |
| `VAR T` parameter  | `T*`             | `T *`                            |
| `STRING(n)`        | `[n+1 x i8]`     | fixed char buffer (see notes)    |
| `ADS(s) OF T`      | `{i8*, i16}`     | (segmented; not a flat C pointer)|

`INTEGER32`/`INTEGER64` require `-f wide-integers`; `REAL32`/`REAL64` require
`-f wide-reals` (always on inside `DEVICE` code).

---

## Empirical findings (reproducible)

Each case below was compiled with `pascal1981`, linked with `clang` against a small C
object plus `libpascalrt.a`, and run on x86-64 Linux (clang 18, llvmlite). The C side is
the reference for "correct."

### Capability matrix

| C signature pattern                          | Works today? | Observed result                              |
|----------------------------------------------|:------------:|----------------------------------------------|
| `int32_t cube(int32_t)`                      | ✅           | `cube(3) = 27` ✓                             |
| `double addd(double,double)`                 | ✅           | `addd(1.5,2.25) = 3.75` ✓                    |
| `int16_t f(int16_t*)` ↔ `VAR x: INTEGER`     | ✅           | correct ✓                                    |
| `int addi(int,int)` mapped to `INTEGER`      | ⚠️ latent    | declared `i16`, mismatches C `i32`; "works" by luck for constants |
| `int f(int)` with negative/dirty bits        | ⚠️ latent    | missing `signext`; intermittently correct    |
| `int32_t sumpt(struct{int32_t x,y})` by value| ❌           | `sumpt({10,32}) = 10` (expected 42)          |
| `struct{int32_t a,b} makepair(int32_t)`      | ❌           | `a=5, b=<garbage>` (expected 5,10)           |
| `struct point*` by reference (`VAR p`)       | ✅           | `42` ✓ — the supported aggregate path        |
| `printf`-style variadic                      | 🚫 impossible| no `...` in the parameter grammar            |
| `void cnoise(void)` ↔ extern `PROCEDURE`     | ⚠️ benign    | runs; IR declares `i32` return, C is `void`  |

### The decisive case: struct by value

```pascal
TYPE point = RECORD x: INTEGER32; y: INTEGER32 END;
FUNCTION sumpt(p: point): INTEGER32; EXTERN;   { pass-by-value }
```

Pascal emits:

```llvm
declare external i32 @"sumpt"(%"POINT" %".1")    ; literal {i32,i32} aggregate
```

`clang`, compiling the *same* C function, lowers the struct per System V:

```llvm
define dso_local i32 @sumpt(i64 %0) ...           ; two i32 fields coalesced into one i64
```

`%POINT` (a first-class aggregate) and `i64` are different ABIs. The call passes the
aggregate in a way the callee does not read, and `sumpt({10,32})` returns `10` instead
of `42`. Struct *return* fails the same way (`makepair` lowers to `i64` on the C side; the
Pascal caller expects a `%PAIR` aggregate and reads back garbage in the second field).

### Why it breaks — root cause

`llvmlite` is a thin binding over LLVM's IR builder. When you write
`ir.FunctionType(i32, [point_struct])`, you get exactly that type in the IR — LLVM
faithfully passes a first-class aggregate using *LLVM's* aggregate convention, which is
deliberately unspecified-for-C and does not match what `clang`'s front end computed for
the C struct. The System V classification (eightbyte INTEGER/SSE/MEMORY, the 16-byte
threshold, `byval`/`sret`, register coercion to `i64`/`{i64,i64}`/`double`/`{double,double}`)
is **front-end work**. Clang does it; this compiler does not. That is the entire gap.

### The working subset, stated precisely

You can call a C function correctly *today* if every one of these holds:

- every argument and the return are scalar (integer, float, or pointer), **and**
- you map widths exactly: `INTEGER32` for C `int`, `INTEGER64` for C `long`/pointer-sized
  ints, `REAL` for `double`, `REAL32` for `float`, never bare `INTEGER` for C `int`; **and**
- any aggregate crosses the boundary **by reference** — declare the C side to take a
  pointer and pass it with a `CONST` (read-only) or `VAR` (writable) parameter, or with
  `adr`/`ADRMEM`; **and**
- the function is **not** variadic.

Prefer `CONST` over `VAR` for the aggregate case unless the callee genuinely writes
through the pointer. Both lower to `T*` (verified: `CONST p: point` emits
`%point*` and links cleanly against C `const struct point *`), but `CONST` is read-only
on the Pascal side and matches the common "pass a struct in, don't mutate it" intent.

Note the by-reference rule is about *both* sides agreeing on a pointer. `CONST`/`VAR`
makes the Pascal caller pass a pointer; it binds correctly only to a C function whose
signature *also* takes a pointer. It does **not** let you call an *unmodified* C function
that takes a struct **by value** — that callee expects the struct coerced into registers
per the target ABI, so a pointer is a *different* mismatch, not a fix. Whenever you
control the C side (or can add a one-line pointer-taking shim) this is a non-issue; the
only case it leaves uncovered is a pre-existing by-value-struct C function you cannot
touch, which has no correct lowering until Phase 2.

Within that envelope the mechanism is solid and link-clean. Outside it, results are
wrong, latent, or unbuildable.

---

## Plan: reaching robust arbitrary C ABI

The work splits into an *interface* problem (how a programmer names a C function and its
C types unambiguously) and a *lowering* problem (emitting ABI-correct IR). They can ship
incrementally; each phase is independently useful and independently testable.

### Design principle: keep the vintage surface untouched

The default dialect must stay byte-for-byte faithful to IBM Pascal 2.0. The opt-in
mechanism for the new by-value behavior is the explicit **`[C]` attribute** introduced in
Phase 1, not a global feature flag. The attribute is local to the one declaration that
needs ABI-correct lowering, and — unlike a flag — it never names a capability that does
not yet exist behind it. (Contrast the abandoned `-f c-ffi` idea: at Phase 0 there is
nothing for such a flag to enable, since the by-value aggregate path is simply wrong on
every target with no correct interpretation, so a flag would only re-arm a silent
miscompile.) Unflagged, unattributed builds and all existing tests are therefore
unaffected, the same way `wide-integers` and `symbolic-enum-io` leave the default build
alone.

### Phase 0 — Diagnostics and discipline (small, high value) — IMPLEMENTED

Goal: stop silent wrong answers for the cases that *look* fine but aren't, before any
ABI engine exists. This is a pure hard-error phase — there is no escape hatch, because
the current by-value aggregate lowering has no scenario in which it is ABI-correct.

- In the type checker, when a routine carries `EXTERN`/`EXTERNAL`, detect by-value
  aggregate parameters and aggregate return types and reject them with a clear
  diagnostic: "by-value aggregate in a foreign routine is not ABI-compatible; pass by
  `CONST`/`VAR` (and declare the C side to take a pointer)." Mark by-value aggregate
  support as available later via the `[C]` attribute (Phase 2). This converts today's
  silent miscompile into a compile-time error.
- Optionally warn when bare `INTEGER` (i16) is used in an `EXTERN` signature, suggesting
  `INTEGER32` for C `int`. Pure ergonomics, but it catches the most common foot-gun.
- Document the working subset (the bullets above) in `README.md` next to the existing
  `EXTERN` description, including the point that by-reference requires the C side to take
  a pointer too.

Touchpoints: `type_checker.py` (extern-signature validation), `README.md`.
Cost: low. Risk: low. Ships independently and needs no new flag.

### Phase 1 — An explicit C-ABI declaration surface — IMPLEMENTED

Goal: let a programmer name C types unambiguously, decoupled from the vintage scalar map,
and mark a routine as "use the C ABI."

Two sub-pieces:

1. A `[C]` (or `CDECL`) attribute on `EXTERN` routines that opts the call/declaration
   into ABI-correct lowering (Phase 2/4). Parser support is trivial — the attribute
   grammar already exists (`attribute_section`); add `C`/`CDECL` to the recognized set in
   the parser and a flag on the AST node.
2. A C-type spelling layer so `int`, `long`, `size_t`, `char`, `double`, `void *` have
   exact, platform-correct meanings independent of `INTEGER`'s 16-bit width. Lowest-risk
   option: a predeclared set of fixed-width aliases registered in
   `builtins_registry.py` (e.g. `CINT = INTEGER32`, `CLONG = INTEGER64`,
   `CSIZE_T = INTEGER64`, `CDOUBLE = REAL`, plus a `CPTR`/`ADRMEM` alias), so no grammar
   change is needed and the type checker already understands them. A richer option (an
   actual `CTYPES` unit) can follow.

Deliverable: `FUNCTION strlen(s: CPTR): CSIZE_T [C]; EXTERN;` parses, type-checks, and —
for scalar/pointer signatures — already lowers correctly even before Phase 2, because
scalars need no classification.

Touchpoints: `parser.py` (attribute keyword), `ast_nodes.py` (flag),
`builtins_registry.py` (C aliases). No new feature flag — the `[C]` attribute is the gate.
Cost: low–medium. Risk: low.

### Phase 2 — Aggregate classifier (the core), behind a per-target seam

Goal: pass and return structs by value correctly; this is what unlocks unmodified
third-party libraries.

Structure the classifier as a small *interface* keyed on the target triple, with one
implementation per supported ABI. The interface is the same everywhere — given an LLVM
aggregate type, return a "coerced signature + per-argument marshalling plan" (which
arguments become `byval` pointers, which are coerced to register-sized pieces, whether
the return uses `sret`). Only the *rules* inside differ by target. This is the seam that
keeps Windows AMD64 and AArch64 reachable: adding a target is adding an implementation,
not touching the call sites.

First implementation — System V AMD64 (our tested target), mirroring `clang`'s
`X86_64ABIInfo`:

- Walk an aggregate's LLVM layout into eightbytes; class each INTEGER, SSE, or MEMORY by
  the SysV merge rules; collapse to MEMORY above 16 bytes or on misalignment.
- From the classification, synthesize the *coerced* IR signature exactly as `clang` does:
  - MEMORY argument → pass a pointer with the `byval(T)` attribute;
  - INTEGER/SSE eightbytes → coerce to `i64`, `{i64,i64}`, `double`, `{double,double}`,
    or mixed, and at the call site pack/unpack the real aggregate to/from the coerced
    form via a stack temporary;
  - MEMORY return → prepend a hidden `sret(T)` pointer argument and read the result back
    from the caller's slot;
  - small-aggregate return → declare the coerced return type and reassemble fields.
- Apply only when the routine is marked `[C]` (Phase 1) so non-foreign calls are
  untouched.

The later target implementations reuse the entire `byval`/`sret`/coercion *machinery*
and the call-site marshalling; they only swap the classification rules — Microsoft x64
("structs over 8 bytes always go by hidden pointer; otherwise one register"; plus shadow
space, which the LLVM backend handles) and AArch64 AAPCS64 (homogeneous float aggregates,
the 16-byte threshold, indirect-result register). Because the differential test harness
below compares against `clang` *for the active triple*, each new target is validated the
same way the first one is.

This is self-contained: a pure function from "LLVM aggregate type + target" to "coerced
signature + per-argument marshalling plan," consumed by `decls.py` (for the `declare`)
and by the two call sites in `stmts.py`/`exprs.py` (for packing/unpacking). It does not
perturb the existing scalar path.

Validation is differential and cheap: for a corpus of struct shapes, compile the C side
with `clang --target=<triple> -emit-llvm` and assert our synthesized signature matches
`clang`'s `define` line (the exact check used as evidence above), then build-and-run for
value equality on any target we can execute. This slots directly into the
`tests/integration/` tier; signature-match checks need only `clang` (which can
cross-emit IR for a non-native triple), so Windows/AArch64 ABI correctness can be guarded
even before we have hardware to run on.

Touchpoints: new `codegen/c_abi.py` (target-dispatched classifier + marshalling), wired
into `decls.py`, `stmts.py`, `exprs.py`. Cost: medium–high for the first target (the
classifier is fiddly but well-specified and finite); low–medium per additional target.
Risk: medium — contained behind `[C]`.

### Phase 3 — Variadic foreign functions

Goal: call `printf`, `open`, etc.

- Grammar: allow a trailing `...` (or a `[VARARGS]` attribute) in a `[C]` `EXTERN`
  parameter list. Lower to `ir.FunctionType(ret, fixed_params, var_arg=True)` — the
  builder already supports this (it's how the internal `printf` is declared).
- Default argument promotions on the variadic tail (C promotes `float`→`double`,
  small ints→`int`); apply them in `coerce_arg` for the variadic positions.
- Variadic register accounting (System V's `al` vector-count, AArch64's separate
  variadic save area, Microsoft x64's spill rules): the LLVM backend handles this per
  target for `var_arg` calls, so no manual work beyond emitting the call against the
  variadic type. Note varargs aggregate passing also flows through the Phase 2 classifier
  on each target.

Touchpoints: `parser.py`, `ast_nodes.py`, `decls.py`, `coerce_arg`. Cost: low–medium.
Risk: low.

### Phase 4 — Scalar extension and return-type fidelity

Goal: close the latent `signext`/`zeroext`/`void` gaps so width-edge cases stop being
luck.

- For `[C]` routines, attach `signext`/`zeroext` to sub-32-bit integer parameters and
  returns per signedness (`INTEGER`/`CHAR` → `signext`, `WORD`/unsigned → `zeroext`).
- Emit `[C]` `EXTERN` procedures as genuine `void`-returning functions rather than the
  internal `i32` convention, so the declaration matches a C `void` exactly.
- Treat `BOOLEAN` ↔ C `_Bool` deliberately (C `_Bool` is one byte, value 0/1; our `i8`
  matches if we guarantee normalization).

Touchpoints: `decls.py`, `coerce_arg`. Cost: low. Risk: low.

### Phasing summary

| Phase | Outcome | Cost | Risk | Independent? | Status |
|-------|---------|------|------|--------------|--------|
| 0 | No more silent wrong answers; docs; no new flag | low | low | yes | done |
| 1 | `[C]` attribute + exact C type names | low–med | low | yes | done |
| 2 | Struct by value/return (per-target classifier, SysV first) | med–high | med | yes | planned |
| 3 | Variadic calls (`printf`) | low–med | low | yes | planned |
| 4 | `signext`/`zeroext`/`void` fidelity | low | low | yes | planned |

A practical first cut is **0 + 1 + 4**, which makes the *scalar/pointer/by-ref* world
correct, safe, and ergonomic without the classifier. Phase 2 is the larger investment, is
only required for by-value aggregates, and is where the per-target work concentrates
(SysV AMD64 first; Windows AMD64 and AArch64 as added implementations behind the same
seam).

---

## Testing strategy

Reuse the differential discipline the project already trusts against the genuine 1981
compiler, but point the oracle at `clang`:

- **Signature-match unit tests.** For each foreign signature, generate the equivalent C,
  compile with `clang -O0 -S -emit-llvm`, and assert our synthesized `declare` matches
  `clang`'s `define` (coercion types and parameter attributes). Catches ABI drift without
  running anything. (`@requires_exe`-free; only needs `clang`.)
- **Build-and-run value tests** in `tests/integration/`, mirroring `test_host_uses.py`:
  materialize a `.pas` caller plus a `.c` callee, link, run, assert output. Cover the
  full matrix — scalars, each struct size class around the 8/16-byte boundaries, mixed
  int/float structs, by-value vs by-ref, struct return, variadic, negative/dirty-bit
  scalars for extension. Run on whatever target the host can execute (GNU/Linux AMD64
  today); the signature-match tier still guards the other targets via cross-emitted IR.
- **Regression guard.** A test asserting that *without* the `[C]` attribute, a by-value
  aggregate `EXTERN` is rejected (Phase 0), so the unsafe path can never silently return.

---

## Alternatives considered

- **Runtime `libffi` instead of compile-time lowering.** `libffi` builds calls
  dynamically and would sidestep the classifier. Rejected as the primary path: it adds a
  runtime dependency the project deliberately avoids, loses static type checking and
  inlining, and is slower. It remains a reasonable escape hatch for *truly* dynamic cases
  (function pointers whose type is known only at runtime) if those ever arise.
- **By-reference only, forever.** Declare aggregates non-passable by value in foreign
  routines and require pointers (Phase 0 alone). This is honest, tiny, and covers a large
  fraction of real C APIs (most take pointers anyway). It is the right answer if "arbitrary
  C ABI" is aspirational rather than required — but it cannot bind a library that takes or
  returns small structs by value (e.g. many math/`complex`/`timeval`-style APIs).
- **Lean on a C front end.** Since `clang` is already a hard dependency, one could imagine
  generating a thin C shim per foreign call and letting `clang` do the ABI work. This is
  effectively automatic-by-reference-wrapping and could be a stop-gap, but it pushes ABI
  knowledge into generated C and complicates the build graph; the in-compiler classifier
  is cleaner and keeps the IR self-describing.

---

## Target portability

GNU/Linux AMD64 (System V) is the only host we have tested, and every measurement in this
report is from it. The plan, however, treats it as the *first* target rather than the
only one, because Windows AMD64 and ARM Linux (AArch64) are intended to follow. Concretely:

- **The ABI logic lives behind one seam.** Phase 2's classifier is dispatched on the
  target triple (`codegen/c_abi.py`), so each ABI is an implementation of a common
  interface — System V AMD64 first, then Microsoft x64 and AAPCS64 — and the call sites in
  `decls.py`/`stmts.py`/`exprs.py` never learn which target they are on. Adding a target
  is adding a classifier, not re-touching codegen.
- **The triple is already threaded through.** The compiler takes `--host-triple`
  (default `x86_64-pc-linux-gnu`) and passes it into codegen, so the dispatch key is in
  hand; no new plumbing is required to select an ABI.
- **Each target is validated the same way.** The differential signature-match tests run
  `clang --target=<triple> -emit-llvm` and compare against our synthesized signature, and
  `clang` cross-emits IR for non-native triples — so Windows/AArch64 ABI correctness can be
  guarded from a Linux AMD64 CI box before we own the hardware to run the build-and-run
  tier there.

What we should *not* do is bake System V constants (the 16-byte threshold, the
INTEGER/SSE eightbyte vocabulary, `rdi…r9`) into shared code; keeping them inside the
SysV implementation is what makes the other two targets a contained addition rather than a
rewrite.

## Scope boundaries and risks

- **Per-target rules still have to be written.** The seam keeps the other targets
  *reachable*, but Microsoft x64 and AAPCS64 each need their own classifier and their own
  test corpus; "structured per-target" is not "free." Until those land, a `[C]` routine
  with by-value aggregates should be rejected on triples whose classifier is not yet
  implemented, rather than silently mislowered.
- **Device code is out of scope.** GPU kernels have their own parameter ABI
  (`ptx_kernel`/`amdgpu_kernel`, address spaces); C-FFI is a host-only concern and should
  be rejected inside `DEVICE MODULE` code, consistent with the existing host/device split.
- **Strings.** `STRING(n)`/`LSTRING(n)` are blank-padded / length-prefixed buffers, not
  NUL-terminated C strings. Passing them to C `char *` needs an explicit convention
  decision (pass `adr` of the buffer; the caller ensures termination). Worth a dedicated
  note when Phase 1 lands.
- **Ownership/lifetime.** Pointers handed to C must outlive the call; the by-ref path
  relies on the Pascal variable's storage, which is fine for synchronous calls but is the
  programmer's responsibility for anything the callee retains.

---

## Appendix: minimal reproductions

Working scalar call (correct today):

```pascal
PROGRAM P(output);
FUNCTION cube(x: INTEGER32): INTEGER32; EXTERN;   { -f wide-integers }
BEGIN WRITELN(cube(3)) END.                         { => 27 }
```
```c
#include <stdint.h>
int32_t cube(int32_t x) { return x*x*x; }
```

Broken by-value struct (wrong today; Phase 2 fixes):

```pascal
PROGRAM P(output);
TYPE point = RECORD x: INTEGER32; y: INTEGER32 END;
FUNCTION sumpt(p: point): INTEGER32; EXTERN;
VAR pt: point;
BEGIN pt.x := 10; pt.y := 32; WRITELN(sumpt(pt)) END.   { prints 10, want 42 }
```

Supported aggregate workaround (correct today; by reference, both sides agree on a
pointer). `CONST` is preferred for read-only; use `VAR` only if the callee writes back:

```pascal
FUNCTION sumpt_ref(CONST p: point): INTEGER32; EXTERN;   { C side takes const point* }
```
```c
int32_t sumpt_ref(const struct point *p) { return p->x + p->y; }   /* => 42 */
```
