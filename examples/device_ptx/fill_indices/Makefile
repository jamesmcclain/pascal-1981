# fill_indices: a 1-D kernel that writes outp[i] := i, launched from Pascal.
# See ../device-example.mk for the build rules and the DEVICE switch.

DEVICE_UNIT := fill.pas
HOST_SRC    := fill_host.pas
EXE         := fill_host
FEATURES    := -f wide-integers

include ../device-example.mk
