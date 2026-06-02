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
  `parse_set_base` now preserves the declared base type instead of collapsing to
  `INTEGER`; set declarations keep their base element type through the AST.
  - Done: parser now returns the real base type (rather than the old placeholder)
    and a parser judgment test verifies `SET OF CHAR` retains its base. Proven by
    `python -m unittest tests.test_parser tests.test_typecheck` and
    `python -m unittest tests.test_codegen`.

- [ ] **1.3 — `NIL` is documented as special but isn't a token.** `[OBSERVED]` **S**
  The EBNF `constant` lists a dedicated `"NIL"` alternative and a comment claims it
  is matched as a constant rather than a generic identifier. There is no `NIL`
  token in the lexer and no `NIL` case in `parse_constant`/`parse_factor`; it only
  "works" by falling through to the IDENTIFIER rule. Add a real `NIL` token + a
  typed null-pointer constant, or correct the grammar doc to match reality.

- [ ] **1.4 — Identifier labels are half-supported and inconsistent.** `[OBSERVED]` **S**
  `parse_label_id` and `LABEL`/`GOTO` accept identifier labels, but a *label
  statement* only triggers on `INTEGER_LITERAL ':'` (parser line ~418), so an
  identifier label can be declared and jumped to but never *defined*. Pick one
  rule (the manual's `getlabl ::= {ident | number}` suggests identifiers are
  legal) and make declaration, definition, and `GOTO` agree.

- [ ] **1.5 — Non-`INCLUDE` brace directives are silently swallowed.** `[OBSERVED]` **S**
  Only `{$INCLUDE:` / `(*$INCLUDE:` are real directives; any other `{$...}`
  (e.g. `{$LIST+}`) falls through to comment-skipping and vanishes with no error.
  Decide the directive policy (recognize-and-ignore known ones, error on unknown,
  or pass through) and implement it deliberately.

---

## 2. Grammar — features in the manual missing from BOTH the EBNF doc and the parser

Each rejects today; all verified by parsing a snippet. These are required for a
full reimplementation.

- [ ] **2.1 — `$`-hex vs manual radix `n#ddd`.** `[OBSERVED]` **S**
  Lexer accepts `$FF`; the manual uses the `{digit}+ # {digit}+` radix form
  (`16#FF`), which lex-rejects today (`Unexpected character '#'`). Decide whether
  to support the manual's radix syntax, the `$` form, or both, and reconcile the
  grammar doc (which currently calls `$FF` an `[ADDED]` extension).

- [ ] **2.2 — Multi-dimensional subscripts `a[i,j]`.** `[OBSERVED]` **S**
  Manual `selectp ::= [ ordexpr \, ]` allows a comma list; the parser's `selector`
  takes a single `[expression]` and rejects the comma. Either desugar `a[i,j]` to
  `a[i][j]` or support comma lists directly.

- [ ] **2.3 — `FOR {STATIC} ident`.** `[OBSERVED]` **S**
  Manual allows an optional `STATIC` after `FOR`; parser rejects it
  (`expected IDENTIFIER, got STATIC`). `STATIC` already exists as a token.

- [ ] **2.4 — Labeled `BREAK` / `CYCLE`.** `[OBSERVED]` **S**
  Manual `{BREAK | CYCL}{getlabl}-` allows an optional target label; parser only
  accepts the bare keywords and leaves the label unconsumed. (Note: `CYCLE`
  spelling confirmed correct — the manual `CYCL` was a single-page typo.)

- [ ] **2.5 — Short-circuit `AND THEN` / `OR ELSE`.** `[OBSERVED]` **M**
  Manual `boolexp ::= expr {{AND THEN | OR ELSE} expr}*`; parser rejects both
  (`expected factor` at `THEN`/`ELSE`). Needs grammar + codegen (true
  short-circuit branching, distinct from bitwise `AND`/`OR`).

- [ ] **2.6 — `ADS ident` factor and `ADR OF` / `ADS OF` pointer types.** `[OBSERVED]` **M**
  Manual `factor` has `ADS ident` (alongside `ADR ident`, which exists); manual
  `typedec` has `{^ | ADR OF | ADS OF}`. Parser supports only `ADR ident` (the
  near pointer) and the `^` type; `ADS` (far/segmented) is absent at both the
  expression and type level. Tie this to the segmented-memory model (see 2.7).

- [ ] **2.7 — `VARS`/`CONSTS` far-reference semantics.** `[READ]` **M**
  Tokens and parameter modes exist; the manual distinguishes near (`VAR`/`CONST`,
  16-bit same-segment) from far (`VARS`/`CONSTS`, 32-bit segment:offset). Confirm
  whether the implementation actually treats them differently in codegen or only
  parses them. Pairs naturally with `ADS` (2.6).

- [ ] **2.8 — Attribute argument forms beyond `ORIGIN(c)`.** `[READ]` **M**
  Manual `getattr` shows attributes carrying arguments and a `:ordcons` form;
  only `ORIGIN(constant)` is parsed. Reconcile the full attribute grammar
  (`attrs1` set + argument syntax) against the parser's fixed list. Confirm the
  `PORT(addr)` status (grammar doc marks it UNVERIFIED).

- [ ] **2.9 — Type-prefixed set constructor `ident setcons`.** `[INFERRED]` **M**
  Manual `factor` lists `ident setcons` (a set constructor qualified by a set
  type name). Not handled. Depends on real set typing (1.2). Confirm exact
  semantics from the manual body before implementing.

---

## 3. Predeclared identifiers — trivial constants and type/file names

Cheapest coverage of the manual's predeclared list. Pure registration work.

- [ ] **3.1 — Predeclared constants `MAXINT`, `MAXWORD`.** `[OBSERVED]` **XS**
  Absent from lexer/typechecker/codegen. Register as folded constants.

- [ ] **3.2 — `TRUE` / `FALSE` as predeclared (audit).** `[OBSERVED]` **XS**
  Already lexer `BOOLEAN_LITERAL`s; confirm that's the desired modeling for a
  faithful reimplementation (manual lists them as predeclared identifiers, i.e.
  re-definable). Probably fine as-is — just confirm and check off.

- [ ] **3.3 — `NULL` (empty/super-array constant).** `[OBSERVED]` **S**
  Listed under Super Array Type Feature. Not present. Confirm exact semantics
  (relationship to `NIL`, to zero-length strings) before wiring. `[INFERRED]`
  on semantics.

- [ ] **3.4 — Standard type / file names `TEXT`, `INPUT`, `OUTPUT`, `STRING`.** `[OBSERVED]` **S**
  None present as predeclared. `TEXT`/`INPUT`/`OUTPUT` only become meaningful with
  file I/O (section 8); `STRING` ties to the string story (section 7). Register
  the names now, flesh out behavior later.

---

## 4. Predeclared identifiers — codegen-level intrinsics (inline IR)

Single instruction or tiny fixed sequence; no allocation, no libc. Cheap, optimize
well. This is where `CHR`/`ORD`/`ODD`/`SUCC`/`SIZEOF`/`UPPER`/`ADR` already live.

- [ ] **4.1 — `PRED`.** `[READ]` **XS** Mirror of existing `SUCC` (`n-1`).
- [ ] **4.2 — `SQR`.** `[READ]` **XS** `x*x`. Distinct from `SQRT` (6.x).
- [ ] **4.3 — `FLOAT`.** `[READ]` **S** INTEGER→REAL (`sitofp`). Needs REAL codegen (see note).
- [ ] **4.4 — `TRUNC` / `ROUND`.** `[READ]` **M** REAL→INTEGER (`fptosi`; `ROUND` adds rounding). Needs REAL codegen.
- [ ] **4.5 — `LOWER`.** `[READ]` **S** Mirror of existing `UPPER` (super-array lower bound).
- [ ] **4.6 — `HIBYTE` / `LOBYTE`.** `[READ]` **S** Shift + truncate to byte.
- [ ] **4.7 — `WRD` / `BYWORD`.** `[INFERRED]` **M** Word conversions; confirm exact semantics from manual body first.
- [ ] **4.8 — `RETYPE`.** `[INFERRED]` **M** Reinterpret cast (LLVM `bitcast`); needs care with type-checker rules. Confirm semantics.
- [ ] **4.9 — `PACK` / `UNPACK`.** `[INFERRED]` **M** Packed-array (un)packing; inline for small, runtime loop for large. Depends on `PACKED` representation.

---

## 5. Predeclared identifiers — runtime-level: memory + control

C runtime, sibling to `runtime/fillc.c`. Loops/memory/OS, so not inline.

- [ ] **5.1 — Promote `FILLC` to a real predeclared `extern`.** `[OBSERVED]` **S**
  Today it only works because `primes.pas` hand-declares it and links `fillc.c`.
  Auto-register it (manual: System Intrinsics) so user code needn't declare it.
  This establishes the pattern for the rest of section 5.
- [ ] **5.2 — `FILLSC`.** `[READ]` **S** Fill-with-shortcount sibling of `FILLC`.
- [ ] **5.3 — `MOVEL` / `MOVER`.** `[READ]` **M** Block moves, left/right (overlap-aware → memmove direction).
- [ ] **5.4 — `MOVESL` / `MOVESR`.** `[READ]` **M** Short-count move variants.
- [ ] **5.5 — `ABORT`.** `[READ]` **S** Wrapper over `abort()`/`exit()`.
- [ ] **5.6 — `NEW` / `DISPOSE`.** `[READ]` **M** Heap alloc/free (`malloc`/`free`). Needs real pointer-type support to be meaningful.

---

## 6. Predeclared identifiers — runtime-level: transcendental math (libm)

Codegen builds the call; libm does the work. One consistent pattern for all six.
Gated on REAL codegen depth (see note at end).

- [ ] **6.1 — `SQRT`.** `[OBSERVED]` **M** Currently a trap (1.1). Map to libm `sqrt`.
- [ ] **6.2 — `SIN`.** `[READ]` **S** libm `sin`.
- [ ] **6.3 — `COS`.** `[READ]` **S** libm `cos`.
- [ ] **6.4 — `LN`.** `[READ]` **S** libm `log`.
- [ ] **6.5 — `EXP`.** `[READ]` **S** libm `exp`.
- [ ] **6.6 — `ARCTAN`.** `[READ]` **S** libm `atan`.
- [ ] **6.7 — `ABS` (INTEGER + REAL).** `[OBSERVED]` **S** Currently a trap (1.1). Integer path inline, REAL path inline or libm `fabs`.

---

## 7. Predeclared identifiers — strings (do AFTER the string representation is settled)

All depend on the `LSTRING` / `STRING` / `SUPER ARRAY` memory layout. Settle that
first or these will be built on sand.

- [ ] **7.1 — Decide and implement the `LSTRING`/`STRING` representation.** `[INFERRED]` **L**
  Length-prefixed (`LSTRING`) vs fixed (`STRING`) vs super-array. Blocks 7.2–7.4.
- [ ] **7.2 — `CONCAT`, `COPYSTR`, `COPYLST`.** `[READ]` **M** String build/copy.
- [ ] **7.3 — `INSERT`, `DELETE`, `POSITN`.** `[READ]` **M** Edit/search.
- [ ] **7.4 — `SCANEQ`, `SCANNE`.** `[READ]` **M** Scan-while-equal / not-equal.
- [ ] **7.5 — `ENCODE` / `DECODE`.** `[READ]` **L** Number↔string formatting (libc `sprintf`/`sscanf` under the hood).

---

## 8. Predeclared identifiers — file & extended I/O (largest subsystem)

The grammar doc already flags `FILE OF` runtime I/O as unverified/blocked. This is
the biggest single chunk; expect it to need its own design pass.

- [ ] **8.1 — File-type runtime + buffer-variable model.** `[READ]` **XL**
  Backs everything below; tie to `TEXT`/`INPUT`/`OUTPUT` (3.4).
- [ ] **8.2 — `RESET`, `REWRITE`, `GET`, `PUT`.** `[READ]` **L** Core file ops.
- [ ] **8.3 — `READ`, and `READLN` beyond integer; `WRITE`/`WRITELN` for `REAL`.** `[OBSERVED]` **M**
  `READLN` currently reads integers only; `WRITE`/`WRITELN` don't handle `REAL`.
  Extend the existing printf/scanf hybrid path.
- [ ] **8.4 — `EOF`, `EOL`, `EOLN`.** `[READ]` **M** Stream predicates.
- [ ] **8.5 — `ASSIGN`, `CLOSE`, `DISCARD`, `READFN`, `READSET`.** `[READ]` **L** Extended I/O verbs.
- [ ] **8.6 — `FILEMODES`, `SEQUENTIAL`, `TERMINAL`, `FCBFQQ`.** `[INFERRED]` **L**
  Confirm each one's meaning from the manual body before implementing — several
  are opaque from the identifier list alone.

---

## 9. Cross-cutting / infrastructure

- [ ] **9.1 — REAL codegen depth.** `[READ]` **M**
  README calls REAL "limited codegen support." Sections 4.3/4.4 and all of 6 hang
  off this. Audit and harden REAL before (or alongside) the math intrinsics.
- [ ] **9.2 — Predeclared-identifier registration mechanism.** `[INFERRED]` **S**
  Today builtins are scattered (some in `_setup_builtins`, some as dedicated AST
  nodes `AdrExpr`/`SizeofExpr`/`UpperExpr`, some hand-declared `extern`). Consider
  one table the type checker and codegen share, so "registered but no codegen"
  traps (1.1) can't recur. The manual says predeclared identifiers are
  *re-definable* by the programmer — model that (don't hard-reserve the names).
- [ ] **9.3 — Test fixtures for every closed item.** `[INFERRED]` **S (ongoing)**
  Each grammar item → a `should_pass`/`should_fail` fixture; each intrinsic → a
  codegen test (and a build/run test where a runtime is involved). Keeps the
  grammar doc, parser, and runtime honest against each other.
- [ ] **9.4 — Keep `docs/ebnf_grammar.md` in sync.** `[INFERRED]` **S (ongoing)**
  Every grammar change above should update the EBNF doc and its change log in the
  same commit, with the right evidence grade.

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
