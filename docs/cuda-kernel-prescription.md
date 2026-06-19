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
| §3 | There are **no entry points**, only device functions | PTX has `.func` ×5, `.entry` ×0; no `nvvm.annotations` |
| §4 | **No parallel execution model** | the "kernel" is a serial sieve; no `threadIdx`, no barrier, no grid |
| §5 | **No host orchestration** | nothing allocates device memory, copies buffers, or launches |
| §6 | **AMDGPU back end crashes** (bonus, ROCm-only) | `LLVM ERROR: Cannot select: FrameIndex` on `build_primes` |

Milestones below are ordered so each one is independently testable and the host/vintage path
stays byte-identical throughout.

---

## 1. The end-state we are building toward

A minimal but *real* CUDA bring-up: a device compiland exporting one kernel that does
something embarrassingly parallel (vector add is the canonical smoke test — keep the sieve as
a second step), a host `PROGRAM` that allocates two input buffers and one output buffer on the
device, copies inputs up, launches the kernel over an N-thread grid, copies the result back,
and prints it.

Written in the `DEVICE UNIT` shape recommended in §1.5 — where the launchable entry points are
exactly the routines the unit *exports* (§3) — the device side is an interface plus an
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
.   { no initializer block — forbidden in a DEVICE UNIT, §1.5.3 }
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

## 1.5 Foundational decision — `DEVICE UNIT` vs `DEVICE MODULE` [RECOMMENDED: `DEVICE UNIT`; owner-ratifiable]

This choice is foundational: it determines how the host names a kernel to launch it (§5.4) and
how launchable entry points are distinguished from device-internal helpers (§3). It was
reopened by a correction to an earlier claim.

### 1.5.1 Correction: `USES` is not broken [VERIFIED — reproduced and fixed]

An earlier pass concluded "`uses` codegen is broken" and routed around it with `EXTERN`-by-name.
That was wrong, and rested on a wrong mental model — that `USES` is how you reach a `MODULE`'s
exports. In the vintage dialect, `USES` pairs with a **`UNIT`** (an `INTERFACE` +
`IMPLEMENTATION OF` pair), not with a plain `MODULE`. Reproduced against the tree:

- The grammar, type checker, and codegen all implement `INTERFACE`/`UNIT`/`USES`/
  `IMPLEMENTATION OF`; the checker even resolves the manual's positional **renaming** import
  (`USES GRAPHICS (MOVE, PLOT)` aliasing the exported `BJUMP, WJUMP`).
- The only real defects were two small codegen bugs, both now fixed (patch `uses-fix.patch`):
  **(1)** `codegen_use_clause` called `parse_file` without importing it — *that* was the
  "undefined `parse_file`" the after-action report saw; a one-line fix. **(2)** The positional
  rename was not threaded into codegen, so a renamed import's call site found no symbol; fixed by
  declaring the external under its real exported name and binding the alias to it.
- With those applied, the IBM manual's PLOTBOX/GRAPHICS example (stubbed) **compiles, links
  against a separately-compiled `IMPLEMENTATION OF GRAPHICS`, and runs** — both the plain and the
  renamed `USES` forms. 607 tests stay green. (See the multi-file example shipped beside this
  doc; it is the seed of a future integration test — §1.5.4.)

So `USES` works, and the design's intended "host `uses` the device code to get launchable
kernels" is a *live* path — but only if the device code is a `UNIT`.

### 1.5.2 The decision

Because `USES` is a `UNIT` mechanism, a device compiland that the host launches by name should be
a **`DEVICE UNIT`** (an `INTERFACE` + `IMPLEMENTATION OF`), not a `DEVICE MODULE`. The
recommendation is to adopt `DEVICE UNIT`. Dividends:

- **Entry points fall out of exports, with no new syntax (§3).** A unit's interface lists what it
  exports; those exported routines are exactly the launchable kernels, and everything in the
  implementation the interface does *not* export is a device-internal helper `.func`. This
  answers — for free — the "how do we mark entry points" question that a `DEVICE MODULE` would
  otherwise need an annotation for. (This supersedes the earlier `[KERNEL]`-on-every-routine
  idea, which was redundant: being inside a device compiland already makes code device code; the
  thing that needs marking is *entry-ness*, and the export list supplies it.)
- **Host launch is the verified `USES` path (§5.4),** not an `EXTERN`-by-name workaround.
- **Device helper libraries compose,** matching the manual's two-tier shape (a `UNIT` that `USES`
  another `UNIT` — GRAPHICS uses BASEPLOT). A `DEVICE UNIT` may `uses` another `DEVICE UNIT` for
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
matter. Where it *does* matter — entry-point marking (§3) and host launch (§5.4) — both the
export-driven (`UNIT`) and the annotation (`MODULE`) routes are given.

### 1.5.3 Rescission: no initializer code in a `DEVICE UNIT` [DECIDED 2026-06-19]

A vintage `UNIT` may carry an **initializer block** — the optional `BEGIN … END` in an interface,
and the `BEGIN … END.` body of an `IMPLEMENTATION OF`. On a device there is no host-style
"module load runs this once" moment, and an initializer would smuggle in exactly the
host-runtime, ordering-dependent code the device dialect is trying to keep out. So **a
`DEVICE UNIT` may not have an initializer block**, in either the interface or the implementation.
This is a new module-scoped **rescission**, in the same family as recursion / `NEW`-heap /
host-I/O (design §9; prescription §2.3.A4): enforce it as a checker ban when the unit is a device
unit and an init block is present — *"initializer code is not available in a DEVICE UNIT."* A
device implementation therefore ends after its declarations (no trailing `BEGIN … END.`); a
device interface ends at `END;` with no `BEGIN`.

### 1.5.4 The multi-file `USES` example (future integration test)

The faux-graphics example reproduced for §1.5.1 ships as a `.zip` beside this doc. It is
deliberately **multi-file** (a program, an interface unit, and an implementation unit, compiled
separately and linked) — which is *why it is not a normal in-process unit test*: it exercises the
on-disk interface resolution, separate compilation, and cross-unit linking that a single-buffer
parser/checker test cannot reach. It is the first concrete candidate for an **integration-test**
tier (compile N files → link → run → diff stdout). Standing up that tier is future work; until
then the example doubles as a manual smoke test for the `USES` path.

---

## 2. Milestone A — make device IR self-contained (the recission gap, for real)

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

## 3. Milestone B — emit *entry points*, not just device functions

**This is the §3 gap and the single thing that makes an artifact launchable.** A PTX `.func`
cannot be the target of `cuLaunchKernel`; only a `.entry` can. Today every device routine is a
`.func`.

The framing matters. Being inside a device compiland already makes a routine device code — that
is *not* what needs marking (so there is **no `[KERNEL]`-on-everything** marker; that earlier idea
was redundant). What needs marking is which device routines are **launchable entry points**
versus device-internal helpers. Both are device code; only an entry point gets the kernel calling
convention and is findable by `cuModuleGetFunction`. A helper (say a `device_min(a,b)` the kernel
calls) must stay a plain `.func`, or it pays launch-ABI overhead on an internal call and clutters
the launchable-symbol namespace.

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

### 3.2 Which routines become entry points

**[RECOMMENDED — export-driven, ties to §1.5]** In the `DEVICE UNIT` model, an entry point is a
routine the unit's **interface exports**; everything in the implementation that is not exported
is a device-internal helper `.func`. No new syntax: the export list *is* the entry-point list.
This is the preferred answer and the main reason §1.5 leans `DEVICE UNIT`.

**[ALTERNATIVE — explicit `[ENTRY]` annotation]** If the device compiland is a single-file
`DEVICE MODULE` (no interface to read exports from), mark the launchable routines with an
`[ENTRY]` header attribute — note *entry*, not *kernel*: the routine is device code regardless;
the attribute only says it is launchable. This reuses the existing `attribute_section` on
proc/func headers (grammar 196/209), so it needs no new grammar. Default without `[ENTRY]` is a
device-internal `.func`.

Either way, enforce in the checker:
- entry-ness is legal only in a device compiland;
- an entry point returns nothing — restrict to `PROCEDURE` (kernels write results through
  `GLOBAL` pointers); **[DEFAULT]** procedures only;
- entry parameters must be device-passable: scalars or `ADS(GLOBAL/CONSTANT) OF T`; reject
  `HOST`-space pointers (the dereferenceability invariant half-does this already).

### 3.3 Codegen [PRESCRIBED]

In `codegen_proc_decl` (`decls.py`), when a routine is an entry point (exported from a device
unit, or `[ENTRY]`-marked) and the unit lowers to a GPU triple:
- set `func.calling_convention = "ptx_kernel"` (nvptx) / `"amdgpu_kernel"` (amdgcn), chosen off
  `self.device_triple`;
- optionally add the `nvvm.annotations` entry;
- ensure entry params that are `ADS(GLOBAL) OF T` lower to `T addrspace(1)*` (the Step-4a type
  lowering does this for variables — verify it fires for *parameters*; param lowering was the
  deferred 4b slice, see §6 note).

**Acceptance:** compile a device unit exporting one entry to `nvptx64`, emit PTX, assert
`.entry <name>` appears, the param is a `.param .u64` into global, **and a non-exported helper in
the same implementation stays `.func`.** Add the symmetric AMDGPU assert once §6 is fixed.

**Green gate:** non-entry device routines still emit `.func`; host unaffected.

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

### 5.4 Host-side launch surface (host `USES` the device unit) [PRESCRIBED]

- A host `PROGRAM`/`MODULE` names a kernel to launch it via the **verified `USES` path** (§1.5):
  `USES vadd (add);` imports the entry `add` by name, and `LAUNCH(add, …)` launches it. This is
  the intended design path (host `uses` device code to get launchable kernels) and it **now
  works** — the earlier "`uses` is broken, use `EXTERN`-by-name" guidance was based on a
  since-fixed one-line bug and is **rescinded** (`uses-fix.patch`).
- **What "launch" lowers to.** `LAUNCH(add, …)` does not call `@add` directly — the host cannot
  call a GPU function. It lowers to the host shim's `pas_dev_launch(module, "add", …)` (§5.2),
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
- §3 (entry points): on `device=x86` the kernel calling convention is inert/ignored — kernel
  *logic* still runs serially, so you can test kernel *correctness* on CPU before you have a GPU.
- §4 (intrinsics): provide CPU-device lowerings — `THREADIDX_X`→0, `BLOCKDIM_X`→1,
  `SYNCTHREADS`→no-op — so a kernel run on the CPU executes as a single-thread grid and
  produces the right scalar answer. This lets you validate kernel math with zero GPU.
- §5 (orchestration): a CPU-device shim where `DEVALLOC`=`malloc`, copies=`memcpy`, `LAUNCH`=a
  direct call. Same Pascal program, no GPU. Then swap the shim for the CUDA one.

This is the CPU-device dividend the design designed for; lean on it.

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
2. **B (entry points → `.entry`).** One-line codegen mechanism, verified; export-driven in the
   `DEVICE UNIT` model, so no new syntax (§3). *(Milestone B.)*
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
   32-bit index), device helper libraries (`DEVICE UNIT` uses `DEVICE UNIT`) and cross-kind
   `uses`-rule enforcement, fatbinary path, and freezing the rest of the recission set
   (including the §1.5.3 initializer-block ban).

Milestones 1–7 are the path to a running CUDA kernel. 8–9 are breadth and polish.

## 10. Definition of done (the smoke test)

A committed, reproducible test that:
1. compiles a vector-add **device unit** (one exported entry) to `nvptx64`, asserts the PTX has
   a `.visible .entry` for the exported routine, that any non-exported helper stays `.func`, and
   **zero** host-runtime symbol references (the §2 denylist);
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
- **`USES` is not broken (§1.5.1):** the IBM manual PLOTBOX/GRAPHICS example (stubbed,
  OCR-corrected) — a `PROGRAM` that `USES` a separately-compiled `INTERFACE`/`IMPLEMENTATION OF
  GRAPHICS` — compiled, linked, and ran, in both plain (`USES GRAPHICS`) and renamed
  (`USES GRAPHICS (MOVE, PLOT)`) forms, after a two-line codegen fix (`parse_file` import + rename
  binding; `uses-fix.patch`). The renamed form lowered `MOVE`→`@BJUMP`, `PLOT`→`@WJUMP`. 607
  tests stay green. ✔ (Multi-file example shipped beside this doc — §1.5.4.)
- **Tests:** `607 passed, 52 subtests`. ✔
