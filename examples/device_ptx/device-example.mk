# Shared build rules for the examples/device_ptx/* examples.
#
# Each example builds a Pascal DEVICE UNIT (the kernel) plus a host PROGRAM (the
# orchestration: allocate / copy / launch / copy back, all written in Pascal) and
# runs the kernel through the device-orchestration runtime shim selected by the
# DEVICE variable. The host Pascal is identical for both devices; only the build
# differs -- which is the whole point of the shim design.
#
#   make                 # DEVICE=cpu  (CPU stand-in -- see CPU_DEVICE_TODO.md)
#   make DEVICE=cuda      # real GPU via the CUDA Driver API shim + embedded PTX
#   make run [DEVICE=...] # build, then run
#   make clean
#
# The including Makefile sets: DEVICE_UNIT, HOST_SRC, EXE, FEATURES
# (and may override SM or CUDA_HOME).

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
	$(PAS) --target ptx $< $@ --sm $(SM) $(FEATURES)

# Objectify the PTX into a single data symbol the host links against.  This is a
# data blob (PTX *text* + a trailing NUL for the shim's C-string read), NOT
# ptxas/cubin output -- hence the _blob.o name, never .ptx.o.
$(BUILD)/dev_ptx_blob.s: $(BUILD)/dev.ptx | $(BUILD)
	printf '\t.section .rodata\n\t.globl __pas_device_ptx\n__pas_device_ptx:\n\t.incbin "$(BUILD)/dev.ptx"\n\t.byte 0\n' > $@

$(BUILD)/dev_ptx_blob.o: $(BUILD)/dev_ptx_blob.s
	clang -c $< -o $@

$(BUILD)/host.ll: $(HOST_SRC) | $(BUILD)
	$(PAS) $(FEATURES) --device-backend cuda $< $@

$(EXE): $(BUILD)/host.ll $(BUILD)/dev_ptx_blob.o
	clang $(BUILD)/host.ll $(BUILD)/dev_ptx_blob.o $(RUNTIME_CUDA) \
	      -L$(CUDA_HOME)/lib64/stubs -lcuda -o $@

else ifeq ($(DEVICE),cpu)
# ---- CPU device: FUTURE WORK (see CPU_DEVICE_TODO.md) -----------------------
# The host orchestration already works on the CPU shim; what's missing is kernel
# coverage. The CPU device runs a single-thread grid, so a one-thread-per-element
# kernel computes only element 0. Enabling this is a kernel change, deferred.
$(EXE):
	@echo "DEVICE=cpu is not yet wired for this example."                    >&2
	@echo "See examples/device_ptx/CPU_DEVICE_TODO.md for why and what it"   >&2
	@echo "needs. For now, build and run on a GPU with:  make DEVICE=cuda"   >&2
	@false

else
$(error DEVICE must be 'cpu' or 'cuda', got '$(DEVICE)')
endif
