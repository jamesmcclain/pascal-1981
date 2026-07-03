# Follow-ups / tracked tech-debt

A home for known, non-blocking issues we have consciously decided to defer, so
they are not lost. Each item states what it is, where it lives, why it matters,
a suggested resolution, and how to verify the fix. Status is one of OPEN /
IN-PROGRESS / DONE.

These are not bugs that produce wrong output today; they are seams worth
closing when the surrounding code is next touched. Resolved items are moved to
`docs/old/old-followups.md` once they ship (most recently the CLI progress
chatter, item 4 here — the `Parsing ...`, `Type checking...`, `Generating LLVM
IR...`, and `Wrote ...` prints in `compile_to_llvm.py` and `compile_to_ptx.py`
are now gated behind `-v`, leaving stderr clean on success; before that the
launch-bound /
loop-hint channel, item 8 here — the `tuning-hints` feature adds
`[MAXNTID(x[,y[,z]])]` / `[REQNTID(x[,y[,z]])]` / `[MINCTASM(n)]` attributes on
exported device kernel procedures, lowered to NVVM launch-bound facts that
surface as `.maxntid`/`.reqntid`/`.minnctapersm` PTX directives, and a
`{$UNROLL n}` metacommand lowered to self-referential `llvm.loop.unroll.count`
metadata on the loop it precedes; design note in `docs/tuning-hints.md`; before
that the super-array
bound-metadata item, item 1 here — long-form `NEW(p, u)` now records the
dynamic upper bound in an 8-byte block header, `UPPER(p^)`/`LOWER(p^)` read
it back, `DISPOSE` frees from the header, `$INDEXCK` checks against it, and
DEVICE code rejects `UPPER(p^)` on super arrays so kernel `.ptx` artifacts
keep the drop-in bare-pointer ABI; design record in
`docs/super-array-bounds-abi.md`; before that the Python
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

## 4. CLI progress chatter is emitted even without --verbose [DONE]

*(Moved to `docs/old/old-followups.md` when shipped.)*

---

## 9. docs/device-code claims need evidence grading before they drive work [OPEN]

**Where.** `docs/device-code/KERNEL_ANALYSIS.md`,
`docs/device-code/OPTIMIZATION_GUIDE.md`,
`docs/device-code/DETAILED_COMPARISONS.md`.

**What.** The analysis mixes observed artifacts (PTX listings, instruction
counts) with unsourced performance narrative: cycle-count models for a virtual
ISA that `ptxas` re-schedules, a "15-20x" pipelining projection walked back to
10-25% in the same section, and CUDA-comparison ratios whose nvcc
version/flags are not recorded. Since then, the compiler gained stock LLVM
optimization plumbing, language-level loop unrolling, tuning hints, kernel
parameter attributes, and range metadata support. That leaves only a narrower
set of potentially useful optimization threads: shared-memory tiling/caching,
deeper LICM/register-reuse opportunities, and a check on whether LLVM/NVPTX
already covers any remaining scheduling wins.

**Why it matters.** Per the repo's own anti-confabulation discipline
(OBSERVED / DOCUMENTED / INFERRED), the guide currently reads as more
authoritative than its evidence supports, and its costliest recommendations
(hand-rolled software pipelining, backend unroller) are superseded by newer
compiler features.

**Suggested resolution.** Annotate each claim with an evidence grade; record
the nvcc version and flags behind the comparison tables or regenerate them;
strike or demote the sections superseded by running the stock LLVM pipeline;
and carry forward only the remaining live ideas above as explicit follow-ups if
they still matter after a current benchmark pass.

**How to verify.** A pass over the three files leaves no ungraded quantitative
claim; comparison tables are reproducible from a committed script; any retained
optimization thread is backed by a current benchmark or an open compiler gap.

---

## 10. PTX pass pipeline may be missing target-specific IR passes (ld.global.nc, mul.wide.u32 never observed) [OPEN]

**Where.** `src/pascal1981/compile_to_ptx.py::llvm_ir_to_ptx` (the `opt_level`
pass-manager plumbing added for the O2-pipeline item).

**What.** Three separate frontend facts that should, per the reasoning in the
now-archived O2-pipeline/`!range`/kernel-parameter-attribute items, help the
NVPTX backend make better instruction-selection choices were each tested
empirically against this repo's pinned `llvmlite==0.47.0` (LLVM 20.1.8) and
**none produced the expected effect**:

- `!range` metadata on `tid`/`ctaid`/`ntid`/`nctaid` sreg reads: no change to
  `mul.wide.s32` vs `mul.wide.u32` selection, on both shipped examples and a
  minimal synthetic repro, at `--opt-level 2`.
- `readonly`/`nocapture` on a provably-unwritten kernel buffer parameter: no
  `ld.global.nc` (read-only-cache) selection on a synthetic read/write-buffer
  kernel built specifically to trigger it, at `--opt-level 2`.
- `noalias` on kernel buffer parameters (behind `-f noalias-kernel-params`):
  no observable PTX difference on the mandelbrot example at `--opt-level 2`
  (though that specific kernel may simply have no overlapping-buffer op to
  vectorize across, so this one data point is weaker evidence than the other
  two).

**Why it matters.** `llvm_ir_to_ptx`'s pipeline currently runs
`PassBuilder::buildPerModuleDefaultPipeline` (via llvmlite's
`create_pass_builder(tm, pto).getModulePassManager().run(...)`) over the IR,
then calls `TargetMachine.emit_assembly` directly. That is a bare mid-level
IR optimization pipeline; it is plausible (not confirmed — this is an
INFERRED hypothesis, not an OBSERVED fact) that NVPTX-specific IR passes such
as `NVPTXLowerArgs` (which is understood to be where `ld.global.nc`
selection and pointer-parameter-attribute-driven decisions happen) are
normally inserted by a full `TargetMachine::addPassesToEmitFile` codegen
pipeline (the one `clang -O2 -target nvptx64...` or `llc` would run) rather
than by the bare per-module IR pipeline this code builds. If so, three
already-shipped, correctness-safe, well-tested frontend facts (`!range`,
`readonly`/`nocapture`, `noalias`) are currently inert cost with no realized
benefit on this toolchain, which would be worth knowing before recommending
any of them as a basis for further work (e.g. the `docs/device-code/`
evidence-grading item).

**Suggested resolution.** Investigate whether llvmlite exposes (or can be
made to expose) the NVPTX-specific IR-level passes that a full
`addPassesToEmitFile` pipeline would insert ahead of instruction selection
(possibly via `TargetMachine`'s legacy `PassManager` codegen path instead of,
or in addition to, the new-pass-manager `PassBuilder` used today), or
determine that llvmlite's binding genuinely does not expose that layer, in
which case this should be downgraded from "OPEN, worth investigating" to
"DOCUMENTED LIMITATION of the llvmlite binding" and cross-referenced from the
three attribute-adding items in `docs/old/old-followups.md`.

**How to verify.** Re-run the same three synthetic/example probes already
described in `docs/old/old-followups.md`'s kernel-parameter-attributes entry
after any pipeline change, and confirm `ld.global.nc`/`mul.wide.u32`/a
noalias-driven PTX difference actually appears where the theory predicts it
should; if it does, add a regression test pinning the (now working)
selection; if llvmlite genuinely cannot reach that layer, record that as the
resolution instead and close this as WON'T-FIX with the reason documented.

---

## 11. Launch-bound attributes accept out-of-range dimensions with no architectural check [OPEN]

**Where.** `type_checker.py::_check_launch_bound_attrs` (validates
`[MAXNTID(...)]`/`[REQNTID(...)]`/`[MINCTASM(...)]`); contrast with
`codegen/exprs.py::_NVVM_SREG_MAX`, which already encodes the relevant CUDA
architectural ceilings for a different purpose (`!range` metadata on sreg
*reads*).

**What.** `_check_launch_bound_attrs` only validates that each dimension
argument is a positive integer literal; it does not check the value against
any architectural ceiling. `[MAXNTID(2000, 2000, 2000)]` type-checks and
compiles today, producing a `.maxntid 2000, 2000, 2000` PTX directive for
block dimensions CUDA cannot actually schedule (x/y max 1024 threads, z max
64, per the same CUDA Compute Capabilities ceilings `_NVVM_SREG_MAX` already
cites `[DOCUMENTED]`). Nothing in this compiler catches it; only `ptxas`
(outside this compiler, not run by any existing test) would, if it catches it
at all rather than silently misbehaving.

**Why it matters.** Low severity — nothing here was ever claimed to be
bound-checked, so this is a hardening gap, not a broken promise the way the
underscored-key bug was. But it is a real, reachable footgun: a user asking
for an impossible block size gets silent acceptance all the way through this
compiler's own pipeline, discovering the mistake only downstream.

**Suggested resolution.** Reuse `exprs.py`'s `_NVVM_SREG_MAX` (or a shared
constant lifted to a common location both `type_checker.py` and `exprs.py`
can import, to avoid a second copy of the same architectural table drifting
out of sync) to bound-check `MAXNTID`/`REQNTID` dimension values per axis
(x/y ≤ 1024, z ≤ 64) at type-check time, and `MINCTASM` against whatever
ceiling is appropriate for `minnctapersm` (check the PTX ISA reference before
picking a number rather than guessing). Emit a type error, not a silent
clamp, on an out-of-range literal — consistent with this feature's existing
"dimensions must be positive integer literals" discipline of catching
mistakes at compile time.

**How to verify.** Parser/type-check fixtures: an in-range `MAXNTID`/`REQNTID`
still accepts; an out-of-range one (e.g. `MAXNTID(2000)`, `MAXNTID(1,1,100)`)
now rejects with a clear message citing the axis ceiling; existing
`tests/test_tuning_hints.py` fixtures stay green (all currently use in-range
values, e.g. the new 3-dimension `(8,8,4)` regression test added alongside
item 8's underscore-key fix).

---

# Possible follow-ups (unconfirmed — survey only, not yet promoted)

The items below are **not** vetted the way items 1-10 above are: each is a
pointer into an older planning/design doc under `docs/old/` where the text
itself says the work is deferred, open, or unverified, but none of these have
been re-confirmed against the current tree, re-scoped, or given a suggested
resolution/verification recipe. Treat each as "worth a look before assuming
it's still true" rather than "ready to implement." Promote an item to the
numbered list above (with Where/What/Why/Suggested resolution/How to verify)
once it has been re-checked against current code and is actually being
scheduled.

- **`AdsExpr` value form still carries a `{ptr, i16}` segment-tagged struct
  instead of a bare flat pointer**, and `coerce_arg`'s silent segment-drop
  when passing that struct to a flat-pointer parameter was supposed to become
  a type error per `docs/old/ads-implementation-plan.md` §6.3's design intent
  ("Step 4b", still marked open there) but apparently does not. Of this
  survey's items, this is the one that looks most like a real, live
  correctness gap (a silent semantic drop) rather than a deferred feature —
  worth re-checking first.
- **AMDGPU datalayout/alloca-addrspace hygiene** — `docs/old/cuda-kernel-prescription.md`
  §6 says "remains open... not yet done"; consistent with a comment seen
  directly in `codegen/exprs.py` during this session ("AMDGPU dimension
  plumbing is deferred... rather than inventing a half-wrong dispatch-ptr
  decode"), so this gap is corroborated in the current source, not just the
  old plan doc.
- **`FORALL` parallel-iteration sugar** (§4.4) and a **broader index/real
  width change** (§4.5) — both explicitly deferred in
  `docs/old/milestone-c-parallel-execution-plan.md` and never picked back up.
- **CPU-device (`DEVICE=cpu`) support for the shipped `device_ptx` examples**
  — blocked on rewriting the example kernels to be grid-stride first;
  `docs/old/CPU_DEVICE_TODO.md` describes this as deferred pending sign-off,
  with the kernels deliberately left untouched.
- **Optional `ptxas`/cubin-embedding build route** — `docs/old/device-build-cleanup-plan.md`
  §3.3, marked optional and explicitly not implemented; lowest priority of
  this group since it was never more than a nice-to-have.
- **Enum input for `READ`** — deliberately left unimplemented (loud rejection
  is intentional current behavior); `docs/old/Grand_Unified_Checklist.md`
  leaves it open pending a differential probe against the genuine 1981
  compiler to establish whether the dialect even supports it.
- **Lexer double-`$ELSE` handling and quote-awareness inside skipped `$IF`
  blocks** — flagged `[UNVERIFIED]` in `docs/old/Grand_Unified_Checklist.md`;
  cheap differential-probe candidates against the vintage compiler, never
  run.
- **`ORIGIN`/`PORT` attributes** — formally closed as out-of-scope, but
  `docs/old/Grand_Unified_Checklist.md` keeps it listed "so the deferral
  stays visible," which reads as an invitation to periodically reconsider
  rather than a final close.

None of the above overlaps with items 5, 6, or 7 (the O2 pipeline,
kernel-parameter attributes, and `!range` metadata work); this list surfaced
from a deliberate sweep of `docs/old/*.md` for stale open threads that never
made it into this tracker, done alongside that work but out of its scope.
