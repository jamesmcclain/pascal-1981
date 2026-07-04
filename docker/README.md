# Optional CUDA development image

A reference container for the **real-GPU path** (the CUDA Driver API shim,
`runtime/cuda_launch.c`). It is entirely optional: the default CPU-device path
needs only `llvmlite` + `clang` and no GPU. Use this image when you want to build
the CUDA shim and run kernels on an actual device.

## What it provides

- Base `nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04` — CUDA 12.8 headers, `ptxas`,
  and the stub `libcuda` for compiling/linking the shim. The real `libcuda` is
  supplied by the **host** NVIDIA driver at run time (`--gpus all`).
- `clang-20`, `curl`, `python3-venv`.
- A venv at `/opt/pascal-venv` (on `PATH`) with `pytest` and `llvmlite==0.47`.

## Requirements on the host

An NVIDIA GPU, the NVIDIA **kernel driver** installed on the host, and the
**NVIDIA Container Toolkit** so Docker can expose the device. The host driver must
support CUDA 12.8 (newer toolkit needs a newer-or-equal driver; `nvidia-smi` on
the host shows the maximum CUDA version it supports).

## Build

```sh
# from the repository root
docker build -t pascal-1981:latest -f docker/Dockerfile .
```

## Run (mounting the repo)

```sh
docker run --gpus all -it --rm -v "$PWD":/work pascal-1981:latest
```

`--gpus all` exposes the device and injects the host `libcuda`; `-v "$PWD":/work`
mounts the checkout at the image's `WORKDIR`.

## Verify the environment

Before building the shim, confirm the three things the GPU path depends on:

```sh
nvidia-smi                                   # device visible; CUDA <= host driver max
python3 - <<'PY'                             # NVPTX backend present in this llvmlite?
import llvmlite.binding as llvm
llvm.initialize_all_targets()
print(llvm.Target.from_triple("nvptx64-nvidia-cuda"))
PY
echo 'int main(){return 0;}' | clang-20 -x c - -L"${CUDA_HOME:-/usr/local/cuda}/lib64/stubs" -lcuda -o /tmp/lcuda && echo "libcuda links"
```

If the NVPTX line raises instead of printing a target, this llvmlite's LLVM was
built without the NVPTX backend — resolve that (a different `llvmlite`/`llvmdev`
build) before the device side can emit PTX. Everything else (the CPU suite) works
regardless.

## Use

```sh
# default CPU-device suite (no GPU needed)
make -C runtime
PYTHONPATH=src pytest tests/

# build the runtime with the CUDA shim, then run the gated GPU test for the
# end-to-end recipe (device unit -> PTX, host -> --device-backend cuda, link)
make -C runtime clean && make -C runtime DEVICE_SHIM=cuda

# the gated GPU test (skips automatically without a device)
PYTHONPATH=src pytest tests/integration/test_device_orchestration_gpu.py
```
