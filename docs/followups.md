# Follow-ups / tracked tech-debt

A home for known, non-blocking issues we have consciously decided to defer, so
they are not lost. Each item states what it is, where it lives, why it matters,
a suggested resolution, and how to verify the fix. Status is one of OPEN /
IN-PROGRESS / DONE.

These are not bugs that produce wrong output today; they are seams worth
closing when the surrounding code is next touched. Resolved items are moved to
`docs/old/old-followups.md` once they ship (most recently the duplicate
parser-fixture number, item 7 here — `16_for_static.pas` was renumbered to
`19_for_static.pas` so the should_pass corpus indexes uniquely again; before
that, the MAXWORD32 / MAXWORD64 parity constants, which was item 5 here — the
wide unsigned types now predeclare `MAXWORD32` / `MAXWORD64` alongside
`MAXINT32` / `MAXINT64`, gated on `wide-integers`; before that, the wide
same-width WORD/INTEGER signedness mix, item 6, where `_check_word_int_mix`
now covers `WORD32`/`INTEGER32` and `WORD64`/`INTEGER64` at equal rank under
the same `strict-word-int` discipline).


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

## 2. WORD/INTEGER constant exemption: fold constant expressions [OPEN]

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

## 3. ODD(WORD) is rejected but should be accepted [OPEN]

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

## 4. Packaging metadata claims Python 3.8+ but the package cannot run below 3.10 [OPEN]

**Where.** `pyproject.toml` (`requires-python = ">=3.8"` plus the 3.8/3.9
classifiers) and `src/pascal1981/compile_to_ptx.py`.

**What.** Two independent facts pin the real floor at Python 3.10. First, the
declared dependency `llvmlite>=0.47.0` itself requires Python >= 3.10 on PyPI, so
`pip install pascal1981` on 3.8/3.9 cannot resolve. Second,
`compile_to_ptx.py` uses PEP 604 union syntax in a function signature
(`emit_llvm_path: str | None = None`) without `from __future__ import
annotations` (the other modules that use `X | None`, e.g. `features.py`, do have
the future import), so merely importing that module raises `TypeError` on
3.8/3.9 even if llvmlite were somehow satisfied.

**Why it matters.** The metadata advertises support the package does not have;
users on 3.8/3.9 get a confusing resolver or import-time failure instead of a
clear "unsupported Python" message from pip.

**Suggested resolution.** Set `requires-python = ">=3.10"`, drop the 3.8/3.9
classifiers, and (for uniformity) add `from __future__ import annotations` to
`compile_to_ptx.py`. Alternatively, if 3.8 support is genuinely desired, lower
the llvmlite floor and replace PEP 604 annotations — but the simpler metadata fix
matches reality.

**How to verify.** `pip install .` in a 3.9 environment should be refused up
front by pip with a requires-python error rather than failing mid-resolution;
`python3 -c "import pascal1981.compile_to_ptx"` succeeds on every advertised
version.

---

## 5. PTX golden-text assertions are brittle across llvmlite/LLVM versions [OPEN]

**Where.** `tests/test_compile_to_ptx.py` (`st.global.u32`, and by extension the
other exact-mnemonic asserts), `tests/integration/test_device_ptx_artifact.py:46`,
`tests/integration/test_device_mandelbrot_ptx.py:113`.

**What.** These tests assert exact NVPTX mnemonics such as `st.global.u32`.
With llvmlite 0.48 (LLVM 20), which satisfies the declared `llvmlite>=0.47.0`
range, the backend emits `st.global.b32` instead, so 3 tests fail on a fresh,
in-range install even though the generated kernel is correct (verified by
reading the emitted PTX: the store, guard, and index math are all present).

**Why it matters.** CI/users tracking the newest llvmlite see spurious failures;
the failures assert nothing wrong with the compiler itself. There is also no
upper bound on llvmlite in `pyproject.toml`, so this class of drift will recur
with each LLVM major bump.

**Suggested resolution.** Either (a) relax the assertions to accept the
size-typed and bit-typed spellings, e.g. match `st.global.[ub]32` (a small
regex or `assertRegex`), or (b) pin an llvmlite upper bound and bump it
deliberately. Option (a) is preferred: the tests are checking "a global 32-bit
store to the buffer exists," not the exact type suffix.

**How to verify.** Full suite green under both llvmlite 0.47 and 0.48
(`pip install llvmlite==0.47.0` / `==0.48.0`, `pytest tests/test_compile_to_ptx.py
tests/integration/test_device_ptx_artifact.py
tests/integration/test_device_mandelbrot_ptx.py`).

---

## 8. CLI progress chatter is emitted even without --verbose [OPEN]

**Where.** `src/pascal1981/compile_to_llvm.py::main` (the `Parsing ...`,
`Type checking...`, `Generating LLVM IR...`, `Wrote ...` prints to stderr).

**What.** Every invocation prints four progress lines to stderr regardless of
`-v`. The `-v/--verbose` help text says it enables per-declaration logging and
tracebacks, implying the default is quiet.

**Why it matters.** Harmless interactively, but noisy in Makefiles and scripted
pipelines (e.g. the examples' Makefiles), and it makes stderr unusable as a
pure diagnostics channel — a wrapper cannot distinguish "warnings" from routine
chatter without pattern matching.

**Suggested resolution.** Gate the progress lines behind `-v` (or add a
`--quiet` flag if the default chatter is considered a feature). Keep the final
`Wrote <path>` if desired, but route it consistently.

**How to verify.** `pascal1981 ok.pas out.ll 2>err.txt` leaves `err.txt` empty on
success without `-v`; with `-v` the progress lines (and tracebacks on failure)
appear.
