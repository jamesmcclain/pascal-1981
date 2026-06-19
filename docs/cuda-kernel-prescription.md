# From Here to a Running CUDA Kernel — A Prescription

**Companion to** `ads-memory-spaces-design.md` and `ads-implementation-plan.md`. Those
documents took the `ADS`/address-space *type-system slice* from idea to validated
`addrspace(k)` IR. This document picks up where they stop and lays out the remaining work to
get an actual GPU kernel to **launch, run, and return a result** on real hardware.

**Audience:** the next agent/instance (or a human) continuing this build. Read it cold.

**Status tags** (same convention as the design record):
- **[VERIFIED]** — checked against the tree or empirically reproduced while writing this doc
  (2026-06-19), with the command/result noted.
- **[GAP]** — something believed done that is *not* done, or only partly done. These are the
  load-bearing corrections.
- **[PRESCRIBED]** — proposed work, not yet built.
- **[DEFAULT]** — a reasonable assistant-chosen default for an unratified question; flag before
  building on it heavily.

> **A note on "closed" items.** This is a vibe-coding build, and several things the design
> docs mark as done are done *narrowly*. The single most important lesson from the
> verification pass below: **a call-site ban is not the same as keeping a symbol out of the
> emitted IR.** The recission work (§2) is the worked example. Re-verify "done" items by
> reading the *emitted artifact*, not the checker.

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
- **[VERIFIED] 607 tests pass.**

So the type system, the space lattice, and the addrspace lowering are genuinely finished and
validated. What follows is everything *else* that a CUDA kernel needs, none of which the
addrspace slice touched.

The four findings that gate a real kernel, each expanded below:

| # | Gap | Symptom (verified) |
|---|-----|--------------------|
| §2 | Device IR is **not self-contained** | `kernel_nvptx.ll` *calls* `abort`/`fflush` and declares `pas_read_int`/`memmove`/`movel`… — all host symbols |
| §3 | There are **no kernels**, only device functions | PTX has `.func` ×5, `.entry` ×0; no `nvvm.annotations` |
| §4 | **No parallel execution model** | the "kernel" is a serial sieve; no `threadIdx`, no barrier, no grid |
| §5 | **No host orchestration** | nothing allocates device memory, copies buffers, or launches |
| §6 | **AMDGPU back end crashes** (bonus, ROCm-only) | `LLVM ERROR: Cannot select: FrameIndex` on `build_primes` |

Milestones below are ordered so each one is independently testable and the host/vintage path
stays byte-identical throughout.

---

## 1. The end-state we are building toward

A minimal but *real* CUDA bring-up: a `DEVICE MODULE` exporting one kernel that does
something embarrassingly parallel (vector add is the canonical smoke test — keep the sieve as
a second step), a host `PROGRAM` that allocates two input buffers and one output buffer on the
device, copies inputs up, launches the kernel over an N-thread grid, copies the result back,
and prints it. Concretely:

```pascal
DEVICE MODULE vadd;
PROCEDURE add(a, b, c: ADS(GLOBAL) OF REAL; n: INTEGER); [KERNEL];
VAR i: INTEGER;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;   { the parallel index }
  IF i < n THEN
    c^[i] := a^[i] + b^[i]
END;
.
```

```pascal
PROGRAM main(output);
{ host-side device API, see §5 }
...
BEGIN
  da := DEVALLOC(n * SIZEOF(REAL));  { etc. }
  DEVCOPYTO(da, @ha, n * SIZEOF(REAL));
  ...
  LAUNCH(add, GRID(blocks), BLOCK(threads), da, db, dc, n);
  DEVCOPYFROM(@hc, dc, n * SIZEOF(REAL));
  ...
END.
```

Everything between here and that program is the prescription.

---

## 2. Milestone A — make device IR self-contained (the recission gap, for real)

**This is the §2 gap and the answer to "I asked the agent to forbid host runtime-backed
builtins and host I/O — I guess it didn't do it?"** It *partly* did. Here is the exact state.

### 2.1 What was actually built [VERIFIED]

`type_checker.py` has a real first-tranche recission:
- `_DEVICE_BANNED_HEAP = {NEW, DISPOSE}` and `_DEVICE_BANNED_IO = {WRITE, WRITELN, READ,
  READLN, PAGE, RESET, REWRITE, GET, PUT, CLOSE, DISCARD, ASSIGN, READFN, READSET}` are
  rejected at the call site inside a `DEVICE MODULE` (`_check_device_recission`, ~`:148`).
- Direct and mutual recursion among device routines is detected at module end
  (`_detect_device_recursion`).

So if your kernel source *writes* `WRITELN(...)` or `NEW(p)`, you get a clean error today.
That part is done.

### 2.2 Why host symbols still leak [VERIFIED — this is the real gap]

The ban is **call-site only**. It does nothing about host code the *compiler itself inserts*,
or about declarations dumped unconditionally. Two distinct leaks, both confirmed in
`kernel_nvptx.ll`:

1. **Compiler-inserted runtime checks call host functions.** The math-overflow check
   (`mathck`) wraps arithmetic (`i+i`, `j+i` in the sieve) and, on overflow, emits
   `call i32 @fflush(i8* null)` then `call void @abort()` (`runtime_builtins.py:emit_runtime_abort`,
   reached via `:201`). The device IR contains **ten** such `abort` calls. `abort`/`fflush` do
   not exist on a GPU; this IR cannot link or run there. `RANGECK`/`INDEXCK`/`NILCK`/`STACKCK`
   have the same shape. The recission list never sees these because they are not user-level
   builtin calls — they are codegen.

2. **Predeclared externs are dumped unconditionally.** `_register_predeclared_externs`
   (`base.py:217`) adds `fillc/fillsc/movel/mover/movesl/movesr/memmove/pas_read_int/
   pas_read_word/pas_read_real/…` to *every* module at construction, host or device, used or
   not. They show up as dead `declare` lines in device IR. Harmless to a permissive linker,
   noise to a GPU loader, and a real problem the moment any of them is actually referenced.

### 2.3 Prescription [PRESCRIBED]

Goal: **a `DEVICE MODULE` compiled to a GPU triple emits zero references to host runtime
symbols.** Make that an enforced invariant, not a hope.

- **A1. Suppress host-calling runtime checks in device modules.** In codegen, gate the
  check-emitting paths on `self.is_device_module`. Options, cheapest first:
  - *Disable* `MATHCK`/`RANGECK`/`INDEXCK`/`NILCK`/`STACKCK` inside device modules (treat them
    as forced-off there). Simplest; matches GPU reality where these traps don't exist. The
    `check_enabled` helper (`base.py`) is the choke point — make it return `False` for these
    flags when `is_device_module`.
  - *Or* lower a device-appropriate trap: NVPTX has `llvm.trap`/`trap;` PTX, AMDGPU
    `llvm.trap`/`s_trap`. If you want the checks to survive, emit `llvm.trap()` instead of the
    `fflush`+`abort` host pair when `is_device_module`. More faithful, more work. **[DEFAULT]**
    Start by disabling; add `llvm.trap` later if you want guard rails on-device.
- **A2. Make the predeclared-extern dump lazy or gated.** Either register the seg/runtime
  externs **on demand** (only when first referenced), or skip the host-runtime set when
  `is_device_module` and the unit lowers to a GPU triple. Lazy registration is the better
  long-term shape (it also de-noises host output) but is a wider change; the gated skip is the
  green-safe minimal move. The seg-bridge intercept (`_device_seg_bridge`) already does **not**
  use these externs in device modules, so nothing in a device module needs them.
- **A3. Add an emitted-IR guard test.** The lesson of this whole section: assert on the
  *artifact*. New test: compile the vector-add and sieve device modules to `nvptx64`, and
  assert the emitted module declares/references **none** of a denylist
  `{abort, fflush, memmove, movel, mover, movesl, movesr, fillc, fillsc, pas_read_int,
  pas_read_word, pas_read_real}`. This catches the class of bug, not just today's instance.
- **A4. (Optional, recommended) Freeze the rest of the recission set.** The candidates from
  the design (sets I/O, dynamic set ranges, flat-heap pointer-chasing, nonlocal GOTO) are
  still unfrozen. They are not blockers for vector-add, but freeze them before you let real
  kernels grow, or they will sneak host-runtime dependencies back in (set I/O in particular
  pulls in the runtime). `_DEVICE_RECISSIONS` in `features.py` is the registry for the
  flag-shaped ones; the construct-shaped ones extend `_check_device_recission`.

**Green gate:** host/vintage IR byte-identical (these are all `is_device_module`-gated); the
A3 guard test passes on both sample kernels.

---

## 3. Milestone B — emit kernels, not device functions (the `[KERNEL]` marker)

**This is the §3 gap and the single thing that makes an artifact launchable.** A PTX `.func`
cannot be the target of `cuLaunchKernel`; only a `.entry` can. Today every routine is a
`.func`.

### 3.1 The mechanism [VERIFIED — tested while writing this]

Two mechanisms exist; either yields a real `.entry`. Confirmed empirically on `nvptx64`/`sm_70`:

- **Calling convention.** Setting `func.calling_convention = "ptx_kernel"` on the llvmlite
  `ir.Function` produced `.visible .entry addone(...)` in emitted PTX. One line.
- **`nvvm.annotations` metadata.** Adding `!{ptr @k, !"kernel", i32 1}` to a named-metadata
  node `nvvm.annotations` is the classic CUDA marker and composes with the above.

For **AMDGPU** the equivalent is calling convention `amdgpu_kernel`.

**[DEFAULT]** Use the calling-convention route (`ptx_kernel` / `amdgpu_kernel`) as primary —
it is one assignment, target-uniform in shape, and verified. Add `nvvm.annotations` too if a
given CUDA loader path wants it.

### 3.2 Surface syntax [PRESCRIBED]

The design already scouted the home for this: `proc_decl_header`/`func_decl_header` carry an
`attribute_section` (grammar 196/209), so a **`[KERNEL]` header attribute** drops in with no
new grammar — exactly parallel to how `[SPACE(...)]` rides the variable attribute slot.
(A trailing directive in the `EXTERN`/`FORWARD` slot is the alternative; the attribute is
cleaner and the parser already parses it.)

```pascal
PROCEDURE add(a, b, c: ADS(GLOBAL) OF REAL; n: INTEGER); [KERNEL];
```

Rules to enforce in the checker:
- `[KERNEL]` is legal **only inside a `DEVICE MODULE`** (reuse the `in_device_module` gate).
- A kernel returns nothing — restrict to `PROCEDURE`, or require `FUNCTION` return to be
  ignored. **[DEFAULT]** procedures only; kernels write results through `GLOBAL` pointers.
- Kernel parameters must be device-passable: scalars, or `ADS(GLOBAL/CONSTANT) OF T`. Reject
  `HOST`-space pointers (the dereferenceability invariant already half-does this).
- A kernel may be `uses`-imported by a host module for launch (that is the *only* cross-kind
  reference allowed; §5.4).

### 3.3 Codegen [PRESCRIBED]

In `codegen_proc_decl` (`decls.py:381`), when the decl carries `KERNEL` and
`self.is_device_module`:
- set `func.calling_convention = "ptx_kernel"` (nvptx) / `"amdgpu_kernel"` (amdgcn), chosen
  off `self.device_triple`;
- optionally add the `nvvm.annotations` entry;
- ensure kernel params that are `ADS(GLOBAL) OF T` lower to `T addrspace(1)*` (the type
  lowering from Step 4a already does this — verify it fires for *parameters*, not just
  variables; param lowering was the deferred 4b slice, see §6 note).

**Acceptance:** compile a `[KERNEL]` proc to `nvptx64`, emit PTX, assert `.entry <name>`
appears and the param is a `.param .u64` pointing into global. Add the symmetric AMDGPU assert
once §6 is fixed.

**Green gate:** non-kernel device functions still emit `.func`; host unaffected.

---

## 4. Milestone C — the parallel execution model (what makes a kernel *viable*)

**This is the §4 gap, and the direct answer to "what language extensions do I need for viable
parallel kernels?"** A kernel with no thread indices is just a slow serial function that
happens to live on the GPU. Viable kernels need four things. None exist today; all are listed
as out-of-scope in the design's §9.

### 4.1 Thread/block index intrinsics [PRESCRIBED — required]

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
12–16 scalar reads, they are trivial and unlock every 1-D/2-D kernel.

### 4.2 Barriers / synchronization [PRESCRIBED — required for anything using SHARED]

`SYNCTHREADS` → `llvm.nvvm.barrier0` (NVPTX) / `llvm.amdgcn.s.barrier` (AMDGPU). Without it,
any kernel that stages through `[SPACE(SHARED)]` memory (exactly the pattern your sieve bridge
demonstrates) is racy. Register as a device-only builtin procedure. Add memory-fence variants
(`THREADFENCE`) later if needed.

### 4.3 The launch-bounds / signature contract [PRESCRIBED]

A kernel's grid/block geometry is supplied **at launch** (host side, §5), not in the kernel
body. The kernel just reads the intrinsics. So no new *kernel-body* syntax is needed beyond
§4.1/§4.2. What you do need is the host-side `GRID(...)`/`BLOCK(...)` surface — that lives in
§5.4.

### 4.4 A parallel-iteration statement [PRESCRIBED — optional, ergonomic]

`FORALL`-style sugar (`FORALL i IN 0..n-1 DO ...`) that expands to the
`i := global_thread_index; IF i < n THEN ...` guard is pure quality-of-life. **Defer it.**
Vector-add and the sieve are fine writing the index expression by hand. Build it once you have
two or three real kernels and the pattern is obvious.

### 4.5 Width reconsideration [PRESCRIBED — flag, decide later]

The dialect's `INTEGER` is 16-bit and `REAL` is f64. On GPUs: indices want 32/64-bit (a 16-bit
thread index caps you at 65 535 threads — fine for vector-add-100, wrong in general), and f64
is throttled hard relative to f32. **[DEFAULT]** For first bring-up, leave widths alone and
keep N small; before you benchmark anything, add `REAL32`/`HALF` and widen the index type used
by the thread-index intrinsics to `i32`. Note `MOVESL`'s length is `i16` today (the `WRD(limit)`
in the sieve) — a 64 KiB copy ceiling worth widening when you generalize the bridge.

---

## 5. Milestone D — host orchestration (allocate / copy / launch / copy back)

**This is the §5 gap and the answer to "what do I need to do around host orchestration?"**
Even with a perfect kernel and a GPU present, nothing today can put data on the device, start
the kernel, or read results. This is the largest *new-surface* piece of work.

### 5.1 What "orchestration" concretely is

Four host-side operations, mediated by the vendor runtime/driver:
1. **Allocate** device memory → returns a `GLOBAL` handle (an opaque device pointer the host
   holds but, by the dereferenceability invariant, may not dereference — the design already
   anticipated this: `GLOBAL` is an "opaque handle" in the host column of §3.2).
2. **Copy host→device** (H2D) and **device→host** (D2H).
3. **Launch** a kernel with a grid/block geometry and an argument list.
4. **Synchronize** and **free**.

### 5.2 Two implementation strategies

- **[DEFAULT] Strategy 1 — host calls a thin C shim that calls the CUDA Driver API.** Write a
  small `runtime/cuda_launch.c` exposing `pas_dev_alloc(size)→ptr`, `pas_dev_copy_to`,
  `pas_dev_copy_from`, `pas_dev_launch(module, name, gx,gy,gz, bx,by,bz, args…)`,
  `pas_dev_sync`, `pas_dev_free`, each wrapping `cuMemAlloc`/`cuMemcpyHtoD`/`cuMemcpyDtoH`/
  `cuModuleLoadData`/`cuModuleGetFunction`/`cuLaunchKernel`/`cuCtxSynchronize`/`cuMemFree`.
  Predeclare these as host externs (the same `_register_predeclared_externs` machinery, host
  side). The Pascal program calls Pascal builtins (`DEVALLOC`, `DEVCOPYTO`, `LAUNCH`, …) that
  lower to these extern calls. This is the least-LLVM-magic path and decouples you from
  fatbinary tooling: the host shim `cuModuleLoadData`s the **PTX string** you already emit.
- **Strategy 2 — fatbinary + CUDA Runtime API.** Bundle host object + device PTX/cubin into a
  fatbinary via `nvcc -fatbin`/`fatbinary`, and use the higher-level `cudaMalloc`/`cudaMemcpy`/
  `<<<>>>`-equivalent `cudaLaunchKernel`. More "native CUDA," but it drags in the fatbin
  toolchain and the runtime's hidden registration. **Defer.** Strategy 1 with the Driver API
  and a raw PTX module is dramatically simpler to stand up first and is fully sufficient to
  *run a kernel*.

### 5.3 Build-model consequence (the two-artifact problem) [GAP — partly real today]

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
- The current `--allow-multiple-definition` link hack in the example is a smell from both
  compilands emitting `input`/`output`/runtime globals; once device IR is self-contained (§2)
  and the device artifact is *PTX loaded at runtime* rather than `clang`-linked into the host
  binary, that hack disappears.

### 5.4 Host-side launch surface + kind-aware `uses` [PRESCRIBED]

- A host `MODULE`/`PROGRAM` must be able to name a kernel to launch it. The design's
  kind-aware `uses` (host may `uses` a `DEVICE MODULE` to get launchable kernels) is the
  intended path but is **deferred and `uses` codegen is currently broken** (the after-action
  report hit an undefined `parse_file` in the import path). **[GAP]** For first bring-up,
  **don't fix `uses`** — pass the kernel by *name string* to `LAUNCH('add', …)`, mirroring how
  the working example already uses `EXTERN`-by-name instead of `uses`. Fix `uses` later as its
  own task.
- `GRID(x[,y[,z]])` and `BLOCK(x[,y[,z]])` are just argument-packing sugar over the six
  `unsigned` geometry args to `cuLaunchKernel`. Start with plain integer args and add the sugar
  later.
- Kernel arguments cross the boundary as: scalars by value, device buffers as the opaque
  `GLOBAL` handle returned by `DEVALLOC`. `cuLaunchKernel` takes a `void**` of arg pointers;
  the shim assembles it.

### 5.5 Minimal orchestration acceptance

A host program that allocates, H2Ds two arrays, launches a 1-block/N-thread vector-add,
D2Hs, and prints the summed array — running on a real GPU (or the CPU-device stand-in, §7).

---

## 6. Milestone E — AMDGPU/ROCm (only if you want both vendors)

**This is the §6 gap, ROCm-specific.** It is *not* on the critical path to a CUDA kernel —
skip it if NVIDIA is the target — but record it so it is not mistaken for "validated."

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
  string the back end expects — pull it from the target, don't hand-roll).
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
- §3 (`[KERNEL]`): on `device=x86` the calling convention is inert/ignored — kernel *logic*
  still runs serially, so you can test kernel *correctness* on CPU before you have a GPU.
- §4 (intrinsics): provide CPU-device lowerings — `THREADIDX_X`→0, `BLOCKDIM_X`→1,
  `SYNCTHREADS`→no-op — so a kernel run on the CPU executes as a single-thread grid and
  produces the right scalar answer. This lets you validate kernel math with zero GPU.
- §5 (orchestration): a CPU-device shim where `DEVALLOC`=`malloc`, copies=`memcpy`, `LAUNCH`=a
  direct call. Same Pascal program, no GPU. Then swap the shim for the CUDA one.

This is the OpenCL-on-CPU dividend the design designed for; lean on it.

---

## 8. Vendor runtime considerations (the Docker question)

**Short answer: yes, a container with the vendor runtime + the GPU exposed is the right move,
with caveats.**

### 8.1 NVIDIA / CUDA [DEFAULT recipe]

- **Host machine must have:** an NVIDIA GPU, the **NVIDIA kernel driver installed on the
  host** (the driver is *not* containerizable — the kernel module lives on the host), and the
  **NVIDIA Container Toolkit** (`nvidia-container-toolkit`) so Docker can expose the device.
- **Run with:** `docker run --gpus all …` (or `--runtime=nvidia`). Verify inside the container
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
**compiler work** in §2–§5. With the right container and *today's* code you would have a GPU
and `libcuda` and still not be able to launch anything, because there is no kernel entry, no
intrinsics, and no host launch path. The container is necessary, not sufficient — sequence it
**after** §3–§5 are real, or in parallel if someone else owns the compiler side. (A cheap early
win: stand up the container now and confirm `nvidia-smi`/a `cuInit` smoke test, so the
environment is derisked before the compiler work lands.)

---

## 9. Suggested order of execution

Each step is independently landable and keeps host/vintage byte-identical.

1. **A1–A3 (self-contained device IR).** Cheapest, and unblocks every later artifact check.
   Without it nothing GPU-side links. *(Milestone A.)*
2. **B (`[KERNEL]` → `.entry`).** One-line codegen mechanism, verified; small grammar reuse.
   *(Milestone B.)*
3. **C.1/C.2 (thread-index intrinsics + `SYNCTHREADS`), with CPU-device lowerings.** Now a
   kernel can be *written* and validated for correctness on the CPU device. *(Milestone D core.)*
4. **PTX emission driver mode** (§5.3) — turn device IR into a `.ptx` artifact via the device
   `TargetMachine` (proven to work).
5. **CPU-device orchestration shim** (§7) — `DEVALLOC`/copies/`LAUNCH` as malloc/memcpy/call;
   prove the *whole vector-add program* runs end-to-end with no GPU.
6. **Stand up the CUDA container** (§8) and confirm `nvidia-smi` + a `cuInit` smoke test.
7. **CUDA orchestration shim** (§5.2 Strategy 1) — swap the CPU shim for `libcuda` driver-API
   calls + `cuModuleLoadData(ptx)`. **First real GPU launch here.**
8. **Datalayout/alloca hygiene** (§6) — fixes AMDGPU and is latently correct for NVPTX.
9. **Ergonomics & breadth:** `FORALL`, `GRID/BLOCK` sugar, width changes (`REAL32`/`HALF`,
   32-bit index), kind-aware `uses` (replacing launch-by-name-string), fatbinary path,
   freeze the rest of the recission set.

Milestones 1–7 are the path to a running CUDA kernel. 8–9 are breadth and polish.

## 10. Definition of done (the smoke test)

A committed, reproducible test that:
1. compiles a `[KERNEL]` vector-add `DEVICE MODULE` to `nvptx64`, asserts the PTX has a
   `.visible .entry` and **zero** host-runtime symbol references (the §2 denylist);
2. runs the same kernel through the **CPU-device** orchestration end-to-end and checks the
   numeric result (no GPU needed — runs in CI on this VM);
3. *(gated on `@requires_gpu`)* runs the kernel through the **CUDA** shim in the container and
   checks the same result against a real device.

When (1) and (2) are green in ordinary CI and (3) passes once in the container, you have a
running CUDA kernel and a regression net under it.

---

## Appendix — verification log for this document (2026-06-19)

Commands/results behind the [VERIFIED]/[GAP] tags, so the next instance can re-run them:

- **CPU-device end-to-end:** built `runtime/` (`make` → `libpascalrt.a`), compiled
  `kernel.pas`+`main.pas` at default `device=x86`, `clang`-linked, ran → 25 primes. ✔
- **NVPTX space-correct IR:** `--device-triple nvptx64-nvidia-cuda` → 22 `addrspace`
  occurrences; SHARED→`addrspace(3)`, GLOBAL→`addrspace(1)`; bridge loads `(3)`, stores `(1)`. ✔
- **PTX emission:** parsed+verified the NVPTX IR, `TargetMachine(cpu=sm_70).emit_assembly` →
  valid PTX, `work_flags` in `.shared`, `prime_flags` in `.global`, `ld.shared`→`st.global`. ✔
- **No kernels:** that PTX has `.entry`×0, `.func`×5; IR has no `nvvm.annotations`. ✔ (§3 gap)
- **Host-symbol leak:** NVPTX IR contains `call void @abort()` ×10 and `call i32 @fflush(...)`,
  plus unconditional `declare`s for `movel/movesl/pas_read_int/memmove/…`. ✔ (§2 gap)
- **Recission *is* partly built:** `_DEVICE_BANNED_IO`/`_DEVICE_BANNED_HEAP` +
  `_detect_device_recursion` reject explicit `WRITE`/`NEW`/recursion in device modules. ✔
- **Kernel marker works:** `func.calling_convention = "ptx_kernel"` on a toy module →
  `.visible .entry` in PTX. ✔ (§3 mechanism)
- **AMDGPU back end aborts:** `amdgcn-amd-amdhsa`/`gfx900` `emit_assembly` on `build_primes` →
  `LLVM ERROR: Cannot select: FrameIndex<0>`. ✔ (§6 gap)
- **Tests:** `607 passed, 52 subtests`. ✔
