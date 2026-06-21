# `fill_indices`: Pascal `DEVICE UNIT` → inspectable PTX

This example demonstrates the current device-only CUDA artifact path:

```text
Pascal DEVICE INTERFACE + DEVICE IMPLEMENTATION
  → Pascal type checker
  → NVPTX LLVM IR
  → LLVM NVPTX backend
  → PTX text assembly
```

It is intentionally **not** a full CUDA application.  The Pascal compiler emits
the device kernel artifact; an external launcher such as PyCUDA or a small CUDA
Driver API program is responsible for loading and running the `.ptx` on a machine
with an NVIDIA driver and device.

The important point: you can inspect the generated PTX on a development VM that
has no NVIDIA GPU and no CUDA runtime installed, as long as the Python/LLVM stack
has an NVPTX backend.

---

## Files

```text
fill.inc   # DEVICE INTERFACE; declares the exported kernel entry
fill.pas   # DEVICE IMPLEMENTATION OF FILL; contains the kernel body
README.md  # this document
RUNNING_PTX.md  # detailed external-launch test plan
```

The interface exports one procedure:

```pascal
DEVICE INTERFACE;
UNIT FILL (fill_indices);
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
END;
```

Because `fill_indices` appears in the unit export list, compiling the
implementation for NVPTX lowers it as a PTX kernel entry.

The implementation computes a one-dimensional CUDA-style global index and stores
that index into a caller-provided global output buffer:

```pascal
DEVICE IMPLEMENTATION OF FILL;

PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
VAR
  i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    outp^[i] := i
END;
.
```

The signature uses:

- `ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32` for a device/global pointer.
- `INTEGER32` for thread-index arithmetic.
- `THREADIDX_X`, `BLOCKIDX_X`, and `BLOCKDIM_X` for the parallel index.

The fixed Pascal array bound `[0..255]` is the current v0 spelling for a bounded
device buffer view.  Runtime launch code should pass `n <= 256` for this example.
Future dialect work may add a more natural open-buffer spelling.

---

## Build from a source checkout

From the repository root:

```bash
cd examples/device_ptx/fill_indices
PYTHONPATH=../../../src python3 -m pascal1981.compile_to_ptx \
  fill.pas \
  fill.ptx \
  --emit-llvm fill.ll \
  --cpu sm_70
```

Outputs:

```text
fill.ll   # intermediate LLVM IR
fill.ptx  # NVPTX assembly text
```

No Pascal runtime library is linked.  No host executable is produced.  This path
only emits the device artifact.

---

## Inspect the generated PTX

Basic checks:

```bash
grep '\.visible .entry fill_indices' fill.ptx
grep '%tid.x' fill.ptx
grep '%ctaid.x' fill.ptx
grep '%ntid.x' fill.ptx
grep 'st.global.u32' fill.ptx
```

Expected meaning:

- `.visible .entry fill_indices` — exported Pascal device-unit procedure became a
  loadable PTX kernel entry.
- `%tid.x` — `THREADIDX_X` was lowered to the NVPTX thread-index special register.
- `%ctaid.x` — `BLOCKIDX_X` was lowered to the NVPTX block-index special register.
- `%ntid.x` — `BLOCKDIM_X` was lowered to the NVPTX block-dimension special register.
- `st.global.u32` — the kernel stores a 32-bit integer into global memory.

You can also inspect the LLVM IR:

```bash
grep 'define ptx_kernel' fill.ll
grep 'llvm.nvvm.read.ptx.sreg' fill.ll
```

---

## Optional PTX validation with NVIDIA tools

If the NVIDIA CUDA toolkit is installed, validate with `ptxas`:

```bash
ptxas -arch=sm_70 -v -o fill.cubin fill.ptx
```

A successful `ptxas` run is stronger evidence than PTX text inspection: it means
NVIDIA's assembler accepted the generated PTX for that target architecture.
This repository's CI/dev VM does not require `ptxas`, so the checked tests stop at
LLVM PTX emission and text inspection.

---

## Running the PTX

Running requires a different environment:

- NVIDIA GPU
- NVIDIA driver
- either PyCUDA or a small CUDA Driver API launcher

See [`RUNNING_PTX.md`](RUNNING_PTX.md) for a detailed plan and launcher sketches.
The recommended first runtime proof is external to the Pascal compiler: load
`fill.ptx`, get the `fill_indices` symbol, allocate a 256-element `int32` device
buffer, launch one block of 256 threads, copy the buffer back, and check that it
contains `0, 1, 2, ..., 255`.

That proves the Pascal-generated PTX can run before we build Pascal host-side
CUDA orchestration.  One ghost at a time.
