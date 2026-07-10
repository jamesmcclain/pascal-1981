# Tuning hints: launch bounds and per-loop unroll

Reference for the `tuning-hints` feature. Both channels are *hint plumbing
only*: they encode programmer intent that LLVM cannot invent, and every
transform remains LLVM's — no bespoke unroller, pipeliner, or scheduler lives
in this compiler.

Claims are graded per the repository's anti-confabulation discipline
(OBSERVED / DOCUMENTED / INFERRED). The originating follow-up item and the
bug history behind the launch-bound lowering correction are archived in
`docs/old/tuning-hints-design-rationale.md`.

## Gating

Neither channel is vintage IBM Pascal, so both sit behind the registered
feature `tuning-hints` (`-f tuning-hints`, listed by `--list-features`). The
feature participates in the extended umbrella, so it is on by default inside
`DEVICE` code (whose feature baseline is the extended set) and under
`--dialect extended`, and rejected under the faithful vintage default.
[OBSERVED — `features.py`, `tests/test_tuning_hints.py::TestFeatureGating`]

## Launch bounds: `[MAXNTID(x[,y[,z]])]`, `[REQNTID(x[,y[,z]])]`, `[MINCTASM(n)]`

Attribute-section syntax on procedure headers, reusing the existing bracket
attribute grammar (like `[SPACE(...)]`); the names are contextual identifiers,
so vintage programs using `maxntid` etc. as ordinary identifiers still parse.
[OBSERVED]

Validity (type checker): device code only; exported kernel PROCEDUREs only
(a kernel entry cannot be a FUNCTION, matching the existing entry-shape rule);
1-3 positive integer literal dimensions for the ntid forms, exactly one for
MINCTASM. Dimensions are literals-only so the annotation values are
compile-time facts. [OBSERVED]

Lowering (only at a real GPU kernel entry, alongside `ptx_kernel`): both
encodings LLVM has used are emitted —

- `"nvvm.maxntid"="x[,y,z]"`-style *function string attributes*.
- the legacy per-dimension `!nvvm.annotations` entries (`maxntidx`,
  `maxntidy`, `maxntidz`, `reqntidx`, ..., `minctasm`) — note **no
  underscore** between the name and the axis letter.

On the pinned `llvmlite==0.47.0`/LLVM 20.1.8 toolchain the legacy,
correctly-spelled (un-underscored) annotation is the one carrying the
correctness burden — it alone is sufficient to produce `.maxntid`/`.reqntid`;
the string-attribute form alone produces nothing. Both are still emitted
(belt-and-suspenders for a different LLVM build that might prefer the
string-attribute form); with both present, each PTX directive appears
exactly once. [OBSERVED — `decls.py`'s `_LAUNCH_BOUND_KEYS`,
`tests/test_tuning_hints.py`]

The bug history behind this correction is archived in
`docs/old/tuning-hints-design-rationale.md`.

llvmlite has no string-attribute API, so the `key="value"` token is added by
shadowing the `FunctionAttributes` instance's `_known` whitelist; the token
renders verbatim in the `define` attribute list, which is LLVM's
string-attribute syntax, and round-trips through `parse_assembly`/`verify`.
[OBSERVED]

On the x86 CPU-device parity path there is no kernel entry, so the attributes
are inert (accepted, then ignored) — the same source compiles for both
targets. AMD (`amdgcn`) launch bounds use a different attribute scheme that is
not implemented; codegen fails loudly rather than dropping the hint.
[OBSERVED]

## Per-loop unroll: `{$UNROLL n}`

A metacommand in the existing `$` tier. It must *immediately precede* a
`FOR`, `WHILE`, or `REPEAT` statement (the count is stamped one-shot onto the
next token; the parser rejects a misplaced stamp instead of silently dropping
the hint). `n` is a positive integer (or a `$INCONST` meta-constant name).
[OBSERVED — lexer/parser tests]

Lowering: `llvm.loop.unroll.count(n)` metadata on the loop's back-edge branch.
One subtlety made this nontrivial: LLVM identifies a loop-ID node by skipping
its first operand, which by convention is a *distinct self-reference*; with a
`null` first operand the module verifies but the unroll pass ignores the hint.
llvmlite's uniqued metadata cannot express that cycle, so codegen emits
`!{ null, ... }` and a targeted textual pass (`_selfref_loop_metadata` in
`codegen/__init__.py`) rewrites exactly the null-headed nodes whose payload
references `llvm.loop.*` option strings into `distinct !{ !N, ... }`. Modules
without loop hints pass through byte-identical. [OBSERVED — with the rewrite,
an `{$UNROLL 4}` loop calling an opaque EXTERN shows 4 call sites after
LLVM's O2 pipeline vs 1 without the hint or with the null head; pinned by
`tests/test_tuning_hints.py::test_unroll_hint_fires_under_o2`]

Note that the hint only *fires* when an optimization pipeline actually runs
over the IR: `clang -O2` on the host link line, or (once follow-up item "PTX
path runs no LLVM IR optimization pipeline" lands) the device PTX path. The
metadata is emitted either way; it is inert at -O0. [OBSERVED for the host
pipeline; the PTX-path pipeline is still open follow-up work]

## Drop-in PTX discipline

Modules that use neither channel are unchanged at every level: no metadata, no
function attributes, no PTX directives; the committed `fill_indices` example
regenerates byte-identical. A `.ptx` artifact that was a no-change drop-in
before this feature remains one. [OBSERVED — diff in the verification run and
`test_hint_free_device_unit_ptx_is_unchanged`]
