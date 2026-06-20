# fill_indices PTX artifact example

This example compiles a Pascal `DEVICE UNIT` to inspectable NVPTX assembly. It
requires LLVM/llvmlite with the NVPTX backend, but does not require a CUDA
runtime or an NVIDIA device.

```bash
cd examples/device_ptx/fill_indices
PYTHONPATH=../../../src python3 -m pascal1981.compile_to_ptx fill.pas fill.ptx --emit-llvm fill.ll --cpu sm_70
grep '\.visible .entry fill_indices' fill.ptx
grep 'st.global.u32' fill.ptx
```

`fill_indices` is exported by the device interface, so it lowers to a PTX kernel
entry.  The body computes a one-dimensional global thread index and stores it to
a caller-provided `ADS(GLOBAL)` output buffer.
