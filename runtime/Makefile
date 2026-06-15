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

# Every .c file in this directory is part of the runtime.
SRCS    := $(wildcard $(SRCDIR)/*.c)
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
