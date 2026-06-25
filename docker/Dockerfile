# Optional development image for the real-GPU (CUDA Driver API) path.
#
# This image is NOT needed for the default CPU-device path (the suite runs with
# nothing but llvmlite + clang). It exists for the @requires_gpu work: building
# the CUDA shim (runtime/cuda_launch.c) and running kernels on a device through
# the CUDA Driver API. See docker/README.md for build/run usage.
#
# Base: CUDA 12.8 *devel* image -> headers, ptxas, and the stub libcuda needed to
# COMPILE and LINK the shim. The real libcuda comes from the host NVIDIA driver
# at run time via `docker run --gpus all` (the driver is not containerizable).
FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

# Toolchain:
#   clang-20        - the runtime Makefile's CC for this image; compiles both the
#                     CPU shim and the CUDA shim against the CUDA 12.8 headers.
#   curl            - convenience (fetching tools, e.g. installing Claude Code).
#   python3-venv    - the compiler is Python; deps go in a venv because Ubuntu
#                     24.04 marks the system Python externally-managed (PEP 668).
RUN apt-get update \
 && apt-get install -y \
        clang-20 \
        curl \
        python3-venv \
	unzip \
 && rm -rf /var/lib/apt/lists/* \
 && ln /usr/bin/clang-20 /usr/bin/clang

# Isolated venv so pip can install without --break-system-packages. Putting its
# bin on PATH makes `python3`, `pip3`, and `pytest` resolve to the venv.
ENV VIRTUAL_ENV=/opt/pascal-venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# pytest to run the suite; llvmlite 0.47 pinned. NOTE: emitting device PTX needs
# this llvmlite's LLVM to have the NVPTX backend registered -- verify after build
# (see docker/README.md "Verify the environment"); if absent, that is the one
# thing to resolve before the GPU path can emit PTX.
RUN pip3 install pytest llvmlite==0.47

WORKDIR /work

# docker build -t pascal-1981:latest -f docker/Dockerfile .
