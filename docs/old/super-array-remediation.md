# Super-array & string-bounds remediation (archived)

The completed D-001 (string `LOWER`/`UPPER`) and D-002 (long-form `NEW`)
remediation, plus the follow-on bound-header ABI work. Concatenated in
narrative order: the probe records (what was wrong), the remediation plan
(what was done, in phases), and the bound-header ABI context (the follow-on
and forward-looking extension guidance).

## Differential discrepancies

### D-001 — `LOWER` / `UPPER` on `STRING(n)` and `LSTRING(n)` — remediated
- **Status:** remediated in modern normal-code type checking and codegen by `6188cfb Remediate string bound intrinsics`; covered by `tests/test_codegen_strings_bounds.py::TestStringLowerUpperSemantics` `[OBSERVED]`
- **Probe:**

```pascal
PROGRAM P;
VAR s: STRING(10);
BEGIN WRITELN(LOWER(s)); WRITELN(UPPER(s)) END.
```

and

```pascal
PROGRAM P;
VAR s: LSTRING(10);
BEGIN WRITELN(LOWER(s)); WRITELN(UPPER(s)) END.
```

- **Behavior targeted:** `LOWER` / `UPPER` on string-like super arrays
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** accepted both probes; `STRING(10)` printed `1` then `10`, and `LSTRING(10)` printed `0` then `10` `[OBSERVED]`
- **Modern (reimpl @ device-code, 2026-06-20):** rejected both probes during type checking with `Function 'LOWER' expects an array variable` / `Function 'UPPER' expects an array variable` `[OBSERVED]`
- **Adjudication:** the manual text describes `STRING` and `LSTRING` as super-array forms, so the vintage acceptance is consistent with the documented dialect. The modern compiler currently treats these forms as string types rather than array-like super arrays for `LOWER` / `UPPER`. `[INFERRED]`
- **Severity:** semantic gap in super-array/string interaction; affects `LOWER` / `UPPER` usability on `STRING(n)` and `LSTRING(n)` values.

### D-002 — `NEW` long form on a `SUPER ARRAY` pointer referent — remediated
- **Status:** remediated in modern normal-code type checking and codegen by `7c4842c Remediate super array NEW allocation`; covered by `tests/test_runtime_fixes.py::TestNewAllocationSize` `[OBSERVED]`
- **Probe:**

```pascal
PROGRAM P;
TYPE VECT = SUPER ARRAY [0..*] OF INTEGER;
VAR p: ^VECT;
BEGIN NEW(p, 10) END.
```

- **Behavior targeted:** long-form `NEW` for super-array allocation
- **Class:** REJECT/ACCEPT
- **Vintage (1981):** accepted; compiled through pas1/pas2/link and produced `t034.exe` `[OBSERVED]`
- **Modern (reimpl @ device-code, 2026-06-20):** rejected in type checking with `NEW expects 1 argument, got 2` `[OBSERVED]`
- **Adjudication:** the manual text states that if a variable is a super array type, the long form of `NEW` must be used, and that all upper bounds must be given for super arrays. The vintage acceptance matches that documented behavior. `[READ]`
- **Severity:** missing long-form `NEW` support for super-array allocation; blocks faithful vintage-style heap allocation for open arrays.

## Discrepancy remediation plan

Source discrepancy log: `docs/old/discrepancies.md`.

This plan covers the currently observed differential gaps only. Evidence grades in this document mean:

- `[OBSERVED]`: observed in vintage/modern probes already recorded in `docs/old/discrepancies.md`.
- `[READ]`: read from the IBM Pascal manual text.
- `[PLANNED]`: implementation plan, not yet observed.

### Scope

Initial focus: make the remediations work in normal, non-`DEVICE` Pascal code.

Later focus: add low-hanging support for the same constructs in `DEVICE` code where the semantics are simple and do not require host heap/runtime machinery.

Current discrepancies:

1. `D-001`: `LOWER` / `UPPER` on `STRING(n)` and `LSTRING(n)`.
2. `D-002`: long-form `NEW(p, upper_bound, ...)` for pointers to `SUPER ARRAY` referents.

### Progress ledger

- Phase 1 completed by `6188cfb Remediate string bound intrinsics`.
- Phase 2 completed by `7c4842c Remediate super array NEW allocation`.
- Phase 3 completed by `5dc7fee Document discrepancy remediation status`.
- Phase 4 completed by `fc97361 Add device string bound coverage`; DEVICE code accepts `LOWER` / `UPPER` on `STRING(n)` and `LSTRING(n)` as constant bound reads without host runtime leakage.
- Phase 5 completed by the follow-up DEVICE heap-recission commit; DEVICE `NEW` / `DISPOSE`, including long-form super-array `NEW`, are rejected before codegen with a clear dynamic-allocation diagnostic.

### Phase 0 — Baseline preservation

- Add regression tests that reproduce the exact observed probes from `docs/old/discrepancies.md` before changing implementation.
- Keep tests separated by behavior:
  - `LOWER` / `UPPER` on `STRING(10)`.
  - `LOWER` / `UPPER` on `LSTRING(10)`.
  - `NEW(p, 10)` where `p: ^VECT` and `VECT = SUPER ARRAY [0..*] OF INTEGER`.
- Keep vintage-observed expectations in comments or fixture names, not as invented broader claims.

Acceptance:

- Existing full suite still passes.
- New failing tests clearly map to `D-001` and `D-002` before implementation.

### Phase 1 — Normal-code remediation for D-001: `LOWER` / `UPPER` on strings

#### Required behavior

Observed vintage behavior:

- `STRING(10)`: `LOWER = 1`, `UPPER = 10`.
- `LSTRING(10)`: `LOWER = 0`, `UPPER = 10`.

#### Type checker changes

Current issue: `infer_expression_type()` only accepts `ArrayType` for `UpperExpr` / `LowerExpr`, so `StringType` and `LStringType` are rejected.

Plan:

- Extend `UpperExpr` / `LowerExpr` handling in `src/pascal1981/type_checker.py`:
  - Accept `ArrayType` as today.
  - Accept `StringType`.
  - Accept `LStringType`.
  - Return `INTEGER_TYPE` for all three.
- Keep non-array, non-string diagnostics unchanged where possible.

#### Codegen changes

Current codegen path in `src/pascal1981/codegen/exprs.py` already has a general bound path for type expressions with `lower_bound` / `upper_bound`, but verify `StringType` / `LStringType` expose usable bounds in the codegen type model.

Plan:

- If codegen already emits constants for `StringType` / `LStringType`, add tests only.
- If not, add explicit bound handling:
  - `StringType(length=n)`: lower `1`, upper `n`.
  - `LStringType(length=n)`: lower `0`, upper `n`.
- Confirm `WRITELN(LOWER(s))` and `WRITELN(UPPER(s))` execute under normal x86 codegen.

#### Tests

Add normal-code tests covering:

- Typecheck acceptance for both forms.
- Executable output:
  - `STRING(10)` prints `1` then `10`.
  - `LSTRING(10)` prints `0` then `10`.
- Existing array `LOWER` / `UPPER` behavior remains unchanged.

Acceptance:

- The D-001 probes compile and run in normal code with vintage-observed output.

### Phase 2 — Normal-code remediation for D-002: long-form `NEW` on super arrays

#### Required behavior

Observed vintage behavior:

```pascal
TYPE VECT = SUPER ARRAY [0..*] OF INTEGER;
VAR p: ^VECT;
BEGIN NEW(p, 10) END.
```

Vintage accepts this long-form `NEW`. Manual text states that super-array heap allocation uses long-form `NEW` with all upper bounds supplied.

#### Parser / AST

Current parser already parses procedure calls with multiple actual parameters; the rejection is in type checking/codegen, not parsing.

Plan:

- Reuse existing `ProcCallStmt('NEW', args)` shape.
- Do not introduce a special AST node unless codegen becomes clearer with one.

#### Type checker changes

Current issue: `_check_new_args()` requires exactly one argument.

Plan:

- Change `_check_new_args()` to allow:
  - Short form: `NEW(p)` for non-super-array pointer referents.
  - Long form: `NEW(p, bound1, ..., boundN)` for super-array pointer referents.
- Resolve the first argument type as today and identify the pointer referent.
- For a pointer to a one-dimensional `SUPER ARRAY`, require exactly one upper-bound argument.
- Require each bound argument to be integer-compatible.
- Reject `NEW(p)` when `p` points to a `SUPER ARRAY`, matching the manual requirement that long form must be used. `[READ]`
- Reject extra long-form bounds for non-super-array referents for now, unless existing variant-record long-form `NEW` support is intentionally added in the same tranche.

#### Runtime/codegen changes

Current issue: `builtin_new()` requires exactly one argument and sizes allocation from the static pointee type.

Plan for one-dimensional super arrays:

- Allow `builtin_new(args)` with more than one argument.
- Resolve pointer referent as today.
- If referent is a `SUPER ARRAY [lo..*] OF T` and one bound expression `hi` is provided:
  - Compute element count: `hi - lo + 1`.
  - Compute byte count: element count times element size, plus any currently modeled bound metadata if the implementation stores it for heap super arrays.
  - Call `malloc` with that computed byte count.
  - Store the casted pointer into `p` as today.
- If the modern runtime representation needs to preserve `UPPER(p^)` later, decide whether bound metadata must be stored. Keep this explicit: allocation-only acceptance is not enough if dereferenced bounds become a user-visible feature.

#### Tests

Start with the exact D-002 acceptance probe.

Then add normal-code behavioral tests, if supported by parser/designator handling:

- Assign and read elements through `p^[i]` after `NEW(p, 10)`.
- If pointer dereference into `LOWER` / `UPPER` is supported later, verify `UPPER(p^) = 10`.

Acceptance:

- `NEW(p, 10)` for pointer-to-super-array typechecks and codegens in normal code.
- Existing `NEW(p)` behavior for ordinary pointers remains unchanged.

### Phase 3 — Normal-code cleanup / documentation — done

- Mark D-001 and D-002 as remediated in `docs/old/discrepancies.md` once tests pass. `[DONE]`
- Preserve the observed vintage notes; add modern remediation commit/test references rather than deleting the discrepancy history. `[DONE]`
- Add grammar/design notes if long-form `NEW` semantics become part of the documented supported subset. `[DONE]`

Acceptance:

- Full test suite passes. `[DONE before Phase 3 doc commit: 756 passed, 63 subtests passed]`
- `docs/old/discrepancies.md` distinguishes historical discrepancy from current fixed status. `[DONE]`

### Phase 4 — DEVICE low-hanging fruit for D-001 — done

`LOWER` / `UPPER` on `STRING(n)` and `LSTRING(n)` are compile-time constant bound reads. That makes them good DEVICE candidates.

Plan:

- Permit the same type-checker acceptance in `DEVICE` source context. `[DONE]`
- Codegen constants exactly as normal code: `[DONE]`
  - `STRING(n)`: `1`, `n`.
  - `LSTRING(n)`: `0`, `n`.
- Add DEVICE artifact tests that compile a small device procedure using these values in integer arithmetic. `[DONE: tests/test_device_string_bounds.py]`
- Avoid adding string runtime operations to DEVICE as part of this task. Only bounds are in scope. `[DONE]`

Acceptance:

- DEVICE code can use `LOWER(s)` / `UPPER(s)` on local or parameter `STRING(n)` / `LSTRING(n)` where those types are otherwise legal. `[DONE for local variables]`
- No host runtime externs leak into device IR/PTX for the bounds-only case. `[DONE]`

### Phase 5 — DEVICE low-hanging fruit for D-002 — done

Full heap allocation in DEVICE code is not low-hanging fruit. CUDA/ROCm device `malloc` semantics, allocator availability, address spaces, and lifetime rules are backend-specific and outside the current Milestone-C body-contract work.

Low-hanging plan:

- Keep ordinary `NEW` rejected or unsupported in DEVICE code if it currently depends on host `malloc`. `[DONE]`
- Improve diagnostics for `NEW` in DEVICE code if needed: `[DONE]`
  - Make it explicit that heap allocation is unavailable in DEVICE code, rather than falling through to host runtime lowering.
- Consider type-check-only acceptance for declarations involving `^SUPER ARRAY` if no allocation occurs. `[DONE: declarations remain accepted; allocation calls are rejected]`
- Do not lower `NEW(p, upper)` to GPU allocator calls in the first DEVICE tranche. `[DONE]`

Possible later DEVICE extension, not first pass:

- For `ADS(GLOBAL) OF SUPER ARRAY [lo..*] OF T`, prefer caller-provided buffers and explicit bounds over device heap allocation.
- Treat super-array bounds as ABI metadata or explicit scalar parameters for kernels, not as `NEW`-allocated storage.

Acceptance:

- DEVICE code does not accidentally emit host `malloc`/`free` for `NEW`. `[DONE: rejected before codegen]`
- Any unsupported `NEW` use in DEVICE code receives a clear diagnostic. `[DONE]`

### Suggested implementation order

1. Add failing normal-code tests for D-001.
2. Fix typechecker/codegen for `LOWER` / `UPPER` on `StringType` and `LStringType`.
3. Add failing normal-code tests for D-002.
4. Fix typechecker long-form `NEW` acceptance for pointer-to-super-array.
5. Fix normal-code `builtin_new()` allocation sizing for one-dimensional super arrays.
6. Update discrepancy statuses.
7. Add DEVICE bound-only support/tests for D-001 if not already covered by the shared implementation.
8. Add explicit DEVICE diagnostics for `NEW` rather than attempting allocator support.

## Super-array bound-header ABI — remediation context (archived)

Archived companion to `docs/super-array-bounds-abi.md`. The reference doc
upstairs records *the representation* (the heap layout, the soundness
boundary, the device-code boundary); this file holds the time-bound material
that originally framed it — the pre-fix problem statement and the
forward-looking design guidance for any future extension.

### Problem (pre-fix baseline)

Long-form `NEW(p, upper_bound)` allocated the right number of bytes for a
`^SUPER ARRAY [low..*] OF T` referent but discarded the bound: nothing in
the allocated block recorded `upper_bound`, so no later `UPPER(p^)`-style
query was possible, and `UPPER`/`LOWER` had no dereferenced form at all.
[OBSERVED]

Worse, `$INDEXCK` guessed the bounds of a `[low..*]` type as `(low, low)`
(the `_array_bounds_or_none` fallback evaluated a missing high bound as the
low bound), so any `p^[i]` with `i > low` aborted at run time — the shipped
long-form `NEW` produced storage that could only ever be indexed at its
lower bound with checking on. [OBSERVED — pinned by
`tests/test_super_array_bounds.py::TestBoundHeaderRuntime::test_full_range_write_no_longer_aborts`,
which fails on the pre-change tree]

### If this is ever extended

Decisions a future multi-dimensional / parameter-carrying design must make:

- how multiple dynamic bounds are stored (one header word per starred
  dimension is the natural extension of this layout);
- how bounds are recovered for super-array *parameters* (a hidden bound
  argument changes the call ABI and needs a differential probe of the
  vintage compiler's calling convention first);
- how kernel buffer bounds are passed (stay with explicit parameters unless
  a real device heap design is approved).

[INFERRED — design guidance, not evidenced behavior]

Historical evidence for the D-001/D-002 remediation that preceded this work
lives earlier in this file (the probe records and remediation plan sections above).

