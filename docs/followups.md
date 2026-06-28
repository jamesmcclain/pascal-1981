# Follow-ups / tracked tech-debt

A home for known, non-blocking issues we have consciously decided to defer, so
they are not lost. Each item states what it is, where it lives, why it matters,
a suggested resolution, and how to verify the fix. Status is one of OPEN /
IN-PROGRESS / DONE.

These are not bugs that produce wrong output today; they are seams worth
closing when the surrounding code is next touched. Resolved items are moved to
`docs/old/old-followups.md` once they ship (most recently the device codegen-
quality gap vs `nvcc` — predication, FMA, alignment — which was item 2 here).


---

## 1. Super-array remediation residue and device-heap boundary [OPEN]

**Where.** The D-001/D-002 historical evidence now lives in
`docs/old/discrepancies-super-array.md` and
`docs/old/discrepancies-remediation-plan.md`. Current implementation touchpoints
are `type_checker.py::_check_new_args`, `codegen/runtime_builtins.py::builtin_new`,
string-bound lowering in expression codegen, and DEVICE recission checks in the
type checker.

**What.** The observed D-001/D-002 gaps are remediated for normal host code:
`LOWER`/`UPPER` accept `STRING(n)` and `LSTRING(n)`, and one-dimensional
`SUPER ARRAY` pointer referents accept long-form `NEW(p, upper_bound)`. DEVICE
code intentionally does **not** support heap allocation; `NEW` and `DISPOSE` are
rejected during type checking with a device-code dynamic-allocation diagnostic,
including long-form `NEW(p, upper_bound)`.

**Why it matters.** The shipped support is deliberately narrower than the full
vintage surface. Long-form `NEW` currently covers the one-dimensional
super-array allocation case needed by D-002; it does not imply variant-record
long-form `NEW`, multi-dimensional super-array heap allocation, or GPU/device
heap allocation. Also, allocation sizing for heap super arrays does not yet
establish a general ABI for preserving dynamic upper-bound metadata for later
`UPPER(p^)`-style queries.

**Suggested resolution.** If future work expands super arrays, decide the runtime
representation first: how dynamic bounds are stored, how dereferenced super-array
bounds are recovered, and how kernel buffer bounds are passed. For DEVICE code,
prefer caller-provided buffers and explicit bound metadata over backend-specific
GPU allocator calls unless a real device heap design is approved.

**How to verify.** Keep the existing regression tests for normal-code
`LOWER`/`UPPER`, long-form super-array `NEW`, DEVICE string bounds, and DEVICE
heap recission. Add new differential probes before expanding beyond the current
subset, especially for multi-dimensional super arrays, `UPPER(p^)`, and any
variant-record long-form `NEW` behavior.

---

## 3. WORD/INTEGER constant exemption: fold constant expressions [OPEN]

**Where.** `type_checker.py::_is_constant_integer_expr` (consulted by
`_check_word_int_assign` and `_check_word_int_mix`).

**What.** The IBM Pascal 2.0 manual (Elementary Types, p.6-5) exempts INTEGER
*constants* from the WORD/INTEGER assignment and expression-mix restrictions:
"INTEGER type constants change to WORD type if necessary, but not INTEGER
variables." Our constant detector currently recognizes only integer *literals*
(including unary `+`/`-`) and direct references to named integer `CONST`s. It
does **not** fold constant *expressions* such as `k + 1`, `2 * SIZE`, or
`SUCC(k)`, so those are treated as non-constant and require an explicit `WRD(...)`
when crossing into WORD.

**Why it matters.** This is slightly *stricter* than the vintage compiler, which
would accept any compile-time-constant INTEGER in a WORD context. It is a
conservative, safe deviation (it never accepts something it should reject), but
it can force a `WRD(...)` the genuine 1981 compiler would not have required.

**Suggested resolution.** Reuse/extend a single constant-folding pass for
integers (the array-bound and literal-range paths already fold pieces of this)
and have `_is_constant_integer_expr` return True whenever the expression folds to
a compile-time integer constant. Keep the literal/named-CONST fast path.

**How to verify.** Add rows to `tests/test_conversion_matrix.py` for
`w := k + 1` and `f(k + 1)` (constant expression into WORD) asserting ACCEPT, and
confirm `tests/test_word_int_strictness.py` still rejects genuine variables.

---

## 4. ODD(WORD) is rejected but should be accepted [OPEN]

**Where.** `builtins_registry.py` registers `ODD` as `FunctionType('ODD',
[('n', INTEGER_TYPE)], BOOLEAN_TYPE)`; the argument check rejects a WORD actual.

**What.** The manual states "the ODD function for INTEGER and WORD values"
(Elementary Types, BOOLEAN, p.6-6), but `ODD(w)` for `w: WORD` is currently a
type error ("expected INTEGER, got WORD").

**Why it matters.** A small vintage-conformance gap: a faithful program that
calls `ODD` on a WORD is wrongly rejected. It is intentionally left out of the
WORD/INTEGER strictness change set to keep that change coherent, and is pinned as
a KNOWN GAP in `tests/test_conversion_matrix.py::TestManualKnownGaps`.

**Suggested resolution.** Accept INTEGER and WORD for `ODD` (special-case it like
the other ordinal-flexible intrinsics, or widen its registered parameter type to
the integer family). `ODD` only needs the low bit, so the lowering is
signedness-independent.

**How to verify.** Flip `TestManualKnownGaps::test_odd_accepts_word_is_a_known_gap`
to assert ACCEPT (and add a build-and-run parity check for `ODD(WORD)` vs
`ODD(INTEGER)`).

---

## 5. MAXWORD32 / MAXWORD64 parity constants [OPEN]

**Where.** `builtins_registry.py` (constant registration) and
`codegen/base.py` (`self.constants` seeding), alongside `MAXINT32`/`MAXINT64`.

**What.** The wide *signed* types ship with `MAXINT32`/`MAXINT64`, but the new
wide *unsigned* types `WORD32`/`WORD64` do not yet have `MAXWORD32`
(`4294967295`) / `MAXWORD64` (`18446744073709551615`) predeclared constants.

**Why it matters.** Minor parity gap only. The types are fully usable without
them (literals, variables, widening, arithmetic, and unsigned `WRITE` all work);
this is a convenience constant, deferred to avoid the unsigned-constant width
selection in the codegen const path (`MAXWORD64` needs an i64 whose value exceeds
the signed i64 max).

**Suggested resolution.** Seed `MAXWORD32`/`MAXWORD64` in both the checker
registry (gated on `wide-integers`, as `MAXINT32`/`MAXINT64` are) and the codegen
constants, verifying `_const_ir` emits them at i32/i64 with the correct unsigned
bit pattern (follow how `MAXWORD = 65535` is already emitted as an i16).

**How to verify.** Add a build-and-run check that `WRITELN(MAXWORD32)` prints
`4294967295` and `WRITELN(MAXWORD64)` prints `18446744073709551615`.

---

## 6. WORD32/INTEGER32 (same-width) signedness mix is undiagnosed [OPEN]

**Where.** `type_checker.py::_check_word_int_mix` (fires only for the 16-bit
`WORD`/`INTEGER` pair); `type_system.py::binary_op_result_type` resolves a
same-width unsigned/signed mix to the unsigned type.

**What.** The vintage WORD/INTEGER (16-bit) expression mix warns (and errors
under `-f strict-word-int`). The analogous *wide* same-width mixes
(`WORD32`/`INTEGER32`, `WORD64`/`INTEGER64`) silently resolve to the unsigned
type with no diagnostic.

**Why it matters.** These are extension types outside the 1981 manual, so there
is no vintage rule to conform to; leaving them undiagnosed is a deliberate, safe
default. But a user who opted into `-f strict-word-int` might reasonably expect
the same signedness discipline at all widths.

**Suggested resolution.** If desired, generalize `_check_word_int_mix` to the
full WORD-family/INTEGER-family at equal rank, keeping the INTEGER-constant
exemption, behind the existing `strict-word-int` flag.

**How to verify.** Add matrix rows for `WORD32 + INTEGER32 variable` asserting a
warning by default and an error under `strict-word-int`.
