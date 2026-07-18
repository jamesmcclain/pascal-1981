# Shared build rules for the examples/device_ptx/* examples.
#
# Each example builds a Pascal DEVICE UNIT (the kernel) plus a host PROGRAM (the
# orchestration: allocate / copy / launch / copy back, all written in Pascal) and
# runs the kernel through the device-orchestration runtime shim selected by the
# DEVICE variable. The host Pascal is identical for both devices; only the build
# differs -- which is the whole point of the shim design.
#
#   make                 # DEVICE=cpu  (CPU stand-in -- emulates the full grid)
#   make DEVICE=cuda      # real GPU via the CUDA Driver API shim + embedded PTX
#   make run [DEVICE=...] # build, then run
#   make clean
#
# The including Makefile sets: DEVICE_UNIT, HOST_SRC, EXE, FEATURES
# (and may override SM or CUDA_HOME).
#
# On the CPU device the shim emulates a full GPU launch: pas_dev_launch loops
# over the whole gx*gy*gz x bx*by*bz geometry, setting thread-local index
# registers (__pas_tid_x etc.) before each kernel call. See runtime/cpu_device_shim.c.

DEVICE ?= cpu

# Repo paths, derived from this file's own location (examples/device_ptx/*.mk).
THIS_MK     := $(lastword $(MAKEFILE_LIST))
REPO        := $(abspath $(dir $(THIS_MK))/../..)
RUNTIME     := $(REPO)/runtime
RUNTIME_LIB := $(RUNTIME)/build/libpascalrt.a
RUNTIME_CUDA:= $(RUNTIME)/build/libpascalrt_cuda.a

PAS := PYTHONPATH=$(REPO)/src python3 -m pascal1981
PTX := PYTHONPATH=$(REPO)/src python3 -m pascal1981.compile_to_ptx

SM        ?= sm_70
CUDA_HOME ?= /usr/local/cuda
BUILD     := build

.PHONY: all run clean
all: $(EXE)
run: $(EXE)
	./$(EXE)
clean:
	rm -rf $(BUILD) $(EXE)

$(BUILD):
	mkdir -p $(BUILD)

ifeq ($(DEVICE),cuda)
# ---- real GPU: CUDA Driver API shim, three commands ------------------------
# The device kernel is compiled ONCE, to PTX (the real kernel).  The host is
# compiled with --device-backend cuda, so it emits no in-process launch thunk
# and no kernel-symbol reference -- there is no second 'dev.ll' device compile.
# The PTX text is packaged as its own object (a NUL-terminated __pas_device_ptx
# byte blob the host references as an external symbol); the CUDA shim
# cuModuleLoadData's it at run time.  Build the cuda runtime archive once with
#   make -C runtime cuda
# (this Makefile does not rebuild it on every example build).
$(BUILD)/dev.ptx: $(DEVICE_UNIT) | $(BUILD)
	$(PAS) --target ptx -S $< -o $@ --sm $(SM) -O2 $(FEATURES)

# Objectify the PTX into a single data symbol the host links against.  This is a
# data blob (PTX *text* + a trailing NUL for the shim's C-string read), NOT
# ptxas/cubin output -- hence the _blob.o name, never .ptx.o.
$(BUILD)/dev_ptx_blob.s: $(BUILD)/dev.ptx | $(BUILD)
	printf '\t.section .rodata\n\t.globl __pas_device_ptx\n__pas_device_ptx:\n\t.incbin "$(BUILD)/dev.ptx"\n\t.byte 0\n' > $@

$(BUILD)/dev_ptx_blob.o: $(BUILD)/dev_ptx_blob.s
	clang -c $< -o $@

$(BUILD)/host.ll: $(HOST_SRC) | $(BUILD)
	$(PAS) -S $(FEATURES) --device-backend cuda $< -o $@

$(EXE): $(BUILD)/host.ll $(BUILD)/dev_ptx_blob.o
	clang $(BUILD)/host.ll $(BUILD)/dev_ptx_blob.o $(RUNTIME_CUDA) \
	      -L$(CUDA_HOME)/lib64/stubs -lcuda -o $@

else ifeq ($(DEVICE),cpu)
# ---- CPU device: full-grid emulation via thread-local index registers -------
# The CPU shim now emulates a GPU launch: pas_dev_launch loops over the full
# gx*gy*gz x bx*by*bz grid, setting thread-local __pas_tid_*/  __pas_ctaid_*
# globals before each thunk call so the kernel sees the correct indices.
# The device unit compiles to the host triple (no PTX), and links alongside
# the host .ll against libpascalrt_cpu.a. No GPU or CUDA toolkit required.
#
# Build the cpu runtime archive once with:  make -C runtime
# (this Makefile does not rebuild it on every example build).
$(BUILD)/dev.ll: $(DEVICE_UNIT) | $(BUILD)
	$(PAS) -S $(FEATURES) $< -o $@

$(BUILD)/host.ll: $(HOST_SRC) | $(BUILD)
	$(PAS) -S $(FEATURES) $< -o $@

$(EXE): $(BUILD)/host.ll $(BUILD)/dev.ll
	clang $(BUILD)/host.ll $(BUILD)/dev.ll $(RUNTIME_LIB) -lm -o $@

else
$(error DEVICE must be 'cpu' or 'cuda', got '$(DEVICE)')
endif
