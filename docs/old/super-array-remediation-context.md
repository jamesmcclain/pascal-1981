# Super-array bound-header ABI — remediation context (archived)

Archived companion to `docs/super-array-bounds-abi.md`. The reference doc
upstairs records *the representation* (the heap layout, the soundness
boundary, the device-code boundary); this file holds the time-bound material
that originally framed it — the pre-fix problem statement and the
forward-looking design guidance for any future extension.

## Problem (pre-fix baseline)

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

## If this is ever extended

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
lives in `docs/old/discrepancies-super-array.md` and
`docs/old/discrepancies-remediation-plan.md`.
