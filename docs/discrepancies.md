# Differential discrepancies

## D-001 — `LOWER` / `UPPER` on `STRING(n)` and `LSTRING(n)`
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
