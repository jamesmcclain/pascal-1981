# Calling C-ABI Foreign Functions from Host Pascal

Reference for foreign-function calls from host `MODULE`/`PROGRAM` code. The
implementation-status report, the TL;DR, the "what arbitrary C ABI requires"
explainer, the empirical findings (pre-Phase-2 baseline matrix and the decisive
by-value-struct reproduction), and the phased plan (Phases 0–4, all
IMPLEMENTED) through its testing strategy, alternatives, target portability,
scope risks, and appendix reproductions are archived in
`docs/old/c-abi-implementation-plan.md`.

Tested target: GNU/Linux AMD64 (System V). Intended targets: also Windows
AMD64 and ARM Linux (AArch64).

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

