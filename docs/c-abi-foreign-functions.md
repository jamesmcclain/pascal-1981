# Calling C-ABI Foreign Functions from Host Pascal

Status: report + implementation plan • Phases 0–4 implemented in-tree • Scope: host `MODULE`/`PROGRAM` code • Tested target: GNU/Linux AMD64 (System V) • Intended targets: also Windows AMD64 and ARM Linux (AArch64)

## Implementation status

Phases 0, 1, and 2 of the plan below have shipped:

- **Phase 0 (foreign-ABI guard).** The type checker rejects by-value aggregate parameters
  and aggregate return types on plain `EXTERN`/`EXTERNAL` routines (no `[C]`), converting
  the former silent miscompile into a clear compile-time error that points at
  `CONST`/`VAR` or the `[C]` marker. A non-fatal warning fires on a bare 16-bit `INTEGER`
  in a foreign signature. (`type_checker.py`: `_check_foreign_abi` / `_is_foreign_routine`.)
- **Phase 1 (C-ABI surface).** A `[C]` attribute (with `[CDECL]` as an accepted synonym)
  parses in attribute position, and predeclared fixed-width C type aliases — `CCHAR`,
  `CSHORT`, `CINT`, `CLONG`, `CSIZE_T`, `CDOUBLE`, `CPTR` — let foreign declarations spell
  exact C widths independent of the vintage 16-bit `INTEGER`. The **whole C-FFI surface is
  gated behind the extended dialect** (see *Dialect gating* below): in the faithful 1981
  dialect the aliases are undeclared and `[C]` is rejected, so a vintage program cannot
  reach a wide C width through a C alias. (`parser.py`,
  `builtins_registry.py::C_ABI_TYPE_ALIASES`, `codegen/base.py`, `type_checker.py`.)
- **Phase 2 (aggregate classifier).** A System V AMD64 classifier reproduces clang's
  eightbyte INTEGER/SSE/MEMORY lowering: small aggregates are coerced into register-sized
  pieces (`i64`, `<2 x float>`, `double`, expanded multi-eightbyte args, …) and large or
  MEMORY-class aggregates use `byval`/`sret`. It is keyed on the host triple behind a
  per-target seam (`c_abi_for_triple`); an unimplemented triple raises rather than
  mislowering. A `[C]` routine that passes or returns a struct **by value** now links and
  runs correctly against an unmodified clang callee — the two cases the original analysis
  showed broken. (`codegen/c_abi.py`, wired into `decls.py`/`stmts.py`/`exprs.py`.)

By-value aggregates were validated differentially against clang across the eightbyte size
classes (1- and 2-eightbyte integer structs, `double`/`<2 x float>` SSE structs, mixed
SSE+INTEGER, and >16-byte MEMORY structs), for both arguments and returns. Coverage is
in `tests/test_c_ffi.py`.
- **Phase 4 (scalar extension and return-type fidelity).** Sub-32-bit scalar parameters
  and return types on `[C]` routines now carry `signext` or `zeroext` LLVM parameter
  attributes, closing the latent dirty-bit gap that caused intermittent wrong answers
  for negative `char`/`short` return values and unsigned `short`/`WORD` arguments.
  Signed narrow types (`INTEGER` i16, `INTEGER8` i8, `CHAR` i8, `CCHAR`, `CSHORT`) get `signext`;
  unsigned/boolean types (`WORD` i16, `WORD8` i8, `BOOLEAN` i8) get `zeroext`.  Attributes appear
  on both the `declare` (so the LLVM backend generates correct register handling) and
  the call-site argument list (via `arg_attrs`).  `[C]` `EXTERN` procedures are now
  declared as `void`-returning rather than the internal `i32` convention, exactly
  matching a C `void` function signature.  (`codegen/c_abi.py`: `CParamPlan.sign_attr`,
  `CCallPlan.ret_sign_attr`, `build_c_abi_plan` sign-attr threading,
  `_c_abi_variadic_promote`; `codegen/decls.py`: `_c_abi_sign_attr` helper,
  `_codegen_c_abi_decl` sign-attr and void-return emission.)
- **Phase 3 (variadic foreign functions).** A `[VARARGS]` attribute (contextual, like
  `[C]`) on a `[C] EXTERN` routine marks the declaration variadic. The compiler emits
  `var_arg=True` in the LLVM `ir.FunctionType`, so the LLVM backend handles all
  platform-specific register accounting (`al` on System V, etc.) automatically. C default
  argument promotions are applied to the variadic tail at each call site: `float`
  (`REAL32`) is widened to `double` via `fpext`; integer types narrower than 32 bits
  (`i1`, `i8`, `i16`) are widened to `i32` via `sext`/`zext`. The `[VARARGS]` surface
  is gated the same way as `[C]`: requires the extended dialect and is rejected in
  `DEVICE` code. The type checker allows any number of call-site arguments ≥ the fixed
  parameter count for variadic routines; fixed-parameter types are still validated.
  Aggregate passing in the variadic tail flows through the existing Phase 2 classifier
  on `[C]` calls. (`parser.py`, `type_checker.py`, `type_system.py`, `codegen/c_abi.py`,
  `codegen/decls.py`.)

### Dialect gating

The C-FFI surface — the `[C]`/`[CDECL]` attribute *and* the `CINT`/`CLONG`/`CSIZE_T`/…
aliases — is available **only under the extended dialect**. The motivation is that the
interface drags in fixed 32/64-bit widths the faithful 1981 dialect does not otherwise
have: `CLONG` is a 64-bit integer wherever it appears, so the thing that names it should
live behind the same door as `INTEGER64` itself. Gating the surface as a unit means the
wide widths and the interface that needs them arrive together, instead of letting `[C]`
smuggle wide types into an otherwise-vintage program. (This also retired an earlier
shortcut where the aliases resolved to wide types *regardless* of `wide-integers`; under
the umbrella `wide-integers` is on, so `CINT → INTEGER32` is now legitimate rather than a
gate bypass.)

The umbrella is read, deliberately, as **"all of `extended_features()` is on"**
(`features.is_extended`). That is the simplest correct reading and keeps the three
touchpoints — alias registration in `builtins_registry`, the alias seed in
`codegen/base.py`, and the `[C]` attribute check in `type_checker._check_foreign_abi` — on
a single predicate so they cannot drift. A finer-grained dedicated `c-ffi` feature (enable
C interop without committing to every other extension) is a possible later refinement; it
would only change `is_extended`'s definition, not its callers. The faithful dialect
rejects `[C]` with an explicit "requires the extended dialect" diagnostic, and the
parser stays dialect-agnostic (it recognizes the attribute syntactically; the checker is
where every dialect gate lives, exactly as for `INTEGER32`/`REAL32`).

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
- **variadic** functions (`printf`-style) — now supported via the `[VARARGS]` attribute (Phase 3).

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
- **No parameter attributes are ever attached** for plain (non-`[C]`) host calls.
  `[C]` routines do carry `signext`/`zeroext`/`byval`/`sret` on their declarations and
  call sites (Phases 2 and 4). (`byval`/`sret`/`signext`/`zeroext` for plain calls
  appear nowhere in `src/`; the only `var_arg=True` in the tree outside `[VARARGS]`
  is the compiler-internal `printf` and `pas_write_fmt` declarations used by `WRITE`
  lowering, in `codegen/base.py`, which are not reachable from Pascal source.)

### The scalar type map

This is the contract a programmer must reconcile against C by hand today
(`codegen/types_map.py::llvm_type`):

| Pascal type        | LLVM type        | Natural C counterpart            |
|--------------------|------------------|----------------------------------|
| `INTEGER`          | `i16`            | `int16_t` / `short` — **not** `int` |
| `INTEGER32`        | `i32`            | `int32_t` / `int`                |
| `INTEGER64`        | `i64`            | `int64_t` / `long`               |
| `WORD`             | `i16` (unsigned) | `uint16_t`                       |
| `WORD8`            | `i8` (unsigned)  | `uint8_t`                        |
| `INTEGER8`         | `i8`             | `int8_t` / `signed char`         |
| `BOOLEAN`          | `i8`             | `_Bool`/`char` (low bit)         |
| `CHAR`             | `i8`             | `char`                           |
| `REAL` / `REAL64`  | `double`         | `double`                         |
| `REAL32`           | `float`          | `float`                          |
| pointer / `ADRMEM` | `i8*`            | `void *` / `T *`                 |
| `VAR T` parameter  | `T*`             | `T *`                            |
| `STRING(n)`        | `[n+1 x i8]`     | fixed char buffer (see notes)    |
| `ADS(s) OF T`      | `{i8*, i16}`     | (segmented; not a flat C pointer)|

`INTEGER8`/`INTEGER32`/`INTEGER64` and `WORD8` require `-f wide-integers`; `REAL32`/`REAL64` require
`-f wide-reals` (always on inside `DEVICE` code).

---

## Empirical findings (reproducible)

Each case below was compiled with `pascal1981`, linked with `clang` against a small C
object plus `libpascalrt.a`, and run on x86-64 Linux (clang 18, llvmlite). The C side is
the reference for "correct."

### Capability matrix

This matrix records the **pre-Phase-2 baseline** that motivated the work; rows marked
"(now fixed in Phase 2)" were resolved by the classifier — see *Implementation status* at
the top.

| C signature pattern                          | Works today? | Observed result                              |
|----------------------------------------------|:------------:|----------------------------------------------|
| `int32_t cube(int32_t)`                      | ✅           | `cube(3) = 27` ✓                             |
| `double addd(double,double)`                 | ✅           | `addd(1.5,2.25) = 3.75` ✓                    |
| `int16_t f(int16_t*)` ↔ `VAR x: INTEGER`     | ✅           | correct ✓                                    |
| `int addi(int,int)` mapped to `INTEGER`      | ⚠️ latent    | declared `i16`, mismatches C `i32`; "works" by luck for constants |
| `int f(int)` with negative/dirty bits        | ✅ `[C]`     | Phase 4: `signext`/`zeroext` on sub-32-bit params/returns |
| `char f(int)` negative return                | ✅ `[C]`     | Phase 4: `signext i8` on return              |
| `int32_t sumpt(struct{int32_t x,y})` by value| ✅ `[C]`     | now fixed in Phase 2: `sumpt({10,32}) = 42` ✓ |
| `struct{int32_t a,b} makepair(int32_t)`      | ✅ `[C]`     | now fixed in Phase 2: `a=5, b=10` ✓          |
| `struct point*` by reference (`VAR p`)       | ✅           | `42` ✓ — the supported aggregate path        |
| `printf`-style variadic (`[VARARGS]`)         | ✅ `[C][VARARGS]` | Phase 3: `[VARARGS]` attribute + default-arg promotions |
| Variadic with integer tail (`i16` promoted)  | ✅ `[C][VARARGS]` | Phase 3: `i8`/`i16` → `i32` sext at call site |
| `void cnoise(void)` ↔ extern `PROCEDURE`     | ✅ `[C]`     | Phase 4: `[C]` procedures declare `void`, not `i32` |

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

### Record layout across the C boundary (guaranteed)

The by-reference rule above rests on a layout guarantee that is now explicit
and pinned by tests: a Pascal `RECORD` whose fields are C-representable
scalars (`CHAR`, `BOOLEAN`, the integer family including `WORD8`/`INTEGER8`,
`REAL`/`REAL32`, the C aliases), pointers (`ADRMEM`/typed pointers), fixed
`ARRAY`s of such, and nested records of such, is laid out **exactly like the
corresponding C struct on the host triple**:

- field offsets follow natural alignment, implicit padding included (records
  lower to non-packed LLVM struct types, so the backend applies the target
  datalayout's ABI alignment — the same rules clang applies to the C struct);
- `SIZEOF` reports the padded size, tail padding included (it is computed from
  the same layout helper the `[C]` aggregate classifier trusts, so `SIZEOF`,
  the allocation size, and the C `sizeof` agree).

This is what makes it sound to transcribe a third-party C struct (a libpng
`png_image`, a `struct timeval`, ...) as a Pascal `RECORD` and pass it by
pointer (`CONST`/`VAR`) to an unmodified C function.  The guarantee is
validated differentially against clang `offsetof`/`sizeof` — including a
mixed-alignment struct, a `png_image`-shaped struct, and nested records with
8-bit fields — in `tests/test_c_record_layout.py`.  Out of scope: Pascal has
no spelling for C bit-fields, unions, or `#pragma pack`ed structs; those still
need a C-side shim.

### Host buffers for foreign code: the heap super-array pattern

When host Pascal needs to *own* a sizable buffer that a C routine (or the
device orchestration builtins) will fill, prefer a heap super array over a
`malloc` extern returning an untyped `ADRMEM`:

```pascal
TYPE BUF = SUPER ARRAY [0..*] OF INTEGER32;
     PB  = ^BUF;
VAR p: PB;
...
NEW(p, n - 1);        { long-form NEW: i64 bound header + element data }
fill_from_c(p, n);    { the pointer coerces to an ADRMEM / void* param }
x := p^[i];           { typed element access, wide index under -f wide-integers }
DISPOSE(p)
```

The pointer lowers to the raw element pointer (the bound header of
`docs/super-array-bounds-abi.md` precedes the data), so the C side sees a
plain `T*`; the Pascal side keeps typed, bounds-aware (`$INDEXCK`) access and
`DISPOSE` cleanup.  Under `-f wide-integers` the `NEW` bound, array indices,
and `FOR` control variables may all be `INTEGER32`, so the buffer can exceed
the 16-bit `INTEGER` range.  Pinned in `tests/test_super_array_host_buffer.py`.

---

## Plan: reaching robust arbitrary C ABI

The work splits into an *interface* problem (how a programmer names a C function and its
C types unambiguously) and a *lowering* problem (emitting ABI-correct IR). They can ship
incrementally; each phase is independently useful and independently testable.

### Design principle: keep the vintage surface untouched

The default dialect must stay byte-for-byte faithful to IBM Pascal 2.0. Two things follow.
First, by-value lowering is opted into per-declaration with the explicit **`[C]` attribute**
from Phase 1, so the marker is local to the one declaration that needs ABI-correct
lowering rather than a mode that reinterprets ordinary code. Second — and this is the
shipped decision (see *Dialect gating* above) — the C-FFI surface as a whole (`[C]` plus
the `CINT`/`CLONG`/… aliases) is **available only under the extended dialect**, because the
aliases name fixed 32/64-bit widths that the faithful dialect does not otherwise expose;
gating the surface as a unit keeps those widths and the interface that needs them arriving
together. So in the faithful dialect the aliases are undeclared and `[C]` is rejected
outright, leaving unattributed vintage builds and all existing tests unaffected, the same
way `wide-integers` and `symbolic-enum-io` leave the default build alone. (An earlier draft
of this section argued for the bare attribute with *no* flag; that was superseded once it
was clear `[C]` would otherwise smuggle wide widths past the `wide-integers` gate.)

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
`builtins_registry.py` (C aliases), `type_checker.py` (`[C]` dialect gate). As shipped,
the entire surface is gated behind the extended dialect rather than a dedicated flag (see
*Dialect gating*); the `[C]` attribute is the per-declaration opt-in *within* that
dialect. Cost: low–medium. Risk: low.

### Phase 2 — Aggregate classifier (the core), behind a per-target seam — IMPLEMENTED

Goal: pass and return structs by value correctly; this is what unlocks unmodified
third-party libraries. Implemented for System V AMD64 in `codegen/c_abi.py`
(`SysVAmd64Abi` + `CAbiMixin`), selected via `c_abi_for_triple`.

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

### Phase 3 — Variadic foreign functions — IMPLEMENTED

Goal: call `printf`, `open`, etc.

A `[VARARGS]` attribute on a `[C] EXTERN` routine marks it variadic. The compiler emits
`ir.FunctionType(ret, fixed_params, var_arg=True)` — the LLVM backend handles all
platform-specific register accounting (`al` on System V, etc.) automatically. C default
argument promotions are applied to the variadic tail at each call site: `float` →
`double` via `fpext`; `i1`/`i8`/`i16` → `i32` via `zext`/`sext`. The `[VARARGS]`
surface shares the same dialect gate as `[C]` (extended dialect only, rejected in
`DEVICE` code), and `[VARARGS]` without `[C]` is a type-check error. The type checker
permits any number of call-site arguments ≥ the fixed parameter count; the variadic tail
is validated for expression well-formedness but not type-matched against a declared type
(there is none). Aggregate passing in the variadic tail flows through the existing Phase
2 classifier on `[C]` calls.

Example:

```pascal
FUNCTION printf(fmt: CPTR): CINT [C, VARARGS]; EXTERN;
FUNCTION sum_n(count: CINT): CINT [C, VARARGS]; EXTERN;
```

Touchpoints: `parser.py` (`[VARARGS]` contextual attribute), `type_checker.py` (dialect
gate, `[C]` requirement, device exclusion, variadic call-site arity), `type_system.py`
(`FunctionType.is_variadic`), `codegen/c_abi.py` (`CCallPlan.is_variadic`,
`_c_abi_variadic_promote`, variadic tail in `codegen_c_abi_call`),
`codegen/decls.py` (`var_arg=True` in `ir.FunctionType`). Cost: low–medium. Risk: low.
Coverage: `tests/test_c_ffi.py::TestVariadicParsing`, `TestVariadicTypecheck`,
`TestVariadicBuildAndRun`.

### Phase 4 — Scalar extension and return-type fidelity — IMPLEMENTED

Goal: close the latent `signext`/`zeroext`/`void` gaps so width-edge cases stop being
luck.

- **`signext`/`zeroext` on sub-32-bit scalar parameters and returns.** On `[C]` routines,
  integer types narrower than 32 bits carry the appropriate extension attribute on both
  the `declare` and the call-site `arg_attrs`:
  - `INTEGER` (i16), `CHAR` (i8), `CCHAR`, `CSHORT` → `signext` (signed).
  - `WORD` (i16), `BOOLEAN` (i8) → `zeroext` (unsigned/boolean).
  - 32-bit-and-wider types (CINT, CLONG, CDOUBLE, …) → no attribute needed.
  The `signext`/`zeroext` on the `declare` is what the LLVM backend uses to generate
  correct register-fill code; the call-site attribute is also emitted (via `arg_attrs`)
  for full LLVM IR well-formedness, matching what clang emits.
- **`[C]` `EXTERN` procedures emit `void`**, not the internal `i32` convention, so the
  declaration exactly matches a C `void` function type.
- **`BOOLEAN` ↔ C `_Bool`**: `BOOLEAN` (i8) is tagged `zeroext` so it zero-extends into
  the 32-bit register slot. C `_Bool` is guaranteed 0/1, so the contract holds for
  well-formed C callees.

Touchpoints: `codegen/c_abi.py` (`CParamPlan.sign_attr`, `CCallPlan.ret_sign_attr`,
`build_c_abi_plan` sign-attr threading, call-site attr emit in `codegen_c_abi_call`);
`codegen/decls.py` (`_c_abi_sign_attr` static helper, sign-attr and void-return
emission in `_codegen_c_abi_decl`). Cost: low. Risk: low.
Coverage: `tests/test_c_ffi.py::TestPhase4ScalarExtensionIR` (IR-level, no toolchain),
`TestPhase4BuildAndRun` (negative char return, void procedure, WORD zeroext).

### Phasing summary

| Phase | Outcome | Cost | Risk | Independent? | Status |
|-------|---------|------|------|--------------|--------|
| 0 | No more silent wrong answers; docs; no new flag | low | low | yes | done |
| 1 | `[C]` attribute + exact C type names | low–med | low | yes | done |
| 2 | Struct by value/return (per-target classifier, SysV first) | med–high | med | yes | done |
| 3 | Variadic calls (`printf`) via `[VARARGS]`; default-arg promotions | low–med | low | yes | done |
| 4 | `signext`/`zeroext`/`void` fidelity for sub-32-bit scalars + procedures | low | low | yes | done |

All four phases (0–4) are now shipped. The plan is complete.

Remaining aspirational items:
- **Windows AMD64** and **AArch64** target ABI classifiers (add implementations in
  `codegen/c_abi.py`; call sites and marshalling are target-neutral and need no changes).
- **A dedicated `c-ffi` feature flag** (finer-grained than the umbrella `extended`;
  described in *Dialect gating*) if future users want C interop without all other
  extended features.
- **Function-pointer variadics** (`var_arg` function pointers held in Pascal `ADRMEM`
  slots) if dynamic dispatch into variadic C callbacks ever becomes needed.

These are enhancements, not correctness gaps. The shipped phases cover all the cases
the original analysis identified as broken or impossible.

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
