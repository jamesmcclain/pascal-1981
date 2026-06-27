# Plan: collapse the GPU device-build pipeline to three commands

Status: PROPOSED. No code changed yet — this is the design.

## 1. Where the bodies are buried (current state)

The end-to-end GPU path for an example (`examples/device_ptx/mandelbrot`,
`fill_indices`) is driven by `examples/device_ptx/device-example.mk` and the
hand-written `scripts/build-cuda-host.sh`. For `DEVICE=cuda` it does **five**
build actions plus a full runtime rebuild, for two source files:

```
1. dev.ptx   = python -m pascal1981.compile_to_ptx  dev.pas  --cpu sm_86   # device -> PTX
2. dev.ll    = python -m pascal1981                  dev.pas               # device -> host-x86 .ll
3. host.ll   = python -m pascal1981 --embed-device-ptx dev.ptx host.pas    # host -> .ll, PTX baked in
4. runtime   = make -C runtime clean && make -C runtime DEVICE_SHIM=cuda   # wholesale archive rebuild
5. link      = clang host.ll dev.ll libpascalrt.a -L.../stubs -lcuda -o exe
```

(`build-cuda-host.sh` has an extra step 3 compiling the interface `.inc` too.)

### The jank, itemized

- **J1 — the device unit is compiled twice, for two unrelated reasons.**
  Once to NVPTX PTX (the real kernel), once to a *host-x86* `.ll` whose only
  job is to define the kernel symbol so the link resolves. The second compile
  produces dead code: it never runs on the GPU.

- **J2 — `dev.ll` exists solely to satisfy a link-time reference from dead
  code.** Host codegen emits, for every `LAUNCH`, an internal dispatch thunk
  `__pas_klaunch_<kernel>` that *calls the external kernel symbol*
  (`codegen/stmts.py::_kernel_launch_thunk`). That thunk is the CPU-device
  stand-in; on the GPU the CUDA shim dispatches the kernel by name out of the
  loaded module (`runtime/cuda_launch.c`) and the thunk is never called. But
  because the thunk *statically references* `@<kernel>`, the linker demands a
  definition, so we drag in `dev.ll`. The reference is real; the call is dead.

- **J3 — host `.ll` is coupled to the device artifact via `--embed-device-ptx`.**
  The PTX text is baked into `host.ll` as the `__pas_device_ptx` blob at host
  compile time (`codegen/stmts.py::_device_ptx_ptr`). So "compile the host"
  cannot run before "compile the device," and any PTX change forces a host
  recompile. The host source has nothing to do with the kernel text; this is a
  packaging concern leaking into the compiler front end.

- **J4 — two CLIs with divergent flags and defaults.** `pascal1981` and
  `pascal1981.compile_to_ptx` duplicate `--device-triple`, `-f`, `--dialect`,
  and disagree on defaults (`--cpu sm_70` vs none; device-triple host vs NVPTX).
  The PTX driver re-implements parse/check/lower glue.

- **J5 — the runtime archive is rebuilt from clean on every GPU build.** The cpu
  and cuda shims define the same `pas_dev_*` symbols and cannot coexist in one
  archive, so the Makefile's `runtime-cuda` target does `make clean && make
  DEVICE_SHIM=cuda` every time. There is no prebuilt-runtime story.

## 2. Target workflow (the goal)

Runtime is prebuilt **once**. Then, per example, exactly three commands:

```bash
# 1. one command against the device file -> .ptx (+ optional .ll, + embeddable object)
pascal1981 --target ptx  mandelbrot.pas  mandelbrot.ptx  --sm sm_86  -f wide-integers

# 2. one command against the host file -> .ll  (no PTX coupling)
pascal1981 --target host --device-backend cuda  mandelbrot_host.pas  mandelbrot_host.ll  -f wide-integers

# 3. one clang command to link the host
clang mandelbrot_host.ll mandelbrot.ptx.o  libpascalrt_cuda.a  -L$CUDA/lib64/stubs -lcuda -o mandelbrot_host
```

`ptxas`/`cubin` stays optional (a stronger check, or an `.o` route — see §3.3).
No second device compile. No `dev.ll`. No runtime rebuild. The host `.ll` is
independent of the kernel text.

## 3. The changes

### 3.1 Kill `dev.ll` by gating the CPU stand-in machinery (fixes J1, J2)

Root cause is the thunk's static reference to the kernel symbol. Add a host
compile knob `--device-backend {cpu,cuda}` (plumbed into the codegen
constructor, `codegen/base.py`). Then in `_codegen_device_orchestration` /
`_emit_launch_registry`:

- **backend=cuda:** do **not** emit the `__pas_klaunch_<kernel>` thunk or the
  `__pas_klaunch_registry` table. The GPU launch path only needs
  `pas_dev_module_load(registry=NULL, ptx)` → `pas_dev_module_get_function(mod,
  name)` → `pas_dev_launch(entry, geom, argv)`. Pass a null registry pointer;
  the cuda shim already ignores it (`runtime/cuda_launch.c::pas_dev_module_load`
  casts `registry` to `(void)`). With no thunk, there is **no reference to the
  kernel symbol in host `.ll`**, so the link needs no `dev.ll`.

- **backend=cpu:** unchanged — emit thunk + registry exactly as today. The CPU
  device still resolves and calls the thunk.

Fallback if we want to keep the thunk for symmetry: emit the kernel extern as
`extern_weak` so an undefined symbol resolves to null instead of forcing a
definition. Preferred is to drop it entirely on the GPU path — less dead IR.

Net: the GPU build compiles the device unit **once** (to PTX) and never produces
or links `dev.ll`.

### 3.2 Decouple PTX embedding from host compile (fixes J3)

Stop baking PTX into `host.ll`. Instead, the host references an *external*
`__pas_device_ptx` symbol, and the PTX blob becomes its own object linked at
step 3. Two ways to produce that object from `mandelbrot.ptx`; pick one:

- **(a) emit it from the device command.** `--target ptx` also writes
  `mandelbrot.ptx.o` (or `.s`) defining `const char __pas_device_ptx[]` via an
  `.incbin`-style stub or `llvm-mc`. Keeps "one command against the device file"
  literally true and the link a single `clang ... mandelbrot.ptx.o ...`.

- **(b) objectify at link time** with a documented one-liner
  (`ld -r -b binary`, or a 3-line `.s` using `.incbin "mandelbrot.ptx"`). The
  clang link line gains one input; the host `.ll` stays pure.

Either way `codegen/stmts.py::_device_ptx_ptr` changes from "embed the text" to
"declare `external global` `__pas_device_ptx`," and `--embed-device-ptx`
becomes optional/legacy. Host compile no longer depends on the device artifact.

If we would rather not add a link input, the legacy `--embed-device-ptx` path
can stay as an opt-in for a strictly two-input link — but the default clean path
should decouple.

### 3.3 Optional `ptxas` / cubin route

For users who want the assembled artifact: `--target ptx` can additionally drive
`ptxas -arch=$SM -o mandelbrot.cubin mandelbrot.ptx` when the toolkit is
present, and §3.2's object can embed the cubin instead of PTX (the cuda shim
then `cuModuleLoadData`s a cubin, which it already accepts). This is a strict
add-on; the PTX-text path remains the no-GPU-needed default.

### 3.4 Fold the two CLIs into one (fixes J4)

Make `--target {host,ptx}` a flag on the single `pascal1981` driver
(`compile_to_llvm.py::main`), sharing feature resolution, dialect, and check
flags. `--target ptx` sets the device triple to `nvptx64-nvidia-cuda`, honors
`--sm` (alias the old `--cpu`), and routes through the existing
`compile_to_ptx.llvm_ir_to_ptx`. Keep `python -m pascal1981.compile_to_ptx` as a
thin shim that forwards to `--target ptx` for back-compat and existing tests
(`tests/integration/test_device_mandelbrot_ptx.py`,
`fill_indices/RUNNING_PTX.md`).

### 3.5 Prebuild both runtime archives once (fixes J5)

Split the shim out of the single archive so neither dominates:

- Build a **core** archive `libpascalrt.a` (everything except the two
  `*_device_shim` / `cuda_launch` shims), plus two tiny shim archives
  `libpascalrt_dev_cpu.a` and `libpascalrt_dev_cuda.a`. Consumers link core +
  the chosen shim. No symbol clash, no rebuild.

  Or, simpler for callers: produce two full archives `libpascalrt_cpu.a` and
  `libpascalrt_cuda.a` in one `make` invocation (two `ar` outputs from one core
  object set + one shim each). Either removes the `runtime-cuda` clean-rebuild.

The example Makefile then just picks the archive; `runtime-cuda` (the phony that
does `make clean && make DEVICE_SHIM=cuda`) is deleted.

## 4. Resulting build files

- `device-example.mk` drops the `dev.ll` rule, the `runtime-cuda` phony, and the
  `--embed-device-ptx` on the host rule. The `cuda` branch becomes:
  ```make
  $(BUILD)/dev.ptx:  $(DEVICE_UNIT) ; $(PAS) --target ptx $< $@ --sm $(SM) $(FEATURES)
  $(BUILD)/dev.o:    $(BUILD)/dev.ptx ; <objectify per 3.2>
  $(BUILD)/host.ll:  $(HOST_SRC) ; $(PAS) --target host --device-backend cuda $(FEATURES) $< $@
  $(EXE): $(BUILD)/host.ll $(BUILD)/dev.o ; clang $^ $(RUNTIME_CUDA) -L$(CUDA_HOME)/lib64/stubs -lcuda -o $@
  ```
- `scripts/build-cuda-host.sh` collapses from 6 steps to 3 (+ optional ptxas),
  and stops rebuilding the runtime.

## 5. Migration / compatibility

- Keep `compile_to_ptx` and `--embed-device-ptx` working (deprecated aliases) so
  existing tests and the `RUNNING_PTX.md` external-launcher recipe keep passing.
- CPU-device path is untouched by design (backend=cpu keeps thunk+registry); the
  deferred grid-stride work in `CPU_DEVICE_TODO.md` is orthogonal.
- The PTX ABI is unchanged — same `.visible .entry`, same parameters — so the
  drop-in property the mandelbrot README sells (matching `mandelbrot.cu`
  symbol-for-symbol) is preserved. The validation ladder in
  `RUNNING_PTX.md`/`cuda-kernel-prescription.md` still applies rung for rung.

## 6. Validation

- Existing PTX-text + `ptxas` checks (mandelbrot/fill READMEs) must still pass on
  the new `--target ptx` output, byte-comparable to the old `compile_to_ptx`.
- A new check: host `.ll` built with `--device-backend cuda` has **no undefined
  kernel symbol** and **no `__pas_klaunch_` thunk** (`grep`-able), proving J1/J2
  are gone.
- Link the three-command path on a GPU box and run the existing host programs;
  output (ASCII mandelbrot, `OK: all 256 indices correct`) must be unchanged.
```
