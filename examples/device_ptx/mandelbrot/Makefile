# mandelbrot: 2-D escape-time kernels (f32/f64) launched from Pascal; the host
# renders one fixed view via a fixed palette into an in-memory image.
# See ../device-example.mk for the build rules and the DEVICE switch.
#
# SM defaults to sm_86 to mirror mandelbrot.cu's `nvcc -arch=sm_86`, keeping the
# emitted PTX a drop-in match for the CUDA build.

DEVICE_UNIT := mandelbrot.pas
HOST_SRC    := mandelbrot_host.pas
EXE         := mandelbrot_host
FEATURES    := -f wide-integers
SM          ?= sm_86

include ../device-example.mk
