# Plan: Item 11 — Bound-check launch-bound attribute dimensions

Source: `docs/followups.md`, item 11, "Launch-bound attributes accept
out-of-range dimensions with no architectural check [OPEN]".

Evidence grading follows the repo's own anti-confabulation discipline:
**[OBSERVED]** = read directly in this repo or reproduced with a scratch
probe against the current tree; **[DOCUMENTED]** = stated in an external
primary source (linked below), not verified against this repo's own
behavior; **[INFERRED]** = my deduction, not directly observed;
**[UNVERIFIED]** = flagged explicitly, not to be treated as fact.

No files in the repo were modified to produce this plan. Scratch probes and
fetched reference pages live under `~/dixie-scratch-area/item-11/`.

## 1. What's actually there today

- `type_checker.py::_check_launch_bound_attrs` (around line 1096) validates,
  for `[MAXNTID]`/`[REQNTID]`/`[MINCTASM]`: feature gate, device-only,
  procedure-only (not function), exported-entry-only, arity (1–3 for
  MAXNTID/REQNTID, exactly 1 for MINCTASM), and that every argument folds to
  a **positive integer literal** via `_fold_int_literal_value`. It does
  **not** compare the folded value against any ceiling. **[OBSERVED —
  `src/pascal1981/type_checker.py:1096-1129`]**
- `codegen/exprs.py::_NVVM_SREG_MAX` (around line 784) is a *different*
  table, used only to compute `!range` metadata on `threadIdx`/`blockIdx`/
  `blockDim`/`gridDim` special-register reads:
  `{'X': {'tid': 1024, 'ctaid': 2**31-1}, 'Y': {'tid': 1024, 'ctaid': 65535},
  'Z': {'tid': 64, 'ctaid': 65535}}`. Its own comment grades itself
  `[DOCUMENTED — CUDA architectural limits, not measured against this
  repository's own code/tests]`. **[OBSERVED —
  `src/pascal1981/codegen/exprs.py:775-793`]**
- I reproduced the bug directly against the current tree (not just read
  about it): a scratch program with `typecheck_module` on
  `_impl(' [MAXNTID(2000, 2000, 2000)]')` from
  `tests/test_tuning_hints.py` returns `success=True` with zero errors, and
  the same source compiles all the way through `compile_to_llvm` and
  `llvm_ir_to_ptx` (`cpu='sm_70'`) to a real PTX line
  `.maxntid 2000, 2000, 2000`. I confirmed the same for `MAXNTID(1,1,100)`,
  `REQNTID(1024,1024,64)`, and `MINCTASM(999999)` — all type-check clean.
  **[OBSERVED — scratch run this session, see "How I verified" below]**
- The item's own text is accurate: nothing downstream in this compiler's own
  pipeline (parser, type checker, codegen, `compile_to_ptx.py`'s
  `llvm_ir_to_ptx`) rejects these values; only a real `ptxas` invocation
  (not run by any test in this repo) could ever catch it, and per the PTX
  ISA docs (below) it might not even reject `.minnctapersm` — it may just
  silently ignore it.

## 2. External grounding (so the ceilings aren't guessed)

Fetched directly this session (URLs below); not relying on background
knowledge alone for the numeric claims.

- **PTX ISA, "Performance-Tuning Directives" §11.4.2–11.4.5**
  (https://docs.nvidia.com/cuda/parallel-thread-execution/index.html,
  fetched to `~/dixie-scratch-area/item-11/parallel-thread-execution.html`):
  - `.maxntid nx[,ny[,nz]]`: "the maximum number of threads in the thread
    block... is guaranteed not to be exceeded... Exceeding the maximum
    number of threads results in a runtime error or kernel launch failure."
    Note it bounds the **product** of the three dimensions, not just each
    axis individually. **[DOCUMENTED]**
  - `.reqntid nx[,ny[,nz]]`: launch geometry must equal this exactly, or
    "a runtime error or kernel launch failure" results. Also: **cannot be
    used together with `.maxntid`** in the same entry. **[DOCUMENTED]** —
    this repo's type checker does not currently reject specifying both
    together; that's arguably a second, smaller gap worth folding into the
    same fix (see §4).
  - `.minnctapersm ncta`: "Optimizations based on `.minnctapersm` need
    either `.maxntid` or `.reqntid` to be specified as well. **If the total
    number of threads on a single SM resulting from `.minnctapersm` and
    `.maxntid`/`.reqntid` exceed the maximum number of threads supported by
    an SM then directive `.minnctapersm` will be ignored.**" So an
    out-of-range `.minnctapersm` is documented to be **silently ignored by
    ptxas**, not a hard compile error. **[DOCUMENTED]** — this changes the
    right fix for MINCTASM specifically; see §4.
- **CUDA architectural ceilings** (1024 max per-block threads; x/y axis
  ≤ 1024, z axis ≤ 64; y/z grid ≤ 65535) are exactly what the *existing*
  `_NVVM_SREG_MAX` table in `exprs.py` already encodes, self-graded
  `[DOCUMENTED]` there against the CUDA C Programming Guide's "Compute
  Capabilities" appendix. I did not get a clean fetch of that exact NVIDIA
  page this session (the current-version URL 404s / redirects to a stub;
  saved attempts are in the scratch dir), but an independent, freely
  fetched third-party CUDA device-query cheat sheet corroborates the same
  numbers for a real device (compute capability 6.1 card): "Max dimension
  size of a thread block (x,y,z): (1024, 1024, 64)"
  (https://kdm.icm.edu.pl/Tutorials/GPU-intro/introduction.en/, fetched
  this session). Treat the exact NVIDIA source page as **[UNVERIFIED —
  page not successfully fetched this session]**; the numbers themselves are
  corroborated **[DOCUMENTED — third-party mirror]** and already trusted
  elsewhere in this exact codebase, so reusing them (rather than
  re-deriving them) is the correct move, not a new unverified claim.

## 3. Is this worth doing? Yes, with one adjustment to the suggested resolution

The followup entry's suggested resolution ("reuse `_NVVM_SREG_MAX` to
bound-check MAXNTID/REQNTID per axis, and MINCTASM against `minnctapersm`'s
own ceiling, checked against the PTX ISA reference before picking a number")
is basically right and should proceed for **MAXNTID/REQNTID**. It needs one
correction for **MINCTASM**, now that the PTX ISA text is in hand:

- `.minnctapersm` has no fixed numeric ceiling in the ISA — it's bounded by
  a runtime relationship (registers/shared-memory/thread budget of the
  actual SM) that this frontend cannot know statically, and the ISA itself
  says an infeasible value is silently **ignored**, not rejected. Inventing
  a hard numeric ceiling for it (e.g. "SM count" or "2048/maxntid") would be
  exactly the kind of unsourced numeric confabulation this repo's own
  discipline (see `_NVVM_SREG_MAX`'s self-grading, and
  `docs/tuning-hints.md`'s "Claims are graded" preamble) explicitly warns
  against. **Recommendation: leave MINCTASM's "positive integer literal"
  check as-is; do not add a numeric ceiling for it in this pass.** Document
  why in a comment, citing the ISA's own "will be ignored" language, so a
  future reader doesn't assume the omission is an oversight.

## 4. Concrete resolution

1. **Lift the ceiling table to one shared location** both `type_checker.py`
   and `codegen/exprs.py` can import without introducing a new import
   cycle. `type_checker.py` currently does not import anything from
   `codegen/`; `codegen/exprs.py` is a mixin under `codegen/` that imports
   from `..type_system` and `.base`, not from `type_checker`. The clean
   spot is a new leaf module, e.g. `src/pascal1981/device_limits.py`, with
   no dependencies beyond the standard library, holding:
   ```python
   # CUDA architectural ceilings for compute capability 7.0+ (sm_70+; this
   # repo's device examples target sm_70/sm_86). [DOCUMENTED — CUDA C
   # Programming Guide, "Compute Capabilities" appendix; corroborated
   # against a real-device query, see docs/followups.md item 11 write-up]
   NVVM_AXIS_MAX = {'X': 1024, 'Y': 1024, 'Z': 64}
   NVVM_MAX_THREADS_PER_BLOCK = 1024
   ```
   Both `type_checker.py::_check_launch_bound_attrs` and
   `codegen/exprs.py::_NVVM_SREG_MAX` import from it. `_NVVM_SREG_MAX`'s
   grid-dimension entries (`ctaid`: `2**31-1`/`65535`) are a different
   concern (grid size, not block size) and can stay local to `exprs.py`
   unless a future item needs them too — don't over-generalize the shared
   module beyond what item 11 actually needs.
2. **In `_check_launch_bound_attrs`**, after the existing
   "positive integer literal" check, for `MAXNTID`/`REQNTID` only:
   - Reject any axis value exceeding `NVVM_AXIS_MAX[axis]` (x/y → 1024,
     z → 64), keyed by argument position (arg 0 = x, 1 = y, 2 = z).
   - Reject when the **product** of all given dimensions exceeds
     `NVVM_MAX_THREADS_PER_BLOCK` (1024) — the ISA bounds the product, not
     just each axis; a per-axis-only check would still let `MAXNTID(1024,
     1024, 1)` through fine (1024 total, OK) but would also wrongly *allow*
     something like `MAXNTID(1024, 2)` (2048 total) if only per-axis limits
     were checked, since neither axis alone exceeds 1024. Both checks are
     needed.
   - Emit a type error (not a warning, not a clamp), citing the axis and
     both ceilings it violates, consistent with the existing message style
     (`f"[{name}] dimensions must be positive integer literals"`).
   - Leave `MINCTASM` exactly as today (positive-literal check only), and
     add a one-line comment explaining why per §3.
3. **Do not touch MAXNTID+REQNTID-together validation in this pass** unless
   scope is explicitly widened — it's a real, separately-documented ISA
   rule ("cannot be used in conjunction") but it's a different bug class
   (co-occurrence, not range) than what item 11 asks for. Worth a follow-up
   note, not silent scope creep into this fix.
4. **No codegen change needed.** `codegen/decls.py::_apply_launch_bound_attrs`
   only fires after type-check success; rejecting bad values earlier is
   sufficient and keeps the "PTX drop-in" byte-identical-when-unused
   property intact.

## 5. Tests to add (mirrors item 11's own "How to verify")

In `tests/test_tuning_hints.py`, extend
`TestLaunchBoundParsing`/`TestFeatureGating`-style cases (probably the
`test_launch_bound_arity_and_values_validated` table, which already covers
arity/positivity) with new rejected cases and matching accepted cases:

- Rejected: `MAXNTID(2000)` (x over 1024), `MAXNTID(1,1,100)` (z over 64),
  `MAXNTID(1024, 2)` (product over 1024 though each axis individually is
  in range), `REQNTID(1025)`, `REQNTID(8,8,65)`. Each should assert on an
  error message that names the ceiling, not just "positive integer
  literal" (so the new failure mode is distinguishable from the old one in
  test output).
- Accepted (regression guard, already-used shape from the existing z-axis
  test): `MAXNTID(8, 8, 4)`, `REQNTID(8, 8, 4)`, `MAXNTID(1024)`,
  `MAXNTID(1024, 1)` — this exercises the "at the exact ceiling" boundary
  both per-axis and product-wise.
- `MINCTASM(999999)` must **still type-check successfully** (explicit
  regression test asserting the deliberate non-fix, so a future contributor
  doesn't "fix" it into an invented ceiling without re-reading this
  rationale).
- All of `TestLoweringIR`/`TestLoweringPTXAndPipeline`'s existing in-range
  fixtures (`MAXNTID(256)`, `MAXNTID(16,16)`, `MAXNTID(8,8,4)` +
  `REQNTID(8,8,4)`, `REQNTID(128)`) must stay green unchanged — they are
  already within both the per-axis and product ceilings, so this is a
  no-op check, not a required edit.

## 6. Unknowns / not determined

- The exact current-version NVIDIA CUDA C Programming Guide "Compute
  Capabilities" appendix page did not fetch cleanly this session (returned
  a short stub / 404 on the URLs tried); the 1024/1024/64 numbers are
  corroborated via a third-party device-query cheat sheet and via this
  repo's own pre-existing, separately-cited `_NVVM_SREG_MAX` table, not via
  a fresh direct read of NVIDIA's own appendix table this session. Grade
  the specific ceiling numbers **[DOCUMENTED, corroborated]**, not
  **[OBSERVED]**.
- Whether `ptxas` itself is available in this environment to run an actual
  end-to-end confirmation (compile bad PTX and watch `ptxas` reject or warn)
  was not checked — `which ptxas` was not run to completion in this
  session's recon; the plan does not depend on that, since the fix targets
  this compiler's own type checker, not `ptxas` behavior.
- Whether raising `MAXNTID`/`REQNTID` co-occurrence as its own follow-up
  item is wanted is a scope decision left to whoever picks this up (see
  §4.3).

## 7. How I verified (this session, no repo files changed)

Ran from a Python one-liner against the checked-out tree, using the
project's own test fixtures (`tests/test_tuning_hints.py::_IFACE`, `_impl`)
via `tests.support.typecheck_module`, `compile_to_llvm`, and
`compile_to_ptx.llvm_ir_to_ptx`:

```
_impl(' [MAXNTID(2000)]')        -> typecheck success=True
_impl(' [MAXNTID(1,1,100)]')     -> typecheck success=True
_impl(' [REQNTID(1024,1024,64)]')-> typecheck success=True
_impl(' [MINCTASM(999999)]')     -> typecheck success=True
_impl(' [MAXNTID(2000, 2000, 2000)]') -> compiles to LLVM IR with
  "nvvm.maxntid"="2000,2000,2000" and to PTX with:
  .maxntid 2000, 2000, 2000
```

`git status --short --untracked-files=all` in the repo before and during
this session showed no modifications; the repo was read-only throughout.
Scratch artifacts (fetched HTML, search JSON, status snapshots) are under
`~/dixie-scratch-area/item-11/`.

## Sources

- NVIDIA, *Parallel Thread Execution ISA*, "Performance-Tuning Directives"
  (`.maxntid`, `.reqntid`, `.minnctapersm`, `.maxnctapersm`):
  https://docs.nvidia.com/cuda/parallel-thread-execution/index.html
- Third-party CUDA device-query cheat sheet corroborating per-axis thread
  block limits (1024, 1024, 64) on a real device:
  https://kdm.icm.edu.pl/Tutorials/GPU-intro/introduction.en/
- (Consulted, not usable as a citation — returned a stub/404 rather than
  the appendix content) NVIDIA CUDA C Programming Guide, "Compute
  Capabilities" appendix:
  https://docs.nvidia.com/cuda/cuda-c-programming-guide/index.html#compute-capabilities
