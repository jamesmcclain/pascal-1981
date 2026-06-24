# From Here to a Running CUDA Kernel - A Prescription

**Companion to** `ads-memory-spaces-design.md` and `ads-implementation-plan.md`. Those
documents took the `ADS`/address-space *type-system slice* from idea to validated
`addrspace(k)` IR. This document picks up where they stop and lays out the remaining work to
get an actual GPU kernel to **launch, run, and return a result** on real hardware.

**Audience:** the next agent/instance (or a human) continuing this build. Read it cold.

**Status tags** (same convention as the design record):
- **[VERIFIED]** - checked against the tree or empirically reproduced while writing this doc
  (2026-06-19), with the command/result noted.
- **[GAP]** - something believed done that is *not* done, or only partly done. These are the
  load-bearing corrections.
- **[PRESCRIBED]** - proposed work, not yet built.
- **[DEFAULT]** - a reasonable assistant-chosen default for an unratified question; flag before
  building on it heavily.
- **[DONE]** - implemented and green-gated since the original writing of this doc.

**Progress summary (2026-06-19):** Milestones A (self-contained IR) and B (entry points) are
**complete**. §1.5 (DEVICE UNIT foundation, USES fix, init-block rescission, integration-test
tier) is **complete**. Milestone C (parallel execution model) is **complete** — the index
intrinsics and `SYNCTHREADS` barrier are implemented, gated, and validated on the `x86` CPU
device; the build record is archived at `docs/old/milestone-c-parallel-execution-plan.md`.
Milestone D (host orchestration) has its **CPU-device first slice landed** (§5.5
acceptance): the host builtins `DEVALLOC`/`DEVCOPYTO`/`DEVCOPYFROM`/`DEVFREE`/
`LAUNCH` lower to a malloc/memcpy shim, and a host program that
allocates, copies in, launches a grid-stride vector-add, copies back, and prints
the result runs end-to-end on `x86`. **`LAUNCH` now lowers through a
GPU-faithful launch ABI** — it marshals the kernel actuals into a `void**`
argument array and calls a `pas_dev_launch(name, thunk, gx,gy,gz, bx,by,bz, argv)`
shim seam (geometry accepted as 2 *or* 6 values), instead of the earlier direct
call to the kernel. On the CPU device the shim runs a compiler-emitted per-kernel
dispatch thunk; the same call site is reused unchanged for the GPU, where the
CUDA shim dispatches by name out of the loaded module and ignores the thunk. So
the real-GPU shim (CUDA driver API, §5.2) is now a pure runtime-library swap plus
the PTX-load/module-handle plumbing; the `GRID`/`BLOCK` *naming* sugar over the
already-supported 6-value geometry, and Milestone E (AMDGPU stack), remain
prescribed. Suite: **840 passed, 69 subtests**.

**Update (2026-06, first GPU run via the external-launcher path):** A Pascal
`DEVICE UNIT` kernel has now run on a real NVIDIA GPU. The `REAL32`/`REAL64` scalar
types and a void-return fix for exported device entries landed (suite grew to 781
passed with `clang` available), and the `examples/device_ptx/mandelbrot` kernels
were emitted to PTX and dropped — unchanged — into the companion mandelbrot-gpu
PyCUDA launcher, producing a correct image. See
`docs/old/mandelbrot-ptx-substitution-plan.md` ("Hardware validation result"). Note
this validates the **external-launcher** route (a Pascal-generated `.ptx` loaded
by an existing host), **not** the Pascal-side host orchestration of §5 / Milestone
D, which is still prescribed: §10 point (3) below — running a kernel through the
*Pascal* `LAUNCH(...)` shim — therefore remains open. What is now proven is that
the device-code half (entry points, the parallel-execution intrinsics of
Milestone C, void-return ABI, and the scalar widths a real kernel needs) is
hardware-correct end to end.

---

## 0. Where you actually are (verified baseline)

Reproduced on this VM with `llvmlite 0.47.0`, the bundled LLVM (≈21), and `clang`:

- **[VERIFIED] The CPU-device path runs end-to-end.** `DEVICE MODULE kernel.pas` +
  `main.pas`, compiled with the default `device=x86`, links against `libpascalrt.a` and prints
  the 25 primes under 100. Spaces collapse to addrspace 0; `MOVESL` lowers to an inline
  load/store byte loop. This is real and it works.
- **[VERIFIED] NVPTX IR is space-correct.** `--device-triple nvptx64-nvidia-cuda` emits
  `[SPACE(SHARED)]`→`addrspace(3)`, `[SPACE(GLOBAL)]`→`addrspace(1)`, and the `MOVESL` bridge
  becomes `load addrspace(3)` → `store addrspace(1)`. Feeding that IR through the bundled LLVM
  target machine (`sm_70`) produces **valid PTX**: `work_flags` in `.shared`, `prime_flags`
  in `.global`, a `ld.shared.u8` → `st.global.u8` copy. The space→instruction thesis holds.
- **[VERIFIED] 607 tests pass** at original writing; suite is now **798 passed, 69 subtests**.

So the type system, the space lattice, and the addrspace lowering are genuinely finished and
validated. What follows is everything *else* that a CUDA kernel needs, none of which the
addrspace slice touched.

The four findings that gate a real kernel, each expanded below:

| # | Gap | Status |
|---|-----|--------|
| §2 | Device IR is **not self-contained** | **[DONE]** — Milestones A1–A3 complete (Phase 2.1 + 2.2 lazy plan) |
| §3 | There are **no entry points**, only device functions | **[DONE]** — Milestone B complete (Phase 2.3) |
| §4 | **No parallel execution model** | **[DONE]** — Milestone C complete: index reads, `SYNCTHREADS`, no new body launch syntax, CPU-device correctness tests |
| §5 | **No host orchestration** | [IN-PROGRESS] — CPU-device slice DONE (DEVALLOC/DEVCOPYTO/DEVCOPYFROM/DEVFREE on a malloc/memcpy shim; LAUNCH lowers through the real `pas_dev_launch(name, thunk, gx,gy,gz, bx,by,bz, argv)` ABI with 2-or-6 geometry; §5.5 vector-add runs end-to-end on x86). CUDA driver shim (now a runtime-only swap + PTX module load) + `GRID`/`BLOCK` naming sugar still open |
| §6 | **AMDGPU back end crashes** (bonus, ROCm-only) | [PRESCRIBED] — open |

Milestones below are ordered so each one is independently testable and the host/vintage path
stays byte-identical throughout.

---

## 1. The end-state we are building toward

A minimal but *real* CUDA bring-up: a device compiland exporting one kernel that does
something embarrassingly parallel (vector add is the canonical smoke test - keep the sieve as
a second step), a host `PROGRAM` that allocates two input buffers and one output buffer on the
device, copies inputs up, launches the kernel over an N-thread grid, copies the result back,
and prints it.

Written in the `DEVICE UNIT` shape recommended in §1.5 - where the launchable entry points are
exactly the routines the unit *exports* (§3) - the device side is an interface plus an
implementation:

```pascal
{ device interface: the exported name `add` is the launchable entry (§3) }
INTERFACE;
UNIT vadd (add);
PROCEDURE add(a, b, c: ADS(GLOBAL) OF REAL; n: INTEGER);
END;
```

```pascal
{ device implementation }
IMPLEMENTATION OF vadd;
PROCEDURE add(a, b, c: ADS(GLOBAL) OF REAL; n: INTEGER);
VAR i: INTEGER;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;   { the parallel index, §4 }
  IF i < n THEN
    c^[i] := a^[i] + b^[i]
END;
.   { no initializer block - forbidden in a DEVICE UNIT, §1.5.3 }
```

and the host program `USES` the device unit to launch it by name (the `USES` path is verified
working, §1.5.1):

```pascal
PROGRAM main(output);
USES vadd (add);                       { import the kernel entry by name }
{ host-side device API, see §5 }
...
BEGIN
  da := DEVALLOC(n * SIZEOF(REAL));    { etc. }
  DEVCOPYTO(da, @ha, n * SIZEOF(REAL));
  ...
  LAUNCH(add, GRID(blocks), BLOCK(threads), da, db, dc, n);
  DEVCOPYFROM(@hc, dc, n * SIZEOF(REAL));
  ...
END.
```

Everything between here and that program is the prescription.

---

## 1.5 Foundational decision - `DEVICE UNIT` vs `DEVICE MODULE` [RECOMMENDED: `DEVICE UNIT`; owner-ratifiable]

This choice is foundational: it determines how the host names a kernel to launch it (§5.4) and
how launchable entry points are distinguished from device-internal helpers (§3). It was
reopened by a correction to an earlier claim.

### 1.5.1 Correction: `USES` is not broken [DONE - reproduced, fixed, and integration-tested]

An earlier pass concluded "`uses` codegen is broken" and routed around it with `EXTERN`-by-name.
That was wrong, and rested on a wrong mental model - that `USES` is how you reach a `MODULE`'s
exports. In the vintage dialect, `USES` pairs with a **`UNIT`** (an `INTERFACE` +
`IMPLEMENTATION OF` pair), not with a plain `MODULE`. Reproduced against the tree:

- The grammar, type checker, and codegen all implement `INTERFACE`/`UNIT`/`USES`/
  `IMPLEMENTATION OF`; the checker even resolves the manual's positional **renaming** import
  (`USES GRAPHICS (MOVE, PLOT)` aliasing the exported `BJUMP, WJUMP`).
- The only real defects were two small codegen bugs, both now fixed (patch `uses-fix.patch`):
  **(1)** `codegen_use_clause` called `parse_file` without importing it - *that* was the
  "undefined `parse_file`" the after-action report saw; a one-line fix. **(2)** The positional
  rename was not threaded into codegen, so a renamed import's call site found no symbol; fixed by
  declaring the external under its real exported name and binding the alias to it.
- With those applied, the IBM manual's PLOTBOX/GRAPHICS example (stubbed) **compiles, links
  against a separately-compiled `IMPLEMENTATION OF GRAPHICS`, and runs** - both the plain and the
  renamed `USES` forms. 607 tests stay green. (See the multi-file example shipped beside this
  doc; it is the seed of a future integration test - §1.5.4.)

So `USES` works, and the design's intended "host `uses` the device code to get launchable
kernels" is a *live* path - but only if the device code is a `UNIT`.

### 1.5.2 The decision [DONE - DEVICE UNIT fully implemented]

Because `USES` is a `UNIT` mechanism, a device compiland that the host launches by name should be
a **`DEVICE UNIT`** (an `INTERFACE` + `IMPLEMENTATION OF`), not a `DEVICE MODULE`. The
recommendation is to adopt `DEVICE UNIT`. Dividends:

- **Entry points fall out of exports, with no new syntax (§3).** A unit's interface lists what it
  exports; those exported routines are exactly the launchable kernels, and everything in the
  implementation the interface does *not* export is a device-internal helper `.func`. This
  answers - for free - the "how do we mark entry points" question that a `DEVICE MODULE` would
  otherwise need an annotation for. (This supersedes the earlier `[KERNEL]`-on-every-routine
  idea, which was redundant: being inside a device compiland already makes code device code; the
  thing that needs marking is *entry-ness*, and the export list supplies it.)
- **Host launch is the verified `USES` path (§5.4),** not an `EXTERN`-by-name workaround.
- **Device helper libraries compose,** matching the manual's two-tier shape (a `UNIT` that `USES`
  another `UNIT` - GRAPHICS uses BASEPLOT). A `DEVICE UNIT` may `uses` another `DEVICE UNIT` for
  shared device code; design §1.2's cross-kind rules still apply (a device unit may not `uses` a
  host unit).

Costs / open points:
- **More ceremony.** A `UNIT` is two files (interface + implementation) versus a single-file
  `DEVICE MODULE`. Mild for a one-kernel smoke test; real for many small kernels. A future
  single-file sugar could collapse the common case, but that is not v1.
- **Naming.** `DEVICE UNIT` reads well and reuses the existing `UNIT` machinery. `DEVICE MODULE`
  could be kept as an alias for the single-file, non-exporting case, but two surfaces for one
  concept is itself a cost; pick one as primary. **[RECOMMENDED]** `DEVICE UNIT` primary.

The overall `UNIT`-vs-`MODULE` selection remains the owner's to ratify; everything below is
written to work either way, and "the device compiland" is used where the distinction does not
matter. Where it *does* matter - entry-point marking (§3) and host launch (§5.4) - both the
export-driven (`UNIT`) and the annotation (`MODULE`) routes are given.

### 1.5.3 Rescission: no initializer code in a `DEVICE UNIT` [DONE - implemented and enforced]

A vintage `UNIT` may carry an **initializer block** - the optional `BEGIN ... END` in an interface,
and the `BEGIN ... END.` body of an `IMPLEMENTATION OF`. On a device there is no host-style
"module load runs this once" moment, and an initializer would smuggle in exactly the
host-runtime, ordering-dependent code the device dialect is trying to keep out. So **a
`DEVICE UNIT` may not have an initializer block**, in either the interface or the implementation.
This is a new module-scoped **rescission**, in the same family as recursion / `NEW`-heap /
host-I/O (design §9; prescription §2.3.A4): enforce it as a checker ban when the unit is a device
unit and an init block is present - *"initializer code is not available in a DEVICE UNIT."* A
device implementation therefore ends after its declarations (no trailing `BEGIN ... END.`); a
device interface ends at `END;` with no `BEGIN`.

### 1.5.4 The multi-file `USES` example - integration-test tier [DONE]

The faux-graphics example has been ported to a standing **integration-test tier**
(`tests/integration/`). Three fixtures now cover the multi-file workflow (compile N files →
link → run → diff stdout):
- `test_device_primes.py` - `DEVICE INTERFACE` + `DEVICE IMPLEMENTATION OF` + host `USES`
  program, compiled separately, linked, run on the CPU-device path; diffs 25 primes.
- `test_host_uses.py` - plain host `INTERFACE`/`IMPLEMENTATION`/`USES` control case.
- `test_uses_graphics.py` - plain and renamed `USES GRAPHICS` end-to-end, including IR-level
  proof that positional renames bind to the real exported symbols.

All three run with `PYTHONPATH=src python3 -m pytest tests/integration/ -q` and use the
`@requires_exe` skip discipline. The `-Wl,--allow-multiple-definition` workaround previously
needed by the multi-file tests has been removed (fixed by the INPUT/OUTPUT single-definition
change, S4.1).

---

## 2. Milestone A - make device IR self-contained [DONE]

### 2.1 What was actually built [DONE]

`type_checker.py` has a complete recission set (extended in Phase 1-2 work):
- `_DEVICE_BANNED_HEAP = {NEW, DISPOSE}` and `_DEVICE_BANNED_IO = {WRITE, WRITELN, READ,
  READLN, PAGE, RESET, REWRITE, GET, PUT, CLOSE, DISCARD, ASSIGN, READFN, READSET}` are
  rejected at the call site inside a `DEVICE MODULE` or `DEVICE UNIT`
  (`_check_device_recission`, via the `_device_context()` context manager shared by all
  device compiland types).
- Direct and mutual recursion among device routines is detected at module/unit end
  (`_detect_device_recursion`).
- Initializer blocks are banned in `DEVICE UNIT` (§1.5.3).

So if your kernel source writes `WRITELN(...)`, `NEW(p)`, recurses, or adds an init block,
you get a clean error. The recission set applies uniformly to `DEVICE MODULE` and
`DEVICE UNIT` via the shared `_device_context()` context manager.

### 2.2 Why host symbols were leaking [DONE - both leaks fixed]

There were two distinct leaks; both are now fixed:

1. **Compiler-inserted runtime checks** (`abort`/`fflush` from `emit_runtime_abort`): fixed by
   `_device_checks_suppressed()` in `codegen/base.py`, which makes `check_enabled` and
   `effective_flag` return `False` for all runtime-check flags (`MATHCK`/`RANGECK`/`INDEXCK`/
   `NILCK`/`STACKCK`) when `is_device_module`. Device IR now contains zero `abort`/`fflush`
   references.

2. **Unconditional predeclared-extern dump**: `_register_predeclared_externs` (the eager
   ~40-`ir.Function` dump at construction) was replaced by a **lazy registration scheme**
   (`_build_extern_factories` + `runtime_extern(name)` accessor in `codegen/base.py`). Dead
   externs - never referenced - never appear in any IR, host or device. The `INPUT`/`OUTPUT`
   multiple-definition collision was also fixed (S4.1): root compilands (PROGRAM/MODULE) own
   the strong global definitions; units declare them externally.

### 2.3 What was prescribed / what was built [DONE]

- **A1. Suppress host-calling runtime checks** → done via `_device_checks_suppressed()`. See §2.2.
- **A2. Lazy extern registration** → done via `_build_extern_factories` / `runtime_extern()`.
  See §2.2.
- **A3. Emitted-IR guard tests** → done: `tests/test_device_no_host_externs.py` (DEVICE UNIT
  vector-add + DEVICE MODULE on `nvptx64`/`amdgcn`, full denylist asserted absent, zero
  `declare`s) and `tests/test_lazy_externs.py` (zero-declare guarantee for host programs/units
  with no runtime references; INPUT/OUTPUT single-definition property).
- **A4. Recission set** → the construct-shaped recissions (host I/O, heap, recursion,
  initializer blocks) are all frozen and enforced. The flag-shaped candidates from the design
  (set I/O, dynamic set ranges, flat-heap pointer-chasing, nonlocal GOTO) remain unfrozen -
  not blockers for vector-add, and the `_device_context()` machinery is in place to add them
  when wanted.

**Green gate met:** device IR on nvptx64 has zero host-runtime symbol references; host/vintage
IR is byte-identical; 798 passed, 69 subtests.

---

## 3. Milestone B - emit *entry points*, not just device functions [DONE]

**This is the §3 gap and the single thing that makes an artifact launchable.** A PTX `.func`
cannot be the target of `cuLaunchKernel`; only a `.entry` can. Today every device routine is a
`.func`.

The framing matters. Being inside a device compiland already makes a routine device code - that
is *not* what needs marking (so there is **no `[KERNEL]`-on-everything** marker; that earlier idea
was redundant). What needs marking is which device routines are **launchable entry points**
versus device-internal helpers. Both are device code; only an entry point gets the kernel calling
convention and is findable by `cuModuleGetFunction`. A helper (say a `device_min(a,b)` the kernel
calls) must stay a plain `.func`, or it pays launch-ABI overhead on an internal call and clutters
the launchable-symbol namespace.

### 3.1 The mechanism [VERIFIED - tested while writing this]

Two mechanisms exist; either yields a real `.entry`. Confirmed empirically on `nvptx64`/`sm_70`:

- **Calling convention.** Setting `func.calling_convention = "ptx_kernel"` on the llvmlite
  `ir.Function` produced `.visible .entry addone(...)` in emitted PTX. One line.
- **`nvvm.annotations` metadata.** Adding `!{ptr @k, !"kernel", i32 1}` to a named-metadata
  node `nvvm.annotations` is the classic CUDA marker and composes with the above.

For **AMDGPU** the equivalent is calling convention `amdgpu_kernel`.

**[DEFAULT]** Use the calling-convention route (`ptx_kernel` / `amdgpu_kernel`) as primary -
it is one assignment, target-uniform in shape, and verified. Add `nvvm.annotations` too if a
given CUDA loader path wants it.

### 3.2 Which routines become entry points [DONE]

Implemented as the export-driven model: the type checker marks `decl.is_exported_entry = True`
on any `ProcDecl` whose name appears in the interface's export list (resolved by
`check_implementation_unit` loading the interface from disk - separate-compilation-safe).
`DEVICE MODULE` compilands have no interface, so no routine is marked as an entry - they
continue to emit plain device functions unchanged.

The alternative `[ENTRY]` annotation for `DEVICE MODULE` remains available via the existing
`attribute_section` slot on proc/func headers, but has not been implemented; the export-driven
route is sufficient.

### 3.3 Codegen [DONE]

`codegen_proc_decl` (`decls.py`) reads `decl.is_exported_entry`: when the flag is set and the
unit lowers to a GPU triple, it sets `func.calling_convention = "ptx_kernel"` (NVPTX) or
`"amdgpu_kernel"` (AMDGPU), chosen off `self.device_triple`. On x86 the check is skipped
(serial CPU-device parity path unaffected). Entry-shape rules enforced at codegen time:
- must be a `PROCEDURE` (not a `FUNCTION`) on GPU triples;
- parameters must be device-passable (scalars or `ADS(GLOBAL/CONSTANT) OF T`).

Acceptance test: `tests/test_device_entry_points.py` - compiles a unit exporting `vecadd` and
a non-exported `helper` to `nvptx64`; asserts `ptx_kernel` on the export and absent from the
helper; emits PTX and asserts `.visible .entry vecadd` + `.func helper`; tests the x86-device
inert path; tests the AMDGPU `amdgpu_kernel` convention; tests the entry-shape rules.

**Green gate met:** non-exported device routines stay `.func`; `DEVICE MODULE` unaffected;
host IR byte-identical.

---

## 4. Milestone C - the parallel execution model (what makes a kernel *viable*) — DONE

> **Build sequence (archived, completed):** `docs/old/milestone-c-parallel-execution-plan.md`
> turned this section into ordered, green-gated items (index intrinsics, `SYNCTHREADS`, the
> grid-stride CPU-device correctness contract, the `INTEGER32` index-width decision). All are
> implemented and validated; only the optional `maxntid`/`reqntid` launch-bounds metadata
> (C.3.2) remains deferred.

**This is the §4 gap, and the direct answer to "what language extensions do I need for viable
parallel kernels?"** A kernel with no thread indices is just a slow serial function that
happens to live on the GPU. Viable kernels need four things. None exist today; all are listed
as out-of-scope in the design's §9.

### 4.1 Thread/block index intrinsics [DONE]

The minimum viable set, as predeclared builtins registered only inside a `DEVICE MODULE`
(`register_builtins` already feature-gates; add a device-kind gate). Map each to the NVPTX
intrinsic (AMDGPU has direct analogues):

| Pascal builtin | meaning | NVPTX intrinsic |
|----------------|---------|-----------------|
| `THREADIDX_X/Y/Z` | thread index within block | `llvm.nvvm.read.ptx.sreg.tid.x/y/z` |
| `BLOCKIDX_X/Y/Z`  | block index within grid | `llvm.nvvm.read.ptx.sreg.ctaid.x/y/z` |
| `BLOCKDIM_X/Y/Z`  | block dimensions | `llvm.nvvm.read.ptx.sreg.ntid.x/y/z` |
| `GRIDDIM_X/Y/Z`   | grid dimensions | `llvm.nvvm.read.ptx.sreg.nctaid.x/y/z` |

These are `i32`-returning nullary reads. They are the device-side primitives the global thread
index `i = threadIdx.x + blockIdx.x*blockDim.x` is built from. **[DEFAULT]** Expose them as
the flat names above (and/or a small `THREADIDX.X` record-ish sugar later); start with the
12-16 scalar reads, they are trivial and unlock every 1-D/2-D kernel.

### 4.2 Barriers / synchronization [DONE]

`SYNCTHREADS` → `llvm.nvvm.barrier0` (NVPTX) / `llvm.amdgcn.s.barrier` (AMDGPU). Without it,
any kernel that stages through `[SPACE(SHARED)]` memory (exactly the pattern your sieve bridge
demonstrates) is racy. Register as a device-only builtin procedure. Add memory-fence variants
(`THREADFENCE`) later if needed.

### 4.3 The launch-bounds / signature contract [DONE]

A kernel's grid/block geometry is supplied **at launch** (host side, §5), not in the kernel
body. The kernel just reads the intrinsics. So no new *kernel-body* syntax is needed beyond
§4.1/§4.2. What you do need is the host-side `GRID(...)`/`BLOCK(...)` surface - that lives in
§5.4.

### 4.4 A parallel-iteration statement [PRESCRIBED - optional, ergonomic]

`FORALL`-style sugar (`FORALL i IN 0..n-1 DO ...`) that expands to the
`i := global_thread_index; IF i < n THEN ...` guard is pure quality-of-life. **Defer it.**
Vector-add and the sieve are fine writing the index expression by hand. Build it once you have
two or three real kernels and the pattern is obvious.

### 4.5 Width reconsideration [DONE for index intrinsics; broader scalar widening deferred]

The dialect's `INTEGER` is 16-bit and `REAL` is f64. On GPUs: indices want 32/64-bit (a 16-bit
thread index caps you at 65 535 threads - fine for vector-add-100, wrong in general), and f64
is throttled hard relative to f32. **[DEFAULT]** For first bring-up, leave widths alone and
keep N small; before you benchmark anything, add `REAL32`/`HALF` and widen the index type used
by the thread-index intrinsics to `i32`. Note `MOVESL`'s length is `i16` today (the `WRD(limit)`
in the sieve) - a 64 KiB copy ceiling worth widening when you generalize the bridge.

---

## 5. Milestone D - host orchestration (allocate / copy / launch / copy back)

**This is the §5 gap and the answer to "what do I need to do around host orchestration?"**
Even with a perfect kernel and a GPU present, nothing today can put data on the device, start
the kernel, or read results. This is the largest *new-surface* piece of work.

### 5.1 What "orchestration" concretely is

Four host-side operations, mediated by the vendor runtime/driver:
1. **Allocate** device memory → returns a `GLOBAL` handle (an opaque device pointer the host
   holds but, by the dereferenceability invariant, may not dereference - the design already
   anticipated this: `GLOBAL` is an "opaque handle" in the host column of §3.2).
2. **Copy host→device** (H2D) and **device→host** (D2H).
3. **Launch** a kernel with a grid/block geometry and an argument list.
4. **Synchronize** and **free**.

### 5.2 Two implementation strategies

- **[DEFAULT] Strategy 1 - host calls a thin C shim that calls the CUDA Driver API.** Write a
  small `runtime/cuda_launch.c` exposing `pas_dev_alloc(size)→ptr`, `pas_dev_copy_to`,
  `pas_dev_copy_from`, `pas_dev_launch(module, name, gx,gy,gz, bx,by,bz, args...)`,
  `pas_dev_sync`, `pas_dev_free`, each wrapping `cuMemAlloc`/`cuMemcpyHtoD`/`cuMemcpyDtoH`/
  `cuModuleLoadData`/`cuModuleGetFunction`/`cuLaunchKernel`/`cuCtxSynchronize`/`cuMemFree`.
  Predeclare these as host externs (the same `_register_predeclared_externs` machinery, host
  side). The Pascal program calls Pascal builtins (`DEVALLOC`, `DEVCOPYTO`, `LAUNCH`, ...) that
  lower to these extern calls. This is the least-LLVM-magic path and decouples you from
  fatbinary tooling: the host shim `cuModuleLoadData`s the **PTX string** you already emit.
- **Strategy 2 - fatbinary + CUDA Runtime API.** Bundle host object + device PTX/cubin into a
  fatbinary via `nvcc -fatbin`/`fatbinary`, and use the higher-level `cudaMalloc`/`cudaMemcpy`/
  `<<<>>>`-equivalent `cudaLaunchKernel`. More "native CUDA," but it drags in the fatbin
  toolchain and the runtime's hidden registration. **Defer.** Strategy 1 with the Driver API
  and a raw PTX module is dramatically simpler to stand up first and is fully sufficient to
  *run a kernel*.

### 5.3 Build-model consequence (the two-artifact problem) [GAP - partly real today]

The design's "multi-target build → two artifacts (host object + device PTX), bundled
fatbinary-style" is **not implemented**. Today (per the Step-4b build log) it is *one module
per `Codegen` instance*; you compile `kernel.pas` and `main.pas` as separate invocations and
link the `.ll`s with `clang`. That separate-compilation model is actually *fine* for Strategy
1: compile the `DEVICE MODULE` to **PTX text** (`emit_assembly` on the device target machine),
embed that PTX as a host string constant (or load it from a file at runtime), and have the
host shim `cuModuleLoadData` it. You do **not** need a fatbinary to launch. So:

- **[PRESCRIBED]** Add a driver mode that, for a `DEVICE MODULE` + GPU device triple, runs the
  emitted IR through the device `TargetMachine` and writes a `.ptx` (you proved this emits
  correctly). The host program references that PTX by path or embedded blob.
- The `--allow-multiple-definition` link hack that appeared in the original integration tests
  (caused by both compilands emitting `input`/`output` globals) has been **removed**: the
  INPUT/OUTPUT single-definition fix (S4.1) ensures only the root compiland owns the strong
  globals; units declare them externally. Once the device artifact is *PTX loaded at runtime*
  rather than `clang`-linked into the host binary, even this source of the collision disappears.

### 5.4 Host-side launch surface (host `USES` the device unit) [PRESCRIBED]

- A host `PROGRAM`/`MODULE` names a kernel to launch it via the **verified `USES` path** (§1.5):
  `USES vadd (add);` imports the entry `add` by name, and `LAUNCH(add, ...)` launches it. This is
  the intended design path (host `uses` device code to get launchable kernels) and it **now
  works** - the earlier "`uses` is broken, use `EXTERN`-by-name" guidance was based on a
  since-fixed one-line bug and is **rescinded** (`uses-fix.patch`).
- **What "launch" lowers to.** `LAUNCH(add, ...)` does not call `@add` directly - the host cannot
  call a GPU function. It lowers to the host shim's `pas_dev_launch(module, "add", ...)` (§5.2),
  which `cuModuleGetFunction`s the entry *by name* out of the loaded PTX and `cuLaunchKernel`s
  it. So the `USES`-imported `add` gives you the name and signature for type-checking the call;
  the shim does the actual dispatch by that name. (This is also why §1.5's "exported = entry"
  works cleanly: the export list is precisely the set of names the host can hand the shim.)
- `GRID(x[,y[,z]])`/`BLOCK(x[,y[,z]])` are argument-packing sugar over the six geometry args to
  `cuLaunchKernel`. Start with plain integers, add the sugar later.
- Kernel arguments cross the boundary as: scalars by value, device buffers as the opaque
  `GLOBAL` handle returned by `DEVALLOC`. `cuLaunchKernel` takes a `void**` of arg pointers;
  the shim assembles it.

### 5.5 Minimal orchestration acceptance

A host program that allocates, H2Ds two arrays, launches a 1-block/N-thread vector-add,
D2Hs, and prints the summed array - running on a real GPU (or the CPU-device stand-in, §7).

**[DONE on the CPU device.]** `tests/integration/test_device_orchestration.py` builds a
`DEVICE UNIT` vector-add kernel and a host `PROGRAM` that does exactly this — `DEVALLOC` ×3,
`DEVCOPYTO` ×2, `LAUNCH(add, 1, n, da, db, dc, n)`, `DEVCOPYFROM`, prints `0 3 6 … 21` — and
runs it via `clang` on x86 with no GPU. The orchestration builtins lower to the
`runtime/cpu_device_shim.c` externs (`pas_dev_alloc`=malloc, copies=memcpy,
`pas_dev_free`=free).

`LAUNCH` lowers through a **real launch ABI**, not a direct call: the compiler marshals the
kernel actuals into a `void**` array (each slot points at a cell holding one argument value,
coerced to the kernel's parameter ABI — exactly what `cuLaunchKernel` consumes) and calls
`pas_dev_launch(name, thunk, gx,gy,gz, bx,by,bz, argv)`. Geometry is supplied as 2 values
(grid, block → a 1-D launch) or 6 (gx,gy,gz, bx,by,bz); the count is implied by the kernel's
arity. On the CPU device `pas_dev_launch` invokes a compiler-emitted per-kernel dispatch
thunk `__pas_klaunch_<name>(void** argv)` that unpacks `argv` and calls the kernel as a
single-thread grid, so its grid-stride loop covers the whole buffer. The kernel-name string
and the geometry ride along unused on the CPU device — they are precisely what the CUDA shim
will consume. So running the *same* Pascal program on a GPU is now a pure runtime-library
swap: replace the four `cpu_device_shim.c` functions with CUDA Driver API wrappers and let
`pas_dev_launch` `cuModuleGetFunction` the entry by `name` and `cuLaunchKernel` it with the
geometry and `argv` already supplied — *no* further codegen change to argument handling. The
one remaining compiler-side piece is the PTX-load/module-handle plumbing (emit the device
PTX, embed or load it, thread the `cuModuleLoadData` handle to the launch), which §5.3 tracks.
The new ABI is pinned by `tests/test_device_launch_abi.py` (IR-level: the host reaches the
kernel only through the thunk, via `pas_dev_launch` with a marshalled `argv`; runtime: the
6-value geometry form prints `0 3 6 … 21`; type checker: the 2-or-6 geometry rule).

One ABI subtlety surfaced and was fixed: a host `USES` of a device unit must declare the
imported kernel in *device* lowering context, or its `ADS(GLOBAL) OF T` parameters lower to
the host segmented `{ptr, i16}` pair while the kernel definition takes a flat/addrspace
pointer — a silent mismatch that hands the kernel a garbage buffer. The imported-kernel
declaration now matches the definition's parameter ABI.

---

## 6. Milestone E - AMDGPU/ROCm (only if you want both vendors)

**This is the §6 gap, ROCm-specific.** It is *not* on the critical path to a CUDA kernel -
skip it if NVIDIA is the target - but record it so it is not mistaken for "validated."

[VERIFIED] The design claims the AMDGPU table was validated, but that validation was an
isolated single-`addrspace`-load spike. Running the **full** `build_primes` through the
`amdgcn-amd-amdhsa` back end (`gfx900`) **aborts**:

```
LLVM ERROR: Cannot select: t2: i64 = FrameIndex<0>  In function: build_primes
```

Cause: AMDGPU requires stack `alloca`s in **addrspace(5)** (private) and a correct
`target datalayout`; today locals are `alloca`'d in addrspace 0 (`decls.py:342`) and
`target datalayout = ""` (verified in the emitted IR). NVPTX tolerates this; AMDGPU does not.

**[PRESCRIBED]**
- Set a real per-triple `target datalayout` (B-adjacent; NVPTX and AMDGPU each have a canonical
  string the back end expects - pull it from the target, don't hand-roll).
- Emit function-scope `alloca`s in the target's **alloca address space** (addrspace 5 on
  amdgcn), then `addrspacecast` to generic where a generic pointer is needed. This is the one
  place a cast is legal (private↔generic on-device) and does not violate the design's
  "no concrete↔concrete cast" rule.
- Then re-run the §0 PTX/GCN acceptance for AMDGPU.

This same datalayout/alloca hygiene is *also* latently correct-making for NVPTX even though it
currently gets away without it.

---

## 7. The CPU-device stand-in stays your fast test loop

[VERIFIED] `device=x86` collapses all spaces to addrspace 0 and runs on the CPU via `clang`.
**Keep using it as the primary correctness loop** for every milestone above:
- §2 (self-contained IR): the CPU path already links clean; use the GPU-triple guard test
  (A3) for the no-host-symbols invariant.
- §3 (entry points): on `device=x86` the kernel calling convention is inert/ignored - kernel
  *logic* still runs serially, so you can test kernel *correctness* on CPU before you have a GPU.
- §4 (intrinsics): provide CPU-device lowerings - `THREADIDX_X`→0, `BLOCKDIM_X`→1,
  `SYNCTHREADS`→no-op - so a kernel run on the CPU executes as a single-thread grid and
  produces the right scalar answer. This lets you validate kernel math with zero GPU.
- §5 (orchestration): a CPU-device shim where `DEVALLOC`=`malloc`, copies=`memcpy`, and
  `LAUNCH` marshals a `void**` and calls `pas_dev_launch`, which runs a per-kernel dispatch
  thunk (single-thread grid). Same Pascal program, no GPU. Then swap the shim for the CUDA one
  — the launch call site is already GPU-shaped, so only the runtime library changes.

This is the CPU-device dividend the design designed for; lean on it.

---

## 8. Vendor runtime considerations (the Docker question)

**Short answer: yes, a container with the vendor runtime + the GPU exposed is the right move,
with caveats.**

### 8.1 NVIDIA / CUDA [DEFAULT recipe]

- **Host machine must have:** an NVIDIA GPU, the **NVIDIA kernel driver installed on the
  host** (the driver is *not* containerizable - the kernel module lives on the host), and the
  **NVIDIA Container Toolkit** (`nvidia-container-toolkit`) so Docker can expose the device.
- **Run with:** `docker run --gpus all ...` (or `--runtime=nvidia`). Verify inside the container
  with `nvidia-smi`.
- **Inside the container:** a CUDA base image (`nvidia/cuda:12.x-devel-ubuntu24.04` or
  similar) gives you `libcuda`/`libcudart`, `ptxas`, `nvcc`, `cuobjdump`, `fatbinary`. For
  **Strategy 1** (driver API + PTX, §5.2) you mainly need `libcuda` (the driver API) and
  `ptxas` (CUDA can JIT PTX→SASS at `cuModuleLoadData` time, so you can even skip explicit
  `ptxas`). Confirm: `ptxas --version`, and a trivial `cuInit` program links against
  `-lcuda`.
- **Driver/toolkit version coupling:** the container's CUDA toolkit version must be supported
  by the **host driver** (newer toolkit needs newer-or-equal driver). This is the most common
  bring-up failure. `nvidia-smi` on the host shows the max CUDA version the driver supports.

### 8.2 AMD / ROCm

- Host needs the **`amdgpu` kernel driver** and a ROCm-supported GPU; container needs the ROCm
  stack (`rocm/dev-ubuntu-24.04`-style image) with `libamdhip64`/`libhsa-runtime64`.
- Expose devices with `--device=/dev/kfd --device=/dev/dri` plus the right `render`/`video`
  group membership (not `--gpus`; ROCm uses the kfd/dri device nodes). Verify with `rocminfo`.
- Given §6, treat AMD as a later phase.

### 8.3 What the container does *not* solve

A container gets you the **runtime libraries and device access**. It does not supply the
**compiler work** in §2-§5. With the right container and *today's* code you would have a GPU
and `libcuda` and still not be able to launch anything, because there is no kernel entry, no
intrinsics, and no host launch path. The container is necessary, not sufficient - sequence it
**after** §3-§5 are real, or in parallel if someone else owns the compiler side. (A cheap early
win: stand up the container now and confirm `nvidia-smi`/a `cuInit` smoke test, so the
environment is derisked before the compiler work lands.)

---

## 9. Suggested order of execution

Each step is independently landable and keeps host/vintage byte-identical.

1. **A1-A3 (self-contained device IR).** Cheapest, and unblocks every later artifact check.
   Without it nothing GPU-side links. *(Milestone A.)*
2. **B (entry points → `.entry`).** One-line codegen mechanism, verified; export-driven in the
   `DEVICE UNIT` model, so no new syntax (§3). *(Milestone B.)*
3. **C.1/C.2 (thread-index intrinsics + `SYNCTHREADS`), with CPU-device lowerings.** Now a
   kernel can be *written* and validated for correctness on the CPU device. *(Milestone D core.)*
4. **PTX emission driver mode** (§5.3) - turn device IR into a `.ptx` artifact via the device
   `TargetMachine` (proven to work).
5. **CPU-device orchestration shim** (§7) - `DEVALLOC`/copies/`LAUNCH` as malloc/memcpy/call;
   prove the *whole vector-add program* runs end-to-end with no GPU.
6. **Stand up the CUDA container** (§8) and confirm `nvidia-smi` + a `cuInit` smoke test.
7. **CUDA orchestration shim** (§5.2 Strategy 1) - swap the CPU shim for `libcuda` driver-API
   calls + `cuModuleLoadData(ptx)`. **First real GPU launch here.**
8. **Datalayout/alloca hygiene** (§6) - fixes AMDGPU and is latently correct for NVPTX.
9. **Ergonomics & breadth:** `FORALL`, `GRID/BLOCK` sugar, width changes (`REAL32`/`HALF`,
   32-bit index), device helper libraries (`DEVICE UNIT` uses `DEVICE UNIT`) and cross-kind
   `uses`-rule enforcement, fatbinary path, and freezing the rest of the recission set
   (including the §1.5.3 initializer-block ban).

Milestones 1-7 are the path to a running CUDA kernel. 8-9 are breadth and polish.

## 10. Definition of done (the smoke test)

A committed, reproducible test that:
1. ~~compiles a vector-add **device unit** (one exported entry) to `nvptx64`, asserts the PTX
   has a `.visible .entry` for the exported routine, that any non-exported helper stays
   `.func`, and **zero** host-runtime symbol references (the §2 denylist)~~ **[DONE]**
   `tests/test_device_entry_points.py` + `tests/test_device_no_host_externs.py` cover this
   in full; PTX `.visible .entry` assertion included where the NVPTX target is available.
2. runs the same kernel through the **CPU-device** body path end-to-end and checks the
   numeric result (no GPU needed - runs in CI on this VM) - **[DONE for Milestone C]**:
   `tests/integration/test_device_grid_stride.py` compiles a `DEVICE UNIT` grid-stride
   vector-add, links it with a host `PROGRAM`, runs it on `device=x86`, and checks the
   numeric result. The future §5 host launch/orchestration shim remains prescribed for the
   CUDA-style `LAUNCH(...)` surface; this test proves the kernel body contract now works;
3. *(gated on `@requires_gpu`)* runs the kernel through the **CUDA** shim in the container and
   checks the same result against a real device - **[PRESCRIBED]**: requires §5 CUDA shim
   and §8 container setup.

Point (1) is green. Points (2) and (3) are the remaining work; together they define when you
have a *running* CUDA kernel.

---

## Appendix - verification log for this document

### Original baseline (2026-06-19, 607 tests)

Commands/results behind the original [VERIFIED]/[GAP] tags:

- **CPU-device end-to-end:** built `runtime/` (`make` → `libpascalrt.a`), compiled
  `kernel.pas`+`main.pas` at default `device=x86`, `clang`-linked, ran → 25 primes. ✔
- **NVPTX space-correct IR:** `--device-triple nvptx64-nvidia-cuda` → 22 `addrspace`
  occurrences; SHARED→`addrspace(3)`, GLOBAL→`addrspace(1)`; bridge loads `(3)`, stores `(1)`. ✔
- **PTX emission:** parsed+verified the NVPTX IR, `TargetMachine(cpu=sm_70).emit_assembly` →
  valid PTX, `work_flags` in `.shared`, `prime_flags` in `.global`, `ld.shared`→`st.global`. ✔
- **No kernels (gap, now closed):** that PTX had `.entry`×0, `.func`×5; IR had no
  `nvvm.annotations`. Gap closed by §3 / Phase 2.3. ✔
- **Host-symbol leak (gap, now closed):** NVPTX IR contained `call void @abort()` ×10 and
  `call i32 @fflush(...)`, plus unconditional `declare`s for `movel/movesl/pas_read_int/
  memmove/...`. Gap closed by §2 / Phases 2.1 + 2.2 lazy plan. ✔
- **Recission partly built (now complete):** `_DEVICE_BANNED_IO`/`_DEVICE_BANNED_HEAP` +
  `_detect_device_recursion` in place; extended to `DEVICE UNIT` and init-block ban added. ✔
- **Kernel calling-convention mechanism verified:** `func.calling_convention = "ptx_kernel"` →
  `.visible .entry` in PTX. Now wired into `codegen_proc_decl` for exported device-unit
  routines. ✔
- **AMDGPU back end aborts:** `amdgcn-amd-amdhsa`/`gfx900` `emit_assembly` on `build_primes` →
  `LLVM ERROR: Cannot select: FrameIndex<0>`. Still open (§6). ✔
- **`USES` verified and integration-tested:** PLOTBOX/GRAPHICS multi-file example ran in both
  plain and renamed forms after two-line codegen fix. Now covered by `tests/integration/`. ✔

### Updated baseline (2026-06-19, 705 tests)

- **Suite:** `705 passed, 63 subtests` on `device-code` branch.
- **§2 gaps closed:** `tests/test_device_no_host_externs.py` (nvptx64/amdgcn, full denylist
  absent, zero `declare`s) and `tests/test_lazy_externs.py` (zero-declare guarantee +
  INPUT/OUTPUT ownership).
- **§3 gap closed:** `tests/test_device_entry_points.py` (ptx_kernel / amdgpu_kernel on
  exports, helpers stay plain, PTX `.visible .entry` asserted, entry-shape rules enforced).
- **§1.5 complete:** `DEVICE UNIT` (parser/checker/codegen/tests) fully implemented;
  integration-test tier running at `tests/integration/`.
- **§4-§6 remain open:** thread-index intrinsics, barriers, host orchestration,
  AMDGPU datalayout/alloca - all still prescribed.
