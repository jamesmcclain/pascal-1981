# Follow-ups / tracked tech-debt

A home for known, non-blocking issues we have consciously decided to defer, so
they are not lost. Each item states what it is, where it lives, why it matters,
a suggested resolution, and how to verify the fix. Status is one of OPEN /
IN-PROGRESS / DONE.

These are not bugs that produce wrong output today; they are seams worth
closing when the surrounding code is next touched. Resolved items are moved to
`docs/old/old-followups.md` once they ship; the per-item entries there are the
changelog of past resolutions.


---

# Possible follow-ups (unconfirmed — survey only, not yet promoted)

The items below are **not** vetted the way the resolved entries in
`docs/old/old-followups.md` are: each is a pointer into an older
planning/design doc under `docs/old/` where the text itself says the work is
deferred, open, or unverified, but none of these have been re-confirmed against
the current tree, re-scoped, or given a suggested resolution/verification
recipe. Treat each as "worth a look before assuming it's still true" rather
than "ready to implement." Promote an item to a fully specified entry in
`docs/old/old-followups.md` (with Where/What/Why/Suggested resolution/How to
verify) once it has been re-checked against current code and is actually being
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
- **`noalias` kernel parameters / the LAUNCH contract** — `-f
  noalias-kernel-params` is an opt-in feature (not part of the `extended`
  umbrella) that asserts *distinct `ADS(GLOBAL)`/`ADS(CONSTANT)` buffer
  parameters of a kernel entry do not overlap in memory*. This is a promise
  about the *caller* (whatever issues the `LAUNCH`/`cuLaunchKernel` call),
  which this compiler cannot verify at a call site — get it wrong (alias two
  `noalias`-tagged buffers at launch time) and the optimizer may reorder or
  vectorize loads/stores across them, a silent miscompilation. That is why it
  defaults off even inside `DEVICE` code. The attribute-shape tests are in
  `tests/test_kernel_param_attrs.py`; the shipped `align`/`dereferenceable`/
  `readonly`/`nocapture` facts are documented in
  `docs/device-kernel-orientation.md`. Worth re-checking whether the default
  should ever flip, and whether any in-tree launch site can be shown to honor
  the contract.

None of the above overlaps with the O2 pipeline, kernel-parameter attribute,
or `!range` metadata work; this list surfaced from a deliberate sweep of
`docs/old/*.md` for stale open threads that never made it into this tracker,
done alongside that work but out of its scope.
