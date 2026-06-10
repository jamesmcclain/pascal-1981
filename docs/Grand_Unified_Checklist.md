# Grand Unified Checklist — IBM Pascal 2.0 Reimplementation

**Goal:** full reimplementation of IBM Pascal 2.0 (not a subset). Every item the
manual specifies is in scope.

**How to read this list**

- `[ ]` = not done, `[x]` = done. Check items off as you go.
- Each item has an **evidence tag** for *how the gap was established*:
  `[OBSERVED]` = reproduced against the current parser/lexer/codegen;
  `[READ]` = read in source or in a manual page;
  `[INFERRED]` = deduction (effort/priority judgement, or manual semantics not yet
  read in full).
- Effort is a rough size: **XS / S / M / L / XL**.
- Items are ordered importance-first, then least-to-most effort within a group.
  The very top is the one true ordering request: the `WRITELN` "expr" grammar bug.
- "Audit" lines under a task are notes for whoever (human or agent) picks it up.

> Convention for agents: when you close an item, add a one-line note of *what you
> did* and *what test proves it*, and flip `[ ]` to `[x]`. Do not delete items;
> strike scope changes with a note instead.

---

## 0. Top priority — the grammar bug that is actively wrong

- [x] **0.1 — Restrict `:` field-width/format args to the I/O builtins.** `[OBSERVED]` **S**
  `parse_actual_parameter` consumes `:expr` suffixes on *every* call, so
  `WRITELN(x:5:2)` works but so does `FOO(1:2:3)` on an ordinary procedure.
  Field-width is a `WRITE`/`WRITELN` feature only.
  - Audit: fixtures `tests/fixtures/parser/judgment_calls/A_write_field_width.pas`
    (should keep passing) and `B_colon_args_any_call.pas` (should start failing).
    Decide whether `:w:d` lives in the grammar's `expression_list` or is special
    syntax recognized only for the write family; the manual's `WRITE` entry is the
    reference. Add a `should_pass` for `WRITELN(x:5:2)` and a `should_fail` for
    `FOO(1:2:3)`.
  - Done: added `WriteArg` parsing only for `WRITE`/`WRITELN`, preserved
    width/precision for printf-style codegen, and promoted the two judgment
    fixtures to parser assertions. Proven by `python -m unittest tests.test_parser`
    and `python -m unittest tests.test_typecheck tests.test_codegen`.

---

## 1. Correctness traps — things that compile/typecheck then break (fix before adding features)

These are worse than missing features because they fail late or silently.

- [x] **1.1 — `ABS` / `SQRT` / `LENGTH` typecheck but have no codegen.** `[OBSERVED]` **S**
  ABS/SQRT now have real type-check and codegen paths; `LENGTH` was removed from
  builtin registration because it is not in the manual's predeclared list.
  - Done: ABS is handled inline for INTEGER/REAL, SQRT lowers to `llvm.sqrt.f64`,
    and the type checker now special-cases both while leaving LENGTH unregistered.
    Proven by `python -m unittest tests.test_typecheck` and
    `python -m unittest tests.test_codegen`.

- [x] **1.2 — Set-type base is parsed then discarded.** `[READ]` **M**
  `parse_set_base` now preserves the declared set base. Named/enum bases keep
  their `NamedType`; subrange bases (`SET OF 1..10`, `SET OF 'A'..'Z'`,
  `SET OF lo..hi`) now parse to a `SubrangeType(low, high, host)` node that
  retains both bounds instead of collapsing to the bare host type. Scope: this
  is the **parser-level** data-loss fix only.
  - Done: added `SubrangeType` AST node, rewrote `parse_set_base`, added three
    judgment tests (`test_set_base_subrange_preserves_bounds`,
    `test_set_base_char_subrange_preserves_bounds`,
    `test_set_base_named_const_subrange_preserves_bounds`). Proven by
    `python -m unittest tests.test_parser`.
  - NOTE / does not cover: sets are still not resolved or lowered end-to-end.
    `type_checker.resolve_type` has no `SetType` branch (a `SET OF ...` decl
    currently resolves to `None`), and `codegen_llvm.llvm_type` has no `SetType`
    branch. Named-constant subrange bounds carry `host=None` pending type-checker
    resolution. Full set typing/codegen tracked in 9.6.

- [x] **1.3 — `NIL` is documented as special but isn't a token.** `[OBSERVED]` **S**
  `NIL` is now a real lexer token and AST literal rather than an identifier
  fallback. Parser constant/factor handling recognizes it, type checking treats it
  as a null pointer constant, and codegen lowers it to a typed LLVM null pointer
  during pointer assignment.
  - Done: added `NilLiteral`, `NIL` tokenization, pointer assignment type support,
    and tests for type checking/codegen. Proven by
    `python -m unittest tests.test_parser tests.test_typecheck` and
    `python -m unittest tests.test_codegen`.

- [x] **1.4 — Identifier labels are half-supported and inconsistent.** `[OBSERVED]` **S**
  `parse_label_id` and `LABEL`/`GOTO` now agree on identifier labels. Label
  statements accept both `IDENTIFIER ':'` and `INTEGER_LITERAL ':'`, so a label
  can be declared, defined, and jumped to using the same identifier form. The
  parser test suite covers the identifier-label path.

- [x] **1.5 — Non-`INCLUDE` brace directives are silently swallowed.** `[OBSERVED]` **S**
  Moved to item 9.5 for the full metacommand list. `$INCLUDE` stays working for
  now; the remaining brace directives need an explicit policy later.

---

## 2. Grammar — features in the manual missing from BOTH the EBNF doc and the parser

Each rejects today; all verified by parsing a snippet. These are required for a
full reimplementation.

- [x] **2.1 — `$`-hex vs manual radix `n#ddd`.** `[OBSERVED]` **S**
  The manual radix form is now supported as the canonical integer syntax:
  `16#FF` lexes as an integer constant, while `$FF` remains accepted as a
  compatibility extension.
  - Done: lexer recognizes `n#digits`, test coverage was added for `16#FF`, and
    the grammar doc now records radix + compatibility hex forms. Proven by
    `python -m unittest tests.test_parser tests.test_typecheck` and
    `python -m unittest tests.test_codegen`.

- [x] **2.2 — Multi-dimensional subscripts `a[i,j]`.** `[OBSERVED]` **S**
  Manual `selectp ::= [ ordexpr \, ]` allows a comma list; the parser's `selector`
  takes a single `[expression]` and rejects the comma. Either desugar `a[i,j]` to
  `a[i][j]` or support comma lists directly.
  - Done: desugared comma-separated subscripts into chained `INDEX` selectors in
    the parser; `a[i,j]` now parses as `a[i][j]`. Proven by
    `python -m unittest tests.test_parser`.

- [x] **2.3 — `FOR {STATIC} ident`.** `[OBSERVED]` **S**
  Manual allows an optional `STATIC` after `FOR`; parser rejects it
  (`expected IDENTIFIER, got STATIC`). `STATIC` already exists as a token.
  - Done: parser now accepts and preserves optional `STATIC` between `FOR` and
    the loop control variable, and codegen lowers that control variable to fixed
    internal global storage instead of stack storage. Added parser/codegen tests.
    Proven by `python -m unittest tests.test_parser tests.test_codegen`.

- [x] **2.4 — Labeled `BREAK` / `CYCLE`.** `[OBSERVED]` **S**
  Manual `{BREAK | CYCL}{getlabl}-` allows an optional target label; parser only
  accepts the bare keywords and leaves the label unconsumed. (Note: `CYCLE`
  spelling confirmed correct — the manual `CYCL` was a single-page typo.)
  - Done: BREAK/CYCLE now accept optional integer or identifier labels and preserve them on the AST while bare forms remain valid. Codegen now resolves unlabeled forms to the nearest enclosing loop and labeled forms to statement labels immediately preceding enclosing `WHILE`, `REPEAT`, or `FOR` loops. Proven by `python -m unittest tests.test_parser tests.test_typecheck tests.test_codegen`.

- [x] **2.5 — Short-circuit `AND THEN` / `OR ELSE`.** `[OBSERVED]` **M**
  Manual `boolexp ::= expr {{AND THEN | OR ELSE} expr}*`; parser rejects both
  (`expected factor` at `THEN`/`ELSE`). Needs grammar + codegen (true
  short-circuit branching, distinct from bitwise `AND`/`OR`).
  - Done: added `boolexp` parsing for condition contexts, boolean-only type rules for `AND_THEN`/`OR_ELSE`, and LLVM branch/PHI lowering that skips unnecessary RHS evaluation. Proven by `python -m unittest tests.test_parser tests.test_typecheck tests.test_codegen`.

- [x] **2.6 — `ADS ident` factor and `ADR OF` / `ADS OF` pointer types.** `[OBSERVED]` **M**
  Manual `factor` has `ADS ident` (alongside `ADR ident`, which exists); manual
  `typedec` has `{^ | ADR OF | ADS OF}`. Parser supports only `ADR ident` (the
  near pointer) and the `^` type; `ADS` (far/segmented) is absent at both the
  expression and type level. Tie this to the segmented-memory model (see 2.7).
  - Done: added `ADS` as an address-of factor and `ADR OF` / `ADS OF` type prefixes. `ADR OF` lowers to a plain LLVM pointer; `ADS OF` lowers to `{R pointer, S WORD}` with `S = 0` for the LLVM target. Proven by `python -m unittest tests.test_parser tests.test_typecheck tests.test_codegen`.

- [x] **2.7 — `VARS`/`CONSTS` far-reference semantics.** `[READ]` **M**
  Tokens and parameter modes exist; the manual distinguishes near (`VAR`/`CONST`,
  same-segment) from far (`VARS`/`CONSTS`, segment:offset-style) parameter
  references. Confirm whether the implementation actually treats them differently
  in codegen or only parses them. Pairs naturally with `ADS` (2.6).
  - Done: parameter-mode parsing preserves all four modes — `VAR`, `VARS`,
    `CONST`, `CONSTS` — as distinct tokens (`CONSTS` = `0x005A`, `VARS` =
    `0x0059` in the lexer). Type checking treats `CONST`/`CONSTS` as read-only
    aliases (assignment to them is rejected) and `VAR`/`VARS` as assignable; a
    `PURE` routine is forbidden from taking `VAR`/`VARS` parameters. Codegen
    passes all four as by-reference aliases. On the LLVM target the far forms
    (`VARS`/`CONSTS`) use the same ordinary-pointer lowering as the near forms —
    the same segment-zero approximation used for `ADS` (2.6) — rather than
    emulating segmented memory; true far/segmented behavior is out of scope here.
    Proven by `python -m unittest tests.test_parser tests.test_typecheck
    tests.test_codegen`.

- [x] **2.8 — Attribute argument forms beyond `ORIGIN(c)`.** `[READ]` **M**
  Manual `getattr` shows attributes carrying arguments and a `:ordcons` form;
  only `ORIGIN(constant)` is parsed. Reconcile the full attribute grammar
  (`attrs1` set + argument syntax) against the parser's fixed list.
  NOTE: `ORIGIN`/`PORT` are intentionally deferred for now; only the six
  confirmed attribute keywords are in scope here.
  - Done: accepted the six confirmed attribute keywords in bracketed lists,
    wired `READONLY` immutability, `PURE` validation, `STATIC` lowering, and
    `PUBLIC`/`EXTERN`/`EXTERNAL` linkage behavior. Proven by
    `python -m unittest tests.test_parser tests.test_typecheck tests.test_codegen`.

- [x] **2.9 — Type-prefixed set constructor `ident setcons`.** `[INFERRED]` **M**
  Implemented `TypeName[constant..constant]` typed set constructors with the
  manual restriction that typed set constructors require constant elements;
  variable-element forms such as `NumberSet[i..j]` are rejected. Backed by real
  set typing/lowering and tests in parser, type checker, and LLVM codegen.

---

## 3. Predeclared identifiers — trivial constants and type/file names

Cheapest coverage of the manual's predeclared list. Pure registration work.

- [x] **3.1 — Predeclared constants `MAXINT`, `MAXWORD`.** `[OBSERVED]` **XS**
  Added `MAXINT`/`MAXWORD` to the builtin symbol table and codegen constant map as folded immutable constants. Proven by `python -m unittest tests.test_typecheck tests.test_codegen`.

- [x] **3.2 — `TRUE` / `FALSE` as predeclared (audit).** `[OBSERVED]` **XS**
  Confirmed the current modeling is intentional: `TRUE`/`FALSE` stay lexer `BOOLEAN_LITERAL`s rather than re-definable identifiers. That matches the parser/typechecker/codegen path already in use, and keeps boolean literals distinct from ordinary names. Proven by the existing parser/typecheck/codegen boolean tests.

- [x] **3.3 — `NULL` (empty/super-array constant).** `[OBSERVED]` **S**
  Implemented `NULL` as the predeclared empty `LSTRING(0)` constant, distinct from pointer `NIL`. Type checking accepts it for compatible string storage, and codegen lowers it to a pointer to a shared empty string constant. Proven by `python -m unittest tests.test_parser tests.test_typecheck tests.test_codegen`.

- [x] **3.4 — Standard type / file names `TEXT`, `INPUT`, `OUTPUT`, `STRING`.** `[OBSERVED]` **S**
  Registered the names in the builtin symbol table so the parser/type checker can resolve them as predeclared identifiers. `TEXT` is modeled as a `TEXT OF CHAR` file type placeholder; `INPUT`/`OUTPUT` alias that placeholder; `STRING` is predeclared as a type name alongside the existing `STRING(n)` syntax. Proven by `python -m unittest tests.test_typecheck tests.test_codegen`.
  - NOTE: the acceptance test (`tests/test_typecheck.py:76`) also asserts
    `WRITELN(INPUT); WRITELN(OUTPUT); WRITELN(f)` typechecks, which is too
    permissive — a whole file is not a `WRITE`/`WRITELN` data argument. Tracked
    as a known gap under 8.3a.

---

## 4. Predeclared identifiers — codegen-level intrinsics (inline IR)

Single instruction or tiny fixed sequence; no allocation, no libc. Cheap, optimize
well. This is where `CHR`/`ORD`/`ODD`/`SUCC`/`SIZEOF`/`UPPER`/`ADR` already live.

- [x] **4.1 — `PRED`.** `[READ]` **XS** Mirror of existing `SUCC` (`n-1`).
  - Done: registered `PRED` as a predeclared integer function and lowered it to integer subtraction in codegen. Proven by `python3 -m unittest tests.test_typecheck tests.test_codegen`.
- [x] **4.2 — `SQR`.** `[READ]` **XS** `x*x`. Distinct from `SQRT` (6.x).
  - Done: registered `SQR` as a predeclared integer/real intrinsic and lowered it to self-multiplication in codegen. Proven by `python3 -m unittest tests.test_typecheck tests.test_codegen`.
- [x] **4.3 — `FLOAT`.** `[READ]` **S** INTEGER→REAL (`sitofp`). Needs REAL codegen.
  - Done: registered `FLOAT` as a predeclared intrinsic (manual 11-7: converts INTEGER to REAL) and lowered it to `sitofp` in codegen. Added type-checking and runtime executable coverage. Proven by `python -m unittest tests.test_typecheck tests.test_codegen`.
- [x] **4.4 — `TRUNC` / `ROUND`.** `[READ]` **M** REAL→INTEGER (`fptosi`; `ROUND` adds rounding). Needs REAL codegen.
  - Done: registered `TRUNC`/`ROUND` as REAL→INTEGER intrinsics (manual 11-7 confirmed: REAL-only arg, INTEGER result). `TRUNC` lowers to a direct `fptosi` (truncate toward zero). `ROUND` lowers to a ±0.5 select-and-add then `fptosi` (half-away-from-zero per IBM Pascal spec, no libm dependency — `llvm.round` links against `libm.round` in llvmlite so the arithmetic approach is used instead). Both reject non-REAL arguments at the type-checker level; INTEGER→REAL widening on the result side is correct Pascal. Proven by `python -m unittest tests.test_typecheck tests.test_codegen` (148 tests).
- [x] **4.5 — `LOWER`.** `[READ]` **S** Mirror of existing `UPPER` (super-array lower bound).
  - Done: added `LOWER` parsing, type checking, and codegen alongside `UPPER` so array bounds can be queried symmetrically. Proven by `python3 -m unittest tests.test_typecheck tests.test_codegen`.
- [x] **4.6 — `HIBYTE` / `LOBYTE`.** `[READ]` **S** Shift + truncate to byte.
  - Done: registered `HIBYTE`/`LOBYTE` as byte-extraction intrinsics and lowered them to shift/truncate codegen. Proven by `python3 -m unittest tests.test_typecheck tests.test_codegen`.
- [x] **4.7 — `WRD` / `BYWORD`.** `[READ]` **M** Word conversions.
  Manual (11-8/11-13): `WRD(x:ordinal):WORD` reinterprets any ordinal (or
  pointer) as an unsigned 16-bit WORD — same 16-bit pattern, so negative
  INTEGER is equivalent to `trunc i32→i16`; `BYWORD(hi,lo):WORD` packs two
  byte-sized ordinals by significance (hi→MSB, lo→LSB: `(hi&0xFF)<<8|(lo&0xFF)`).
  `WRD`/`BYWORD` also appear in constant expressions (manual p.6-5); extended
  `parse_constant` to recognise them as `FuncCall` nodes so the constant-folder
  can evaluate them at compile time.
  Also corrected `HIBYTE`/`LOBYTE` to accept `WORD` arguments (manual 11-12:
  "integer-word" parameter type); previously only `INTEGER` was accepted.
  - Done: registered both in type checker (`_setup_builtins`), added inference
    logic (WRD: any ordinal or pointer→WORD; BYWORD: 2 byte-sized ordinals→WORD;
    REAL rejected), extended `eval_const_expr` in codegen, added IR lowering
    (WRD: `trunc`/identity/`zext` by width; BYWORD: mask+shl+or), and extended
    `parse_constant` for constant-expression usage. Fixtures:
    `should_pass/wrd_basic.pas`, `should_pass/wrd_in_const.pas`,
    `should_fail/wrd_real_arg.pas`. Proven by
    `python -m unittest tests.test_typecheck tests.test_codegen` (172 tests,
    20 new).
- [x] **4.8 — `RETYPE`.** `[INFERRED]` **M** Reinterpret cast (LLVM `bitcast`); needs care with type-checker rules. Confirm semantics.
  - Done: Added `RetypeExpr` AST node, supporting optional trailing selectors. Integrated type checker resolution with size checks (generating Warning 248 on mismatches). Designed robust codegen using stack allocation of the larger type (zero-initialized) and pointer bitcasting to eliminate buffer over-read bugs. Added tests in both type checker and codegen. Proven by `python3 -m unittest discover -s tests`.
- [x] **4.9 — `PACK` / `UNPACK`.** `[INFERRED]` **M** Packed-array (un)packing; inline for small, runtime loop for large. Depends on `PACKED` representation.
  - Done: Added `packed` flag support to type system's `ArrayType` and updated `resolve_type` to propagate it. Implemented semantically complete type checking for `PACK` and `UNPACK` including mutability of output buffers, index range constraints, and bounds/size mismatch verification. Implemented code generation by generating a clean dynamic LLVM loop that performs safe index translation. Added tests verifying error validation and end-to-end execution. Proven by `python3 -m unittest discover -s tests`.

---

## 5. Predeclared identifiers — runtime-level: memory + control

C runtime, sibling to `runtime/fillc.c`. Loops/memory/OS, so not inline.

- [x] **5.1 — Promote `FILLC` to a real predeclared `extern`.** `[OBSERVED]` **S**
  Added `FILLC` to the shared predeclared registry and predeclared its runtime
  symbol in codegen, so user code can call it without a manual declaration while
  programmer-defined `FILLC` still shadows the builtin. Also handles the
  reference-compiler case where source declares `PROCEDURE fillc ...; extern;`
  by reusing the existing LLVM symbol instead of duplicating it. Verified by
  `python -m unittest` (266 tests).
- [x] **5.2 — `FILLSC`.** `[READ]` **S** Fill-with-shortcount sibling of `FILLC`.
  Added `FILLSC` to the shared predeclared registry, predeclared its runtime
  symbol in codegen, and added a runtime stub mirroring `FILLC` so source-level
  `extern` declarations reuse the existing LLVM symbol instead of colliding.
  CORRECTION to the original note: the leading **S** does NOT mean "shortcount".
  Per the manual, `FILLSC`/`MOVESL`/`MOVESR` are "the corresponding segmented
  address versions of these routines ... declared with `ADSMEM` instead of
  `ADRMEM` parameters" — i.e. they are the compatibility forms of the 8088
  segmented-memory builtins. `FILLSC` now takes an `ADSMEM` destination
  (segmented address). `ADSMEM` is a first-class type (the `ADS` sibling of
  `ADRMEM`) lowering to a `{flat pointer, segment word}` pair, matching `ADS`
  pointers; the runtime stub takes the matching C `adsmem` struct by value (the
  segment is always zero on this flat host, but is passed intact). Verified by
  `python -m unittest`.
- [x] **5.3 — `MOVEL` / `MOVER`.** `[READ]` **M** Block moves, left/right (overlap-aware → memmove direction).
  Added both names to the shared predeclared registry, predeclared their runtime
  externs in codegen, and implemented runtime stubs. CORRECTION to the original
  note: these must NOT be `memmove`. The manual defines `MOVEL` as a forward
  (left-start, ascending) byte copy and `MOVER` as a backward (right-start,
  descending) copy, and the direction is observable for overlapping regions
  (e.g. `MOVEL(p, p+1, n)` propagates the first byte across the buffer).
  `memmove` copies as-if-through-a-temporary and erases that distinction, so the
  stubs are now explicit forward/backward loops. Source-level `extern`
  declarations reuse the existing LLVM symbol. Proven by `python -m unittest`,
  including `TestMoveRuntimeDirection` (C-level overlap tests asserting
  `movel`→`AAAAA`, `mover`→`AABCD`, and that they differ).
- [x] **5.4 — `MOVESL` / `MOVESR`.** `[READ]` **M** Short-count move variants.
  Added both names to the shared predeclared registry, predeclared their runtime
  externs in codegen, and implemented runtime stubs. As with 5.3 the stubs are
  explicit forward (`MOVESL`, left-start) / backward (`MOVESR`, right-start)
  loops, not `memmove`. CORRECTION to the original note: these are NOT
  "short-count" move variants. Per the manual they are the SEGMENTED-address
  versions of `MOVEL`/`MOVER`, "declared with `ADSMEM` instead of `ADRMEM`
  parameters" — the compatibility forms of the original 8088 segmented-memory
  moves. Both `src`/`dst` are now `ADSMEM` (segmented `{pointer, segment}`
  pairs); passing a flat `ADR` address is a type error, and the runtime stubs
  take the matching C `adsmem` struct by value. There is no separate "short
  count" length semantics — the explicit caller-supplied length is correct.
  Source-level `extern` declarations (now with `ADSMEM` params) reuse the
  existing LLVM symbol. Proven by `python -m unittest`: `TestMoveRuntimeDirection`
  exercises the S variants (including a full Pascal→IR→runtime link asserting
  `MOVESL(ADS a, ADS b, WRD 4)` copies correctly), plus typecheck coverage that
  the segmented variants reject `ADR` and that `ADSMEM` resolves as a type.
- [x] **5.5 — `ABORT`.** `[READ]` **S** Wrapper over `abort()`/`exit()`.
  Added ABORT as a predeclared procedure. CORRECTION to the original note: the
  manual signature is `ABORT(CONST STRING, WORD, WORD)` — an error message, an
  error code, and a STATUS word — so the message param is typed `STRING` (not a
  raw pointer) and the lowering no longer discards the arguments. `builtin_abort`
  extracts the message chars/length, coerces the two WORD operands, and calls a
  new runtime `pabort(msg, len, code, status)` that reports them on stderr and
  aborts ("stops execution in the same way as an internal runtime error").
  Proven by `python -m unittest`, including `TestAbortRuntime` (the handler
  prints the message/code/status and aborts) and an IR test asserting the
  `pabort` call carries all four operands.
- [x] **5.6 — `NEW` / `DISPOSE`.** `[READ]` **M** Heap alloc/free (`malloc`/`free`). Needs real pointer-type support to be meaningful.
  - Done: added NEW/DISPOSE as predeclared procedures, type-checked them against mutable pointer variables, and lowered them to `malloc`/`free` with a null reset on DISPOSE. Proven by `python -m unittest tests.test_typecheck tests.test_codegen`.

---

## 6. Predeclared identifiers — runtime-level: transcendental math (libm)

Codegen builds the call; libm does the work. One consistent pattern for all six.
Gated on REAL codegen depth (see note at end).

- [x] **6.1 — `SQRT`.** `[OBSERVED]` **M** Currently a trap (1.1). Map to libm `sqrt`.
  - Done: Swapped the temporary LLVM intrinsic `llvm.sqrt` for standard external `libm` call pattern. Added `-lm` to the compilation options in `build_and_run`. Proven by `python -m unittest tests.test_codegen`.
- [x] **6.2 — `SIN`.** `[READ]` **S** libm `sin`.
  - Done: Added `SIN` to the type checker's special math functions list and mapped to libm `sin` in codegen. Proven by `tests.test_typecheck` and `tests.test_codegen`.
- [x] **6.3 — `COS`.** `[READ]` **S** libm `cos`.
  - Done: Added `COS` to the type checker's special math functions list and mapped to libm `cos` in codegen. Proven by `tests.test_typecheck` and `tests.test_codegen`.
- [x] **6.4 — `LN`.** `[READ]` **S** libm `log`.
  - Done: Added `LN` to the type checker's special math functions list and mapped to libm `log` in codegen. Proven by `tests.test_typecheck` and `tests.test_codegen`.
- [x] **6.5 — `EXP`.** `[READ]` **S** libm `exp`.
  - Done: Added `EXP` to the type checker's special math functions list and mapped to libm `exp` in codegen. Proven by `tests.test_typecheck` and `tests.test_codegen`.
- [x] **6.6 — `ARCTAN`.** `[READ]` **S** libm `atan`.
  - Done: Added `ARCTAN` to the type checker's special math functions list and mapped to libm `atan` in codegen. Proven by `tests.test_typecheck` and `tests.test_codegen`.
- [x] **6.7 — `ABS` (INTEGER + REAL).** `[OBSERVED]` **S** Currently a trap (1.1). Integer path inline, REAL path inline or libm `fabs`.
  - Done: ABS handles INTEGER/REAL inline (via select/sub/fsub). Proven by `tests.test_typecheck` and `tests.test_codegen`.

---

## 7. Predeclared identifiers — strings (do AFTER the string representation is settled)

All depend on the `LSTRING` / `STRING` / `SUPER ARRAY` memory layout. Settle that
first or these will be built on sand.

- [x] **7.1 — Decide the `LSTRING`/`STRING` representation.** `[INFERRED]` **S**
  Distinct semantic types: fixed-capacity `STRING(n)` and length-prefixed `LSTRING(n)`. Type checking resolves both forms, infers string literals as capacity-bearing `LSTRING`, and enforces literal capacity on assignment. Proven by `python -m unittest tests.test_parser tests.test_typecheck`.
- [x] **7.2 — Implement the `LSTRING`/`STRING` storage representation.** `[OBSERVED]` **L**
  Done: Replaced placeholder byte-pointer lowering with proper inline aggregate representation. LSTRING(n) → `[n+1 x i8]` with byte [0] = length (0..n, max n=255). STRING(n) → `[n x i8]` with no length prefix. Both stored inline (not pointer-to-side-buffer), supporting direct ADR/SIZEOF semantics. Assignment implements range checks (overflow = error), null-terminates LSTRING, blank-pads STRING (0x20). Updated `llvm_type`, `codegen_var_decl`, `get_string_chars_and_len`, assignment path, and WRITE/WRITELN output for inline aggregates. Re-tested all 7.3 string intrinsics (CONCAT/COPYSTR/COPYLST) and full codegen suite (170 tests). Proven by `python -m unittest tests.test_codegen tests.test_codegen_strings_bounds`.
  - CORRECTION (see 7.7): the "null-terminates LSTRING" behavior noted above
    was non-spec and is removed. LSTRING is length-prefixed only (manual 6-18);
    the terminator also overflowed by one byte at exact capacity.
- [x] **7.3 — `CONCAT`, `COPYSTR`, `COPYLST`.** `[READ]` **M** String build/copy.
  - Done: Added global and local buffer allocation for string variables in `codegen_var_decl`. Registered three procedures in type checker and added comprehensive type checking logic. Implemented branchless inline LLVM lowering (using `memcpy` and `memset`) for string copying, concatenating, space-padding, and dynamic null-termination in code generator. Added 10 type-checking tests and 3 compile-and-run tests. Proven by `python -m unittest tests.test_typecheck.TestStringProcedures` and `python -m unittest tests.test_codegen` (including `test_string_concat_runtime`, `test_string_copylst_runtime`, `test_string_copystr_runtime`).
  - CORRECTION (see 7.7): the "dynamic null-termination" noted above is removed
    (non-spec), and the manual's capacity error (11-20) is now enforced on all
    three procedures, which 7.3 omitted.
- [x] **7.4 — `INSERT`, `DELETE`, `POSITN`.** `[READ]` **M** Edit/search.
  - Done: added builtin registration and type checking for all three, lowered INSERT/DELETE with `memmove`, and implemented POSITN via a runtime search helper returning the 1-based match offset or 0 when not found. Proven by `python -m unittest tests.test_typecheck tests.test_codegen`.
- [x] **7.5 — `SCANEQ`, `SCANNE`.** `[READ]` **M** Scan-while-equal / not-equal.
  - Done: added builtin registration, type checking, runtime lowering, and tests for the scan intrinsics. Proven by `python -m unittest tests.test_typecheck tests.test_codegen`.
- [x] **7.6 — `ENCODE` / `DECODE`.** `[READ]` **L** Number↔string formatting (libc `sprintf`/`sscanf` under the hood).
  - Done: registered the builtins, added parser support for `X:M:N`-style calls, typechecked the destination/source and formattable argument, lowered to runtime helpers, and added tests.
- [x] **7.7 — String-correctness hardening (post-7.2/7.3 follow-ups).** `[OBSERVED]` **M**
  Three defects in the shipped LSTRING/STRING codegen, found reviewing 7.2/7.3
  against the manual, are fixed:
  (1) **LSTRING is length-prefixed, not null-terminated.** 7.2/7.3 wrote a NUL
  at byte `[current_len + 1]`. The manual (6-18/6-19) defines `LSTRING(n)` as
  `PACKED ARRAY [0..n] OF CHAR` — byte `[0]` = length, no terminator — and
  `WRITE` emits "the current length string." At exact capacity (`len == n`) the
  NUL landed at index `n+1`, one past the `[n+1 x i8]` aggregate (a one-byte
  overflow). Removed the terminator stores from the assignment path, `CONCAT`,
  and `COPYLST`; `WRITE` of an LSTRING now uses `%.*s` driven by the `[0]`
  length byte instead of `%s`.
  (2) **`CONCAT`/`COPYLST`/`COPYSTR` had no capacity check.** The manual (11-20)
  requires an error when `upper(D) < length(D)+upper(S)` (CONCAT) or
  `upper(D) < upper(S)` (COPYLST/COPYSTR). Added a shared guard
  (`_guard_string_capacity`) that aborts before any write; this also makes
  `COPYSTR`'s blank-pad length provably non-negative.
  (3) **`WRITE` field-width arg ordering.** `WRITE(s:w)` lowered to `%*.*s` but
  emitted the implicit length (the precision) ahead of the width arg, swapping
  the two; the length is now appended after the width.
  - Proven (confirmed here): front end green via `python3 -m unittest
    tests.test_parser tests.test_typecheck` (100), and the IR-level guard/format
    checks in `tests.test_codegen_strings_bounds`
    (`TestStringIntrinsicCapacityIR`, `TestLStringLengthSemantics`).
  - Build/run coverage (`TestStringIntrinsicCapacityRuntime`,
    `TestWriteFieldWidthOrdering`, exact-capacity round-trip) is in the same
    file under `@requires_exe`; runs where llvmlite+clang are present.
  - Open coupling: per the manual these range errors are gated on `$RANGECK`,
    still unhandled (see 9.5). The checks are currently unconditional; revisit
    when the metacommand machinery lands.

---

## 8. Predeclared identifiers — file & extended I/O (largest subsystem)

The grammar doc already flags `FILE OF` runtime I/O as unverified/blocked. This is
the biggest single chunk; expect it to need its own design pass.

- [x] **8.1 — File-type runtime + buffer-variable model.** `[READ]` **XL**
  Backs everything below; tie to `TEXT`/`INPUT`/`OUTPUT` (3.4).
  - Done: added `TEXT` vs binary `FILE OF T` metadata and an inline
    file-control block (element size, structure flag, touched flag, and a
    pointer to the current-component buffer) for file variables and predeclared
    `INPUT`/`OUTPUT`. The FCB and its buffer are allocated inline at the
    variable's storage site (no per-file `malloc`, so nothing leaks); the
    `structure` flag is stored in the FCB rather than discarded; `F^` reads/
    writes the FCB's own buffer (distinct from the handle) through
    `pas_file_buffer`; and the `pas_file_touch_buffer` hook records buffer
    access (sets the touched flag) instead of being an empty body. Whole-file
    assignment is rejected. Proven by `python -m unittest tests.test_parser
    tests.test_typecheck tests.test_codegen`.
  - NOTE / does not cover: there is no device I/O yet. `INPUT`/`OUTPUT` are not
    attached to stdin/stdout, and the FCB has no fd/position/mode — those, plus
    the lazy fill/flush that the touch hook is a seam for, are 8.2 (`RESET`/
    `REWRITE`/`GET`/`PUT`). 8.1 is the in-memory buffer-variable model only.
- [ ] **8.2 — `RESET`, `REWRITE`, `GET`, `PUT`.** `[READ]` **L** Core file ops.
- [x] **8.3 — `READ`, and `READLN` beyond integer; `WRITE`/`WRITELN` for `REAL`.** `[OBSERVED]` **M**
  `READLN` currently reads integers only; `WRITE`/`WRITELN` don't handle `REAL`.
  Extend the existing printf/scanf hybrid path.
  - Done: READ/READLN dispatch now resolves semantic types, `READLN` emits
    `pas_readln_skip`, BOOLEAN reads are rejected at typecheck, and the string
    range-guard control flow no longer self-branches when RANGECK is off.
    Proven by `python -m unittest tests.test_typecheck tests.test_codegen_strings_bounds -q`.
  - NOTE / does not cover: file-directed I/O, the optional leading file
    argument, or the remaining runtime-reader semantics for bounded string
    input. Those remain for 8.2/8.3a/8.4.
- [ ] **8.3a — `WRITE`/`WRITELN` accept a whole file variable as a data argument.** `[OBSERVED]` **S**
  Still open. This item is the file-selector / whole-file-argument split, and
  should stay separate from 8.3's ordinary data-argument type checking.
  Typecheck trap: a bare file variable in the argument list passes today, e.g.
  `WRITE(f)` and `WRITELN(f)` for `f: FILE OF INTEGER` (a *binary* file) both
  typecheck as success, as does `WRITELN(t)`/`WRITELN(INPUT)`/`WRITELN(OUTPUT)`.
  This is wrong: `WRITE`/`WRITELN` apply to `TEXT` only, and a file is never a
  data value. Proper semantics: an *optional leading* `TEXT`-file argument
  selects the target stream and the remaining arguments are values; a binary
  `FILE OF T`, or a whole file in the data position, must be rejected (parallel
  to the whole-file *assignment* rejection already done in 8.1). The checker
  currently models neither the file-selector role nor the binary rejection.
  - Audit: `tests/test_typecheck.py:76` asserts
    `WRITELN(INPUT); WRITELN(OUTPUT); WRITELN(f)` *succeeds* (a 3.4 artifact),
    baking in this behavior; that assertion must be revisited when this is
    fixed.
- [ ] **8.4 — `EOF`, `EOL`, `EOLN`.** `[READ]` **M** Stream predicates.
- [ ] **8.5 — `ASSIGN`, `CLOSE`, `DISCARD`, `READFN`, `READSET`.** `[READ]` **L** Extended I/O verbs.
- [ ] **8.6 — `FILEMODES`, `SEQUENTIAL`, `TERMINAL`, `FCBFQQ`.** `[INFERRED]` **L**
  Confirm each one's meaning from the manual body before implementing — several
  are opaque from the identifier list alone.

---

## 9. Cross-cutting / infrastructure

- [x] **9.1 — REAL codegen depth.** `[READ]` **M**
  README calls REAL "limited codegen support." Sections 4.3/4.4 and all of 6 hang
  off this. Audit and harden REAL before (or alongside) the math intrinsics.
  - Done: three bugs found and fixed.
    (1) `SLASH` always produces REAL in Pascal, but codegen checked `is_real` by
    inspecting LLVM operand types — two `i32`s produced `fdiv i32` (invalid IR).
    Fixed by setting `is_real = True` unconditionally when `op == 'SLASH'` before
    the operand-promotion block. (2) REAL constants (`CONST PI = 3.14159`) crashed
    with "Cannot evaluate constant expression: RealLiteral" because `eval_const_expr`
    was int-only and `self.constants` was typed `Dict[str,int]`. Fixed by widening
    both to carry `float` values, adding `RealLiteral`/`CharLiteral` cases and
    float-aware arithmetic folding, and emitting `ir.Constant(ir.DoubleType(), v)`
    at use sites via a new `_const_ir()` helper. (3) Unary minus on a `double`
    operand emitted integer `sub`; fixed by checking operand type in
    `codegen_unaryop` and using `fsub(0.0, operand)` for doubles. Output format
    note: WRITE/WRITELN emits `%f` (six decimals by default); IBM Pascal’s
    free-format / exponential default differs — tracked as a future cosmetic fix,
    not a correctness blocker. Proven by `python -m unittest tests.test_parser
    tests.test_typecheck tests.test_codegen` (157 tests, 8 new REAL-hardening
    run tests in `TestCodegenBuildRun`).
- [x] **9.2 — Predeclared-identifier registration mechanism.** `[INFERRED]` **S**
  Centralized predeclared registration in `builtins_registry.py`; `type_checker`
  now uses the shared table, predeclared symbols are tagged `is_builtin`, and
  user-defined redeclarations are allowed to shadow builtins instead of tripping
  redeclaration errors. Proven by `python -m unittest` (264 tests).
- [ ] **9.3 — Test fixtures for every closed item.** `[INFERRED]` **S (ongoing)**
  Each grammar item → a `should_pass`/`should_fail` fixture; each intrinsic → a
  codegen test (and a build/run test where a runtime is involved). Keeps the
  grammar doc, parser, and runtime honest against each other.
- [ ] **9.4 — Keep `docs/ebnf_grammar.md` in sync.** `[INFERRED]` **S (ongoing)**
  Every grammar change above should update the EBNF doc and its change log in the
  same commit, with the right evidence grade.

- [ ] **9.5 — Remaining compiler metacommands.** `[OBSERVED]` **M**
  After `$INCLUDE` and the identifier-label cleanup, the brace-directive path
  still needs an explicit policy for the rest of the IBM Pascal metacommands:
  `$BRAVE`, `$DEBUG`, `$ENTRY`, `$ERRORS`, `$GOTO`, `$INDEXCK`, `$INITCK`,
  `$LINE`, `$MATHCK`, `$NILCK`, `$RANGECK`, `$RUNTIME`, `$STACKCK`, `$WARN`,
  `$LINESIZE`, `$LIST`, `$OCODE`, `$PAGE`, `$PAGEIF`, `$PAGESIZE`, `$SKIP`,
  `$SUBTITLE`, `$SYMTAB`, `$TITLE`, `$IF`, `$INCONST`, `$MESSAGE`, `$POP`, and
  `$PUSH`. Decide which are ignored, which affect parser/codegen state, and
  which should error when unsupported.

- [x] **9.6 — Full set type-checking and codegen.** `[OBSERVED]` **L**
  CORRECTION to the original audit note: by the time this item was picked up,
  item 2.9 had already added the `SetType` resolution path
  (`type_checker.resolve_type` handles `ASTSetType`/`ASTSubrangeType`), the
  fixed 256-bit (`[4 x i64]`) runtime representation (`codegen_llvm.set_llvm_type`
  + `llvm_type` `SetType` branch), and lowering for `IN`, union (`+`),
  intersection (`*`), difference (`-`), and the set comparisons. So the audit's
  "resolve_type/llvm_type have no SetType branch" was stale.
  - Done (the three gaps that actually remained):
    (1) **Dynamic set constructors.** `codegen_set_constructor` now folds the
    constant part and emits runtime IR for non-constant elements (single-bit OR)
    and non-constant ranges (`[lo..hi]` via a counted loop, reversed = empty), so
    `s := [i, lo..hi, 20]` works. (2) **Enum-based set bases.** Added
    `type_system.EnumType`, an `ASTEnumType` branch in `resolve_type`,
    registration of enum members as ordinal constants in both the type checker
    and codegen, an `EnumType` branch in `codegen_llvm.llvm_type` (i32), and enum
    comparison in `binary_op_result_type`, so `SET OF Color` / `Green IN s` work.
    (3) **Named-constant subrange bases.** `SET OF lo..hi` resolves via the
    bound expressions' ordinal type.
  - Also fixed a cross-cutting bug found along the way: `CharLiteral` carried the
    quoted lexeme (`'B'`) instead of the unquoted value, so char ordinals were
    wrong (membership used `'`=39, constant folding returned 0). The parser now
    stores `Token.value`; char sets are correct.
  - Proven by `python -m unittest tests.test_parser tests.test_typecheck
    tests.test_codegen tests.test_integration` (163 tests). New tests:
    `test_set_dynamic_element_runtime`, `test_set_dynamic_range_runtime`,
    `test_char_set_membership_runtime`, `test_enum_set_membership_runtime`
    (codegen) and `test_enum_set_declaration_and_membership`,
    `test_named_const_subrange_set_base_resolves` (typecheck). EBNF `set_type`
    note refreshed to match the real implementation.

- [ ] **9.7 — Deferred attribute-argument forms.** `[DEFERRED]` **S**
  `ORIGIN(c)` and any `PORT(addr)`-style attribute syntax remain intentionally
  out of scope until the manual's prose and grammar are reconciled more fully.

- [x] **9.8 — Full Enum support.** `[INFERRED]` **M**
  Enum-based sets (9.6) now work because they resolve to `i32` ordinals, but
  the compiler lacks first-class enum support: `SUCC`/`PRED` on enums, `CASE`
  statements over enums, enum-controlled `FOR` loops, and `WRITE` of enum names
  all need dedicated paths to support the `EnumType` introduced in 9.6.
  - Done: enums lower to `i32`, so the codegen for `SUCC`/`PRED`, `ORD`, `CASE`,
    and `FOR` already operated correctly on the ordinal; the gaps were in the
    type checker plus one real codegen feature (WRITE-by-name).
    (1) **`SUCC`/`PRED`** now accept any ordinal type and return that same type
    (so `c := SUCC(c)` keeps its enum type) instead of demanding `INTEGER`.
    (2) **`CASE`** type-checking was a no-op `TODO`; it now infers the selector
    type and checks every label (and range endpoint) for compatibility, so
    `CASE c OF Red: ...` is validated and a wrong-enum label is rejected. The
    check is lenient (silent on un-inferable types, bidirectional `can_assign`)
    so existing INTEGER/CHAR cases are unaffected. (3) **`FOR`** now accepts any
    ordinal control variable with assignment-compatible bounds (`FOR c := Red TO
    Blue`), replacing the hard INTEGER-only rule. (4) **`WRITE`/`WRITELN`** of an
    enum value now prints the symbolic member name: codegen emits a cached
    per-enum `[n x i8*]` name table, indexes it by the runtime ordinal, and
    prints the resulting pointer with `%s`. Covers enum variables, enum
    designators, and bare member literals (`WRITE(Blue)`).
  - Also in scope (necessary supporting fix, noted per the strike-don't-delete
    convention): **`ORD`** previously accepted only `CHAR`; it now accepts any
    ordinal type (enums included) and returns `INTEGER`. This is what makes an
    enum `FOR` body able to use the ordinal and is core to first-class enum use.
  - Does NOT cover: printing the *name* of an arbitrary enum-typed expression
    such as `WRITE(SUCC(c))` — codegen has no per-expression Pascal type, so
    only enum variables/designators/member-literals print by name; other
    enum-typed expressions still print the ordinal. Also unchanged: `READ` of an
    enum (no enum input parsing). These are intentionally out of scope here.
  - Existing-behavior change: a non-ordinal `FOR` control variable (e.g. `REAL`)
    is still rejected, but the message is now "FOR loop variable must be an
    ordinal type" rather than "must be INTEGER"; the one test asserting the old
    wording was updated accordingly.
  - Proven by `python -m unittest tests.test_parser tests.test_typecheck
    tests.test_codegen tests.test_integration tests.test_codegen_strings_bounds`
    (262 tests). New tests: `TestEnumCodegen` (SUCC/PRED, CASE, FOR, WRITE-name
    variable/loop/bare-literal runtime, plus an IR-level name-table check) and
    `TestEnumValidation` (valid enum FOR/SUCC/PRED/ORD/CASE, plus
    `SUCC` on REAL and wrong-enum CASE label rejections).

- [x] **9.9** `RETYPE` on a pointer value is ambiguous. When the inner expression is already a pointer type, the code bitcasts the pointer and loads through it — reinterpreting the pointee, not the pointer's address bits. That's correct when the "pointer" is an aggregate's address (array/string), but if someone retypes an actual Pascal `^T` variable, they'd reasonably expect the address bits reinterpreted, not a deref. This is the codebase's existing aggregate-vs-pointer-value conflation, but RETYPE makes it user-reachable, so at least a guard or comment is warranted.
  - Done: the `RetypeExpr` codegen no longer branches on the LLVM type alone.
    A new helper `retype_source_is_pointer_value` classifies the inner
    expression from its Pascal type: `ADR`/`ADS`/`NIL` and `^T` variables are
    genuine pointer *values* (reinterpret the address bits via a spill + slot
    bitcast, no dereference), while STRING/LSTRING/ARRAY/RECORD addresses keep
    the legacy load-through-pointee behavior. When the AST can't classify the
    inner expression, the lowering falls back to the LLVM pointee type (a
    non-aggregate pointee is treated as a scalar pointer; an aggregate pointee
    defaults to load-through), so the silent null-deref miscompile is gone and
    no case is left to guess wrongly. The branch carries a comment documenting
    the conflation so it isn't re-collapsed later. Proven by
    `python -m unittest tests.test_parser tests.test_typecheck tests.test_codegen`,
    including new IR-level tests
    `test_retype_pointer_value_reinterprets_bits_not_pointee` and
    `test_retype_aggregate_address_still_loads_through`, plus the
    `@requires_exe` `test_retype_nil_pointer_does_not_dereference`.

- [x] **9.10** `wrd_real_arg.pas` is misfiled and self-contradictory. It sits in `parser/should_pass/`, its body comment says "must be rejected — ERROR: REAL is not an ordinal type," and the 4.7 checklist note cites it as `should_fail/wrd_real_arg.pas`. All three disagree. As a parser fixture it correctly passes (REAL rejection is a type error, not a parse error), and the parser-reject test only catches `LexerError`/`ParserError` anyway — so even in `should_fail/` it wouldn't assert what the comment claims. The good news: the REAL rejection is actually covered, by `TestWrdByword.test_wrd_real_is_error` in `test_typecheck`. So there's no real coverage gap — just an artifact that documents a guarantee it doesn't itself enforce. Move/rename it or fix the comment so it stops lying.
  - Done: Moved the test fixture to `tests/fixtures/typecheck/should_fail/wrd_real_arg.pas` and corrected its comment to clarify it's a type error, not a parse error.

---

## Notes on ordering judgement

- Section 0 is the one item that is *actively wrong* (accepts illegal programs),
  hence top, per request.
- Section 1 is next because late/silent failures erode trust faster than missing
  features; these are mostly small.
- Sections 2–3 are high-value, low-effort grammar + constant work.
- Sections 4–6 are the bulk of the intrinsics, ordered cheap→dear, with math
  gated on REAL (9.1).
- Sections 7–8 are deferred on purpose: they need representation/subsystem
  decisions first, and the grammar doc already flags the file side as unverified.
- Section 9 is the connective tissue that keeps the other eight honest.

## Unknowns to resolve from the manual body (not just the identifier list)

`NULL`, `RETYPE`, `BYWORD`, `WRD`, `FCBFQQ`, `FILEMODES`, `SEQUENTIAL`,
`TERMINAL`, the exact `getattr` argument grammar, and the `ident setcons` form
are all `[INFERRED]` on semantics — read the manual's prose for each before
implementing, to avoid building on a plausible-but-wrong guess.
