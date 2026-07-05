# Tuning hints — design rationale (archived)

Archived companion to `docs/tuning-hints.md`. The reference doc upstairs
records *how the feature works*; this file holds the time-bound rationale
that originally framed it — the originating follow-up item and the bug
history behind the launch-bound lowering correction.

## Origin

The `tuning-hints` feature closed follow-up item "No source-level channel
for launch bounds or per-loop hints" (now archived in
`docs/old/old-followups.md`). Both channels are *hint plumbing only*: they
encode programmer intent that LLVM cannot invent, and every transform remains
LLVM's — no bespoke unroller, pipeliner, or scheduler lives in this compiler.

Claims in the reference doc are graded per the repository's anti-confabulation
discipline (OBSERVED / DOCUMENTED / INFERRED).

## Corrected 2026-07 (docs/followups.md item 5/7/6 bundle)

The original claim that the string-attribute form is what LLVM 20/llvmlite
0.48 requires, and that the legacy form uses an underscored key
(`maxntid_x`), was wrong and shipped a real bug: on the LLVM 20.1.8 actually
bundled with the pinned `llvmlite==0.47.0` wheel (re-verified with `pip
freeze`), a minimal `parse_assembly`/`emit_assembly` probe *outside* this
codebase shows the opposite —

- the underscored legacy key (`maxntid_x`) silently produces **no** PTX
  directive at all;
- the correctly-spelled, un-underscored legacy key (`maxntidx`) alone is
  sufficient and produces `.maxntid`;
- the `"nvvm.maxntid"="..."` string attribute alone, with no legacy
  annotation present, produces **nothing**.

[OBSERVED — corrected; probes re-run and codegen fixed in `decls.py`'s
`_LAUNCH_BOUND_KEYS`, `tests/test_tuning_hints.py`] `minnctapersm` was
never affected (`minctasm` has no axis suffix, so both spellings coincide),
which is exactly why it was the one directive that always worked and masked
the bug in `.maxntid`/`.reqntid` for two axes. Both encodings are still
emitted (harmless belt-and-suspenders for a different LLVM build that might
prefer the string-attribute form), but the legacy, correctly-spelled
annotation is the one carrying the correctness burden on this repo's pinned
toolchain.
