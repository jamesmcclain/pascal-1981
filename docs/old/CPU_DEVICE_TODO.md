# CPU device support for the `device_ptx` examples — DONE

The Makefiles in `fill_indices/` and `mandelbrot/` accept `DEVICE=cpu` and
`DEVICE=cuda`. Only `DEVICE=cuda` is wired today; `DEVICE=cpu` prints a pointer
to this note and stops. This explains why, and what enabling it needs.

## What already works on the CPU shim

The *host orchestration* runs end to end on the CPU device stand-in
(`runtime/cpu_device_shim.c`): `DEVALLOC` → `pas_dev_alloc`, the copies →
`memcpy`, `LAUNCH` → the three-step `pas_dev_module_load` / `_get_function` /
`pas_dev_launch` path, `DEVFREE` → `free`. Both example host programs compile,
link against the CPU shim, and run to completion. The plumbing is sound.

## What's missing: kernel coverage

The CPU device runs a kernel as a **single-thread grid**. `pas_dev_launch` calls
the kernel's dispatch thunk exactly once, and on the CPU device the thread-index
intrinsics lower to constants: `THREADIDX_*`/`BLOCKIDX_*` to 0 and
`BLOCKDIM_*`/`GRIDDIM_*` to 1. So a kernel that maps one thread to one output
element computes only element 0.

Both example kernels are one-thread-per-element:

- `fill_indices` writes `outp[i] := i` for the single global index `i`, so on the
  CPU device only `outp[0]` is written. (Observed: the buffer comes back as
  `0` followed by the seeded sentinels.)
- `mandelbrot_f32`/`mandelbrot_f64` map one thread to pixel `(px, py)`, so on the
  CPU device only pixel `(0, 0)` is computed; the rest of the image is whatever
  the device buffer happened to hold.

This is a property of the kernels, not the orchestration or the shim.

## How it was fixed (implemented)

Rather than changing the kernels, the CPU shim was made to actually emulate GPU
execution:

1. **Compiler (`codegen/exprs.py`)**: on the CPU triple, `THREADIDX_*`,
   `BLOCKIDX_*`, `BLOCKDIM_*`, `GRIDDIM_*` now lower to **loads from
   thread-local globals** (`__pas_tid_x`, `__pas_ctaid_x`, etc.) instead of
   baked-in constants. The runtime defines these.

2. **CPU shim (`runtime/cpu_device_shim.c`)**: `pas_dev_launch` now loops over
   the full launch geometry (`gx*gy*gz` blocks × `bx*by*bz` threads), setting
   the TLS index registers before each thunk call. `BLOCKDIM_*`/`GRIDDIM_*`
   default to 1 so direct (non-LAUNCH) kernel calls still work.

3. **Makefile (`device-example.mk`)**: the `DEVICE=cpu` stub now builds and
   links `dev.ll` + `host.ll` against `libpascalrt_cpu.a`.

The kernels are unchanged. `make DEVICE=cpu run` now produces correct output for
both `fill_indices` (all 256 indices correct) and `mandelbrot` (full image).

## What was previously needed (now moot): grid-stride kernels

Make each kernel iterate its whole index space with a grid-stride loop instead of
handling a single element. For a 1-D kernel:

```
i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
stride := BLOCKDIM_X * GRIDDIM_X;
WHILE i < n DO BEGIN { body for element i } ; i := i + stride END
```

and the 2-D analog (an outer `py` loop and inner `px` loop, each strided) for
Mandelbrot. This change is:

- **ABI-preserving.** Entry names and parameters are unchanged, so the emitted
  PTX stays a drop-in replacement for `mandelbrot.cu`, and under a
  one-thread-per-pixel GPU launch the result is identical (each thread's stride
  loop runs exactly once).
- **The established pattern.** The vector-add device unit used elsewhere in the
  tree is already grid-stride for exactly this reason — it runs correctly on both
  the CPU stand-in (one thread covers the whole buffer) and the GPU.

**This is a change to the device kernels and is deferred pending sign-off** — the
kernels are intentionally left untouched here.

## Turning CPU on, once the kernels are grid-stride

Replace the `DEVICE=cpu` stub in `examples/device_ptx/device-example.mk` with the
mirror of the cuda branch, minus PTX (the CPU shim never loads it) and minus
`-lcuda`:

```make
$(BUILD)/dev.ll: $(DEVICE_UNIT) | $(BUILD)
	$(PAS) $(FEATURES) $< $@

$(BUILD)/host.ll: $(HOST_SRC) | $(BUILD)
	$(PAS) $(FEATURES) $< $@

.PHONY: runtime-cpu
runtime-cpu:
	$(MAKE) -C $(RUNTIME) clean
	$(MAKE) -C $(RUNTIME) DEVICE_SHIM=cpu

$(EXE): $(BUILD)/host.ll $(BUILD)/dev.ll runtime-cpu
	clang $(BUILD)/host.ll $(BUILD)/dev.ll $(RUNTIME_LIB) -lm -o $@
```

The host Pascal and the host orchestration do not change between devices — that
is the whole point of the shim design. `make` (DEVICE=cpu) would then build and
run on any machine with no GPU.
