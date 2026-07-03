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
