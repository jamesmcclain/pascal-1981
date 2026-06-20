# Plan — Milestone C: the parallel execution model

**Goal.** Give a `DEVICE` kernel the four things that turn it from "a slow serial
function that happens to live on the GPU" into a viable parallel program: the
**thread/block index intrinsics** to compute a per-thread global index, and a
**barrier** to synchronize threads that stage through `[SPACE(SHARED)]` memory.
This closes the §4 gap in `docs/cuda-kernel-prescription.md` (Milestone C). It is
the *device-side* half of "run a real kernel"; the *host-side* launch geometry
(grid/block dimensions, allocate/copy/launch) is Milestone D (§5) and is **out of
scope here**.

**Non-goals / explicit constraints.**
- Do **not** change the host/`vintage` path or the existing `DEVICE MODULE` /
  `DEVICE UNIT` behavior. Every new builtin is **device-only** and gated.
- **The CPU-device (`device=x86`) serial test loop must keep producing correct
  answers.** This is the project's primary correctness loop (prescription §7) and
  the single hardest constraint on this milestone — see "The grid-stride contract"
  below. On `device=x86` the new intrinsics lower to constants for a one-thread,
  one-block grid (`THREADIDX_*→0`, `BLOCKIDX_*→0`, `BLOCKDIM_*→1`, `GRIDDIM_*→1`,
  `SYNCTHREADS→no-op`).
- `FORALL` sugar (§4.4) and the broad width change (§4.5) are **deferred**; only
  the narrow index-width decision that the intrinsics force is made here.
- No host orchestration, no launch surface, no CUDA/ROCm runtime. Those are D/E.

**How to read this file.**
- `[ ]` items are ordered; earlier phases gate later ones.
- **Anchor** = `file.py:symbol (~line)`. **Line numbers drift — re-grep the
  symbol before editing.**
- **Green gate** = the condition that must hold before an item is "done." The
  universal green gate, in addition to any stated one: **full suite stays green**
  (`PYTHONPATH=src python3 -m pytest tests/ -q`) **and** host/`vintage` + existing
  `DEVICE MODULE`/`DEVICE UNIT` output is unchanged **and** every CPU-device
  kernel still produces its correct serial answer.

**Companion docs.** `docs/cuda-kernel-prescription.md` §4 (the gap this closes),
§7 (the CPU-device lowerings this must honor), §1 (the vector-add/sieve end
state). `docs/ads-memory-spaces-design.md` §5.4 (the `SHARED` staging pattern
`SYNCTHREADS` protects). The retired `docs/old/device-unit-migration-checklist.md`
is the format and discipline this plan follows.

---

## The grid-stride contract (read this before writing any test)

A kernel meant to be launched over *N* threads, run **single-threaded** on the
CPU device, must still compute the whole result — otherwise `device=x86` stops
being a correctness loop the moment kernels become parallel. The idiom that makes
this work, and that every acceptance kernel in this milestone is written in, is
the **grid-stride loop**:

```
i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;     { global thread index }
stride := BLOCKDIM_X * GRIDDIM_X;               { total threads in grid }
WHILE i < n DO BEGIN
  a[i] := a[i] + b[i];
  i := i + stride
END;
```

On `device=x86` the constants collapse to `i := 0; stride := 1`, so the one
thread walks every element `0..n-1` and the serial answer is correct. On a GPU
each thread starts at its own `i` and strides by the grid width, covering the
array between them. **Same source, correct both ways.** Any acceptance kernel that
indexes with a bare `a[THREADIDX_X]` (no stride loop) is wrong for this milestone
because it would only touch element 0 on the CPU device — reject such tests in
review.

---

## Phase 0 — Owner decisions [DECIDED]

Phase 0 decisions are now ratified for Milestone C. These are design commitments;
implementation starts in C.1/C.2.

**0.1 The builtin surface — DECISION: flat names first.** Register the §4.1 reads
as **flat nullary builtin functions** with the names in the prescription table
(`THREADIDX_X/Y/Z`, `BLOCKIDX_X/Y/Z`, `BLOCKDIM_X/Y/Z`, `GRIDDIM_X/Y/Z`) and
`SYNCTHREADS` as a nullary builtin **procedure**. No record-sugar (`THREADIDX.X`)
for now; it can be layered later without breaking the flat names. This follows
the prescription default and keeps the first surface trivial to parse, type-check,
and lower.

**0.2 The device-code gate — DECISION: global registration, use-site rejection.**
`register_builtins` (`builtins_registry.py:~24`) runs **once, globally**, before
any unit's device-ness is known, so the gate cannot be "register only inside a
device module." Instead: **register the builtins unconditionally, and reject their
*use* outside `in_device_module` in the type checker.** The checker already tracks
`in_device_module` (`type_checker.py:~91`, set by `_device_context` `~206`), so the
use-site check is a few lines at the builtin-call path.

Important distinction: this gate is about **source context**, not physical target
hardware. `device=x86` is the CPU-device stand-in, so these builtins are valid in
`DEVICE MODULE` / `DEVICE UNIT` code even when that device code lowers to host CPU
instructions. They are invalid only in normal host `PROGRAM`/host `MODULE`/host
`UNIT` code. Diagnostics should say "`THREADIDX_X` is only available in DEVICE
code" rather than implying that an x86 lowering is disallowed.

**0.3 The index scalar type — DECISION: return `INTEGER32`.** The index reads
return `INTEGER32`, not vintage `INTEGER`. The GPU intrinsics are `i32`, and using
`INTEGER32` avoids baking the dialect's 16-bit `INTEGER` ceiling into parallel
indices while leaving ordinary `INTEGER` semantics untouched. This is the narrow
width decision only; broad scalar work (`REAL32`/`HALF`, `MOVESL` length widening,
and any wider launch-argument audit) remains deferred.

**Green gate for Phase 0:** complete — decisions recorded; no code yet.

---

## Phase C.1 — Thread/block index intrinsics (§4.1) [required]

- [x] **C.1.1 Register the 12 index reads as device-only nullary functions.**
  Anchor: `builtins_registry.py:register_builtins (~24)` / `define_builtin`.
  Define `THREADIDX_X/Y/Z`, `BLOCKIDX_X/Y/Z`, `BLOCKDIM_X/Y/Z`, `GRIDDIM_X/Y/Z`
  as parameterless functions returning the index scalar type (see C.1.3).
  **Green gate:** they parse and type-check inside a `DEVICE` body; the suite is
  unchanged for host code.

- [x] **C.1.2 Reject use outside DEVICE source code.** Anchor: the builtin-call
  checking path in `type_checker.py` (the function-call resolver; re-grep where
  builtin function calls are validated). If one of the index reads is called and
  `not self.in_device_module`, emit a clear error. Do **not** key this check off
  the backend triple: `device=x86` is valid device code and must accept the
  builtins. **Green gate:** a normal host `PROGRAM` calling `THREADIDX_X` is
  rejected with a device-only diagnostic; a `DEVICE` body calling it passes for
  both `device=x86` and GPU triples.

- [ ] **C.1.3 Codegen lowering — GPU intrinsic vs CPU-device constant.**
  Anchors: the function-builtin dispatch in `codegen/exprs.py`
  (`codegen_expr_func_call`; re-grep), `codegen/base.py:_is_gpu_triple`, and the
  `_declare_libm_func` pattern (`codegen/__init__.py:~60`) for declaring an
  intrinsic once and calling it. Lower each read:
  - **NVPTX** (`nvptx*`): a call to the matching `llvm.nvvm.read.ptx.sreg.*`
    intrinsic (`tid`/`ctaid`/`ntid`/`nctaid` × `.x/.y/.z`), `i32`-returning.
  - **AMDGPU** (`amdgcn*`): the analogue (`llvm.amdgcn.workitem.id.*` /
    `llvm.amdgcn.workgroup.id.*`; block/grid dims come from dispatch-ptr reads —
    a thin helper, acceptable to stub `*DIM` as a TODO if amdgcn is not a C-tier
    target, but keep NVPTX complete).
  - **CPU-device** (`x86`): a constant — `THREADIDX_*→0`, `BLOCKIDX_*→0`,
    `BLOCKDIM_*→1`, `GRIDDIM_*→1` — so a grid-stride kernel runs as a 1×1 grid.
  **Green gate:** on `nvptx64`, a kernel using the four reads emits exactly the
  expected `llvm.nvvm.read.ptx.sreg.*` calls (artifact-level assert) and no host
  symbols; on `x86`, the reads fold to the constants above and **the grid-stride
  vector-add kernel runs and produces the correct full-array result** (the
  milestone's headline correctness test).
  **Progress:** NVPTX special-register lowering and CPU-device constants are
  implemented and artifact-tested in `tests/test_device_index_intrinsics.py`;
  the grid-stride run test remains for C.5.

- [x] **C.1.4 Index-width implementation (the narrow part of §4.5).** The Phase
  0.3 decision is that the intrinsics return **`INTEGER32`** (already a scalar
  type, `_SCALAR_SIZES['INTEGER32']=4`) rather than `INTEGER`, so the global-index
  arithmetic is 32-bit and the 65 535-thread ceiling is lifted for indices while
  leaving `INTEGER` semantics untouched everywhere else. Do **not** undertake the
  broad `REAL32`/`HALF`/`MOVESL`-length widening (that stays deferred in
  `docs/followups.md`). **Green gate:** assigning a read to an `INTEGER32`
  variable type-checks without truncation; mixing with `INTEGER` follows the
  existing promotion rules.

---

## Phase C.2 — Barriers / synchronization (§4.2) [required for SHARED]

- [ ] **C.2.1 Register `SYNCTHREADS` as a device-only nullary procedure.**
  Anchors: `builtins_registry.py` (register) + the Phase-0.2 use-site gate.
  **Green gate:** `SYNCTHREADS` parses/type-checks in a `DEVICE` body; rejected in
  host code.

- [ ] **C.2.2 Codegen lowering.** Anchor: the procedure-builtin dispatch in
  `codegen/stmts.py:codegen_proc_call_stmt (~212)` (where `FILLSC`/`MOVESL` are
  already device-intercepted). Lower `SYNCTHREADS`:
  - **NVPTX** → `llvm.nvvm.barrier0` (a.k.a. `barrier.sync 0`).
  - **AMDGPU** → `llvm.amdgcn.s.barrier`.
  - **CPU-device** (`x86`) → **no-op** (emit nothing). A 1×1 grid needs no
    barrier, and emitting nothing keeps the serial run correct.
  **Green gate:** a `[SPACE(SHARED)]`-staging kernel with `SYNCTHREADS` (the
  sieve-bridge pattern) compiles; on `nvptx64` the barrier intrinsic appears; on
  `x86` no barrier is emitted and the kernel still produces the correct serial
  result. Defer `THREADFENCE` and fence variants until a kernel needs them.

---

## Phase C.3 — Launch-bounds / signature contract (§4.3) [mostly note; defer body work]

- [ ] **C.3.1 Confirm no new kernel-*body* syntax is required.** Per §4.3, grid
  and block geometry are supplied at **launch** (host side, Milestone D §5.4), not
  in the kernel. The kernel reads the C.1 intrinsics; that is the whole contract.
  This item is a recorded decision, not code. **Green gate:** the C.1 acceptance
  kernel needs nothing beyond C.1/C.2 — verified by it compiling and running.
- [ ] **C.3.2 (Optional, deferrable) `maxntid`/`reqntid` launch-bounds metadata.**
  If/when a kernel wants the compiler to know its block size, emit NVPTX
  `nvvm.annotations` launch-bounds metadata on the entry. **Defer** until Milestone
  D defines where the block size comes from; there is nothing to pin it to yet.

---

## Phase C.4 — Parallel-iteration statement `FORALL` (§4.4) [DEFERRED]

Explicitly **not** in this milestone. `FORALL i IN 0..n-1 DO ...` is sugar that
expands to the grid-stride guard above; per the prescription, build it **after**
two or three real kernels exist and the hand-written pattern is obviously
repetitive. Tracked here only so it is not forgotten. No green gate (no work).

---

## Phase C.5 — Tests & acceptance

- [ ] **C.5.1 Unit/artifact tests.** Mirror the device-unit test discipline
  (assert on the emitted IR, both triples):
  - `nvptx64`: each index read emits its specific `llvm.nvvm.read.ptx.sreg.*`
    intrinsic; `SYNCTHREADS` emits `llvm.nvvm.barrier0`; still zero host-runtime
    symbols (the Milestone-A invariant must survive).
  - `x86`: each read folds to its constant (`0`/`1`); `SYNCTHREADS` emits nothing.
  - Normal host code using any of these is rejected with a device-only diagnostic;
    `DEVICE` code targeting `device=x86` accepts them and lowers them to the CPU-device constants.
- [ ] **C.5.2 CPU-device correctness (the headline).** A **grid-stride vector-add**
  `DEVICE UNIT` (and a `SHARED`-staging kernel using `SYNCTHREADS`) compiled to
  `device=x86`, linked, and **run**, producing the correct full-array result.
  This is the proof that the parallel model is correct independent of any GPU.
- [ ] **C.5.3 Definition-of-done (prescription §10, point 2 dependency).** With
  C.1/C.2 in place, the "vector-add kernel computes the right numeric result on the
  CPU device (no GPU needed)" smoke test (§10) becomes achievable; wire it as a
  standing integration test. **Green gate:** prescription §10 point (2) can be
  ticked from `[PRESCRIBED]` to `[DONE]`.

---

## Suggested order of execution

1. Phase 0 decisions (0.1 surface, 0.2 gate, 0.3 width) — done; proceed to
   implementation.
2. C.1.1–C.1.3 (index reads), proving the **grid-stride vector-add runs correctly
   on `x86`** before touching GPU specifics — that single run is the milestone's
   spine.
3. C.2 (`SYNCTHREADS`), proving a `SHARED`-staging kernel runs serially on `x86`.
4. C.5 tests throughout (artifact assertions land with each lowering; the run
   tests land once C.1/C.2 are in).
5. C.3.1 is a one-paragraph confirmation; C.3.2 and C.4 stay deferred with
   pointers.

**Definition of done for Milestone C:** a grid-stride vector-add and a
`SHARED`+`SYNCTHREADS` kernel both (a) emit the correct NVPTX intrinsics with zero
host-runtime symbols on `nvptx64`, and (b) run on the `x86` CPU device and produce
the correct numeric results; host/`vintage`/`DEVICE MODULE`/`DEVICE UNIT` output
unchanged; full suite green. At that point prescription §4 is `[DONE]` and the
remaining gaps are Milestone D (host orchestration) and E (AMDGPU).
