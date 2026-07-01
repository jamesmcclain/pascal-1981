# Follow-ups / tracked tech-debt

A home for known, non-blocking issues we have consciously decided to defer, so
they are not lost. Each item states what it is, where it lives, why it matters,
a suggested resolution, and how to verify the fix. Status is one of OPEN /
IN-PROGRESS / DONE.

These are not bugs that produce wrong output today; they are seams worth
closing when the surrounding code is next touched. Resolved items are moved to
`docs/old/old-followups.md` once they ship (most recently the Python
version-floor packaging metadata, item 4 here — `pyproject.toml` now declares
`requires-python = ">=3.10"` and only lists the 3.10-3.12 classifiers, matching
the real floor set by `llvmlite>=0.47.0`;
`compile_to_ptx.py` already carried `from __future__ import annotations`, so
no source change was needed there; before that, the brittle PTX golden-text
assertions, item 5 — the exact-mnemonic asserts now accept both
`st.global.u32` and `st.global.b32` via `assertRegex(r'st\.global\.[ub]32')`,
verified green under both llvmlite 0.47 and 0.48; before that, the duplicate
parser-fixture number, item 7 — `16_for_static.pas` was renumbered to
`19_for_static.pas` so the should_pass corpus indexes uniquely again; before
that, the build-and-run test prerequisite, item 6 — `tests/support.py` now
builds `runtime/build/libpascalrt.a` automatically via `make -C runtime` on
first import if it is missing; before that, the MAXWORD32 / MAXWORD64 parity
constants, item 5 originally — the wide unsigned types now predeclare
`MAXWORD32` / `MAXWORD64` alongside `MAXINT32` / `MAXINT64`, gated on
`wide-integers`; before that, the wide same-width WORD/INTEGER signedness mix,
item 6 originally, where `_check_word_int_mix` now covers `WORD32`/`INTEGER32`
and `WORD64`/`INTEGER64` at equal rank under the same `strict-word-int`
discipline).


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

---

## 9. PTX path runs no LLVM IR optimization pipeline [OPEN]

**Where.** `src/pascal1981/compile_to_ptx.py::llvm_ir_to_ptx` (and the
`--target ptx` path in `compile_to_llvm.py`).

**What.** The device path is parse → verify → `create_target_machine(cpu=...)`
→ `emit_assembly`. No mid-level pass pipeline (O2/O3) is ever run over the IR,
so LLVM's loop unrolling, LICM, GVN, instruction combining, and load/store
vectorization never fire. The recommendations in
`docs/device-code/OPTIMIZATION_GUIDE.md` §1 (unrolling), §2 (software
pipelining), and §4 (address hoisting) describe hand-implementing transforms
that the stock LLVM pipeline already provides.

**Why it matters.** The kernels we ship are effectively -O0 IR handed straight
to the NVPTX backend. Most of the guide's projected wins are available for the
cost of pipeline plumbing rather than weeks of bespoke backend passes — and a
bespoke unroller/pipeliner would be a maintenance liability duplicating opt.

**Suggested resolution.** After `parse_assembly`/`verify`, run llvmlite's new
pass manager (`PipelineTuningOptions` + `PassBuilder`, O2 default, flag-tunable
via e.g. `--opt-level`) before `emit_assembly`. Note that PTX is virtual
assembly and `ptxas` performs final scheduling/register allocation, so IR-level
cleanup is the right layer; do not hand-implement software pipelining or
PTX-level scheduling (OPTIMIZATION_GUIDE §2/§5) — see item 10 for the frontend
facts the pipeline needs to be effective on memory ops.

**How to verify.** Diff PTX for the fill/mandelbrot examples at O0 vs O2;
existing device tests stay green (adjusting mnemonic-brittle asserts per item
5); optional benchmark on real hardware via `scripts/build-cuda-host.sh`.

---

## 10. Kernel entries carry no parameter facts: noalias / readonly / align / dereferenceable [OPEN]

**Where.** `codegen/decls.py` (kernel-entry emission around
`calling_convention = 'ptx_kernel'`); contrast with `codegen/c_abi.py`, which
already sets attributes on the host C-ABI path.

**What.** Device kernel buffer parameters (`ADS(GLOBAL)` pointers) are emitted
as bare pointers. LLVM cannot itself infer that two buffers do not alias, that
a buffer is never written through, or its alignment — those are facts only the
frontend (Pascal semantics + the LAUNCH contract) can assert. Without them the
optimizer must stay conservative: no `ld.global.v4.f32` vectorization, no
read-only-cache (`ld.global.nc`) selection, limited load reordering.

**Why it matters.** This is the highest-leverage device codegen item and is
orthogonal to LLVM: it is precisely the information LLVM lacks. It also
multiplies item 9 — an O2 pipeline over attribute-free pointers leaves most
memory-op wins on the table.

**Suggested resolution.** (a) `readonly`: the type checker can already prove a
kernel never assigns through a given buffer parameter; plumb that through to a
`readonly` (+ `nocapture`) attribute. (b) `align`/`dereferenceable(n)`: derive
from element type and, where the launch contract fixes a length parameter,
from bounds. (c) `noalias`: define it as part of the LAUNCH contract (distinct
buffer arguments must not overlap), document it, gate behind a feature flag if
there is any doubt about vintage-faithful semantics.

**How to verify.** Unit tests asserting the attributes appear in kernel-entry
IR; PTX-level test that a provably-readonly streamed buffer compiles to
`ld.global.nc.*` at O2; differential run of the mandelbrot/fill examples
confirming identical output.

---

## 11. Device index intrinsics lack !range metadata [OPEN]

**Where.** `codegen/exprs.py` (~line 782), where `THREADIDX_*` / `BLOCKIDX_*` /
`BLOCKDIM_*` / `GRIDDIM_*` lower to `llvm.nvvm.read.ptx.sreg.*` calls.

**What.** The intrinsic calls carry no `!range` metadata. Clang attaches ranges
(e.g. tid.x ∈ [0, 1024), ntid.x ∈ [1, 1025)) so LLVM can prove grid-stride
index math is non-negative and non-overflowing. Without them the backend must
allow negative indices, blocking sign-extension elimination, `mul.wide.u32`
selection, and trip-count reasoning in exactly the loops our kernels use.

**Why it matters.** Frontend-only information, roughly ten lines of codegen,
zero semantic risk, and it feeds every downstream pass (item 9).

**Suggested resolution.** Attach `!range` to each sreg call using the CUDA
architectural limits keyed off `--sm` (conservative sm_70 defaults are fine).
Optionally emit `llvm.assume` for the derived global index when both factors
are range-annotated.

**How to verify.** IR test asserting `!range` on the sreg calls; PTX diff
showing e.g. `mul.wide.u32`/dropped `cvt` instructions in the fill_indices
kernel at O2.

---

## 12. No source-level channel for launch bounds or per-loop hints [OPEN]

**Where.** Kernel-entry emission in `codegen/decls.py` (no
`!nvvm.annotations` beyond kernel marking); the `$`-metacommand tier in
`lexer.py`/parser (currently `$if`/`$message`/push-pop only).

**What.** Two hint channels that LLVM cannot invent because they encode
programmer intent: (a) launch bounds — `maxntid` / `reqntid` / `minctasm`
annotations that let the backend budget registers for a known block size; and
(b) per-loop transform hints — `llvm.loop.unroll.count` etc., the `#pragma
unroll` equivalent. Neither has any surface syntax today.

**Why it matters.** Occupancy tuning (OPTIMIZATION_GUIDE §5/§6) is impossible
without (a); (b) gives users the guide's §1 unrolling benefits selectively
without a bespoke unroller, once item 9 lands. Both are pure hint plumbing —
the transforms remain LLVM's.

**Suggested resolution.** Reuse existing extension syntax: a bracket attribute
on exported device procedures (e.g. `[MAXNTID(256)]`, mirroring the current
attribute grammar) lowered to `!nvvm.annotations`, and a `{$unroll N}`
metacommand attaching `llvm.loop` metadata to the following loop. Gate both
behind a registered feature (they are not vintage IBM Pascal), consistent with
the `--dialect`/`-f` machinery.

**How to verify.** Parser fixtures for the new attribute/metacommand (accept
under the feature, reject under `vintage`); IR tests asserting the annotation
and loop metadata; PTX test that `.maxntid` appears in the kernel directive.

---

## 13. docs/device-code claims need evidence grading before they drive work [OPEN]

**Where.** `docs/device-code/KERNEL_ANALYSIS.md`,
`docs/device-code/OPTIMIZATION_GUIDE.md`,
`docs/device-code/DETAILED_COMPARISONS.md`.

**What.** The analysis mixes observed artifacts (PTX listings, instruction
counts) with unsourced performance narrative: cycle-count models for a virtual
ISA that `ptxas` re-schedules, a "15-20x" pipelining projection walked back to
10-25% in the same section, and CUDA-comparison ratios whose nvcc
version/flags are not recorded. Items 9-12 above deliberately extract only the
parts that survive scrutiny.

**Why it matters.** Per the repo's own anti-confabulation discipline
(OBSERVED / DOCUMENTED / INFERRED), the guide currently reads as more
authoritative than its evidence supports, and its costliest recommendations
(hand-rolled software pipelining, backend unroller) are superseded by item 9.

**Suggested resolution.** Annotate each claim with an evidence grade; record
the nvcc version and flags behind the comparison tables or regenerate them;
strike or demote the sections superseded by running the stock LLVM pipeline.

**How to verify.** A pass over the three files leaves no ungraded quantitative
claim; comparison tables are reproducible from a committed script.
