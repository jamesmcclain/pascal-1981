# Makefile for the Pascal-1981 C runtime static library.
#
# Usage:
#   make            Build libpascalrt.a in build/
#   make clean      Remove build artifacts

CC      := clang
CFLAGS  := -c -O2 -Wall -Wextra
AR      := ar
ARFLAGS := rcs

SRCDIR  := .
BUILDDIR:= build
TARGET  := $(BUILDDIR)/libpascalrt.a

# Device-orchestration shim selector: cpu (default, CPU stand-in, no GPU) or
# cuda (real CUDA Driver API).  Both shims define the same pas_dev_* symbols, so
# exactly one must be in the archive.  The cuda shim needs the CUDA headers to
# compile; consumers linking the cuda archive must add -lcuda on their final
# link line (or use the scripts/build-cuda-host.sh recipe).
DEVICE_SHIM ?= cpu

ifeq ($(DEVICE_SHIM),cuda)
SHIM_EXCLUDE := cpu_device_shim.c
CUDA_HOME    ?= /usr/local/cuda
CFLAGS       += -I$(CUDA_HOME)/include
else ifeq ($(DEVICE_SHIM),cpu)
SHIM_EXCLUDE := cuda_launch.c
else
$(error DEVICE_SHIM must be 'cpu' or 'cuda', got '$(DEVICE_SHIM)')
endif

# Every .c file in this directory is part of the runtime, except the shim that
# was not selected.
SRCS    := $(filter-out $(SRCDIR)/$(SHIM_EXCLUDE),$(wildcard $(SRCDIR)/*.c))
OBJS    := $(patsubst $(SRCDIR)/%.c,$(BUILDDIR)/%.o,$(SRCS))

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS) | $(BUILDDIR)
	$(AR) $(ARFLAGS) $@ $^

$(BUILDDIR)/%.o: $(SRCDIR)/%.c pascalrt.h | $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ $<

$(BUILDDIR):
	mkdir -p $(BUILDDIR)

clean:
	rm -rf $(BUILDDIR)

cleaner: clean
	rm -f $(TARGET)
