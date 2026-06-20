# Differential discrepancies

## D-001 — `LOWER` / `UPPER` on `STRING(n)` and `LSTRING(n)` — remediated
- **Status:** remediated in modern normal-code type checking and codegen; covered by `tests/test_codegen_strings_bounds.py::TestStringLowerUpperSemantics` `[OBSERVED]`
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

## D-002 — `NEW` long form on a `SUPER ARRAY` pointer referent
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
