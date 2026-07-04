"""x86 CPU-device sanity check for the real Mandelbrot kernel.

DEVICE code that happens to target the host triple (x86_64 CPU-device) must
still compile, link, and run: address spaces collapse to 0, thread/block index
reads fold to constants (one thread, one block), and the kernel body executes
as an ordinary host function. This pins that the followups.md item 2 changes
(predication `select`, FMA `contract`, pointer alignment) do not break the
CPU-device path:

* the `.align N` hint is GPU-gated off (``_apply_kernel_entry`` only fires on a
  GPU triple), so x86 params stay unannotated;
* the IF/ELSE-of-assignment `select` peephole fires harmlessly (it is not
  triple-gated) and the x86 backend accepts it;
* the device `contract` fast-math flag is emitted (it is device-gated, not
  GPU-gated) and the x86 backend accepts it without changing the kernel's
  observable integer result.

This drives the *actual* ``examples/device_ptx/mandelbrot/mandelbrot.pas``
(not a copy): it is compiled to x86 IR and linked with a tiny C harness that
calls ``mandelbrot_f32`` / ``mandelbrot_f64`` directly. The example source and
interface files are not modified.

On x86 the kernel computes only pixel (0,0): THREADIDX_*/BLOCKIDX_* fold to 0,
so px=py=0. With x_min=y_min=0 the corner maps to c=0 (inside the set), so the
escape loop runs to max_iter and output[0] == max_iter.

Host-to-device ABI assumptions (device triple == host triple)
-------------------------------------------------------------
This test exercises the case where the device triple IS the host triple, so
"device code" lowers to ordinary host functions callable from C. The C harness
below hard-codes the resulting x86_64 SysV ABI shape of the two kernels. These
are assumptions about how the core compiler lowers a DEVICE UNIT when
device == host, not about the GPU path:

* **Super-array pointee width.** `PIXELS = SUPER ARRAY [0..*] OF INTEGER32`
  lowers to `[100 x i32]` on x86 (the default 100-element size for an open
  super array), so the output parameter is `[100 x i32]*` and the C harness
  declares it `int (*)[100]`. On NVPTX the same type lowers to a bare `i32*`
  device pointer; the two ABIs are deliberately different.
* **Return type.** Device routines keep the vintage `i32`-returning shape on
  x86 because the kernel-entry void return (and `ptx_kernel` calling
  convention) is GPU-gated off via ``_is_kernel_entry`` / ``_apply_kernel_entry``
  (they require ``_is_gpu_triple(device_triple)``). So the C harness declares
  both kernels `int (...)`, not `void (...)`.
* **Linkage / name mangling.** The routines are external-linkage and callable
  by their source name from C (no C++ mangling); the test relies on the
  symbols `mandelbrot_f32` / `mandelbrot_f64` being present verbatim.
* **No GPU parameter attributes.** The `.align N` pointer-param hint added by
  the item 2 alignment work is GPU-gated, so it does NOT appear on x86 and
  cannot interfere with C interop (a foreign param attribute could make the
  routine uncallable from C).
* **Thread/block index folding.** On x86, `codegen_device_index_builtin`
  folds THREADIDX_*/BLOCKIDX_* to 0 and BLOCKDIM_*/GRIDDIM_* to 1 (one-thread,
  one-block grid), so the kernel computes exactly one pixel.
* **`contract` is device-gated, not GPU-gated.** The item 2 FMA work sets the
  LLVM `contract` fast-math flag whenever ``is_device_module`` is true,
  regardless of triple, so x86 CPU-device fp ops DO carry `contract`. This is
  a semantic (numerics) decision, not an ABI one: the x86 backend accepts the
  flag and the kernel's observable integer result is unchanged. The test
  asserts `contract` is present precisely to pin this device==host behavior.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import requires_exe

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_EXAMPLE_DIR = os.path.join(_REPO, 'examples', 'device_ptx', 'mandelbrot')
_IMPL = os.path.join(_EXAMPLE_DIR, 'mandelbrot.pas')

# C harness for the device == host case. The signatures hard-code the x86_64
# SysV ABI the core compiler emits for a DEVICE UNIT when the device triple is
# the host triple (see the module docstring's "Host-to-device ABI assumptions"
# block for the reasoning):
#   * output is `int (*)[100]`  -- SUPER ARRAY [0..*] OF INTEGER32 -> [100 x i32]
#     on x86 (open super-array default size 100; on NVPTX this would be a bare
#     `int*` device pointer -- a deliberately different ABI);
#   * both kernels return `int` -- the vintage i32 shape, because the
#     kernel-entry void return + ptx_kernel CC is GPU-gated off on x86;
#   * the routines are external-linkage and C-callable by their source names.
_HARNESS_C = r"""
#include <stdio.h>
extern int mandelbrot_f32(int (*output)[100], int width, int height, int max_iter,
                          float xmin, float xmax, float ymin, float ymax);
extern int mandelbrot_f64(int (*output)[100], int width, int height, int max_iter,
                          double xmin, double xmax, double ymin, double ymax);
int main(void) {
    int buf[100] = {0};
    mandelbrot_f32(&buf, 2, 2, 10, 0.0f, 0.5f, 0.0f, 0.5f);
    printf("%d\n", buf[0]);
    for (int i = 0; i < 100; i++) buf[i] = 0;
    mandelbrot_f64(&buf, 2, 2, 10, 0.0, 0.5, 0.0, 0.5);
    printf("%d\n", buf[0]);
    return 0;
}
"""


@requires_exe
class TestMandelbrotX86CpuDevice(unittest.TestCase):
    """The real Mandelbrot kernel, lowered to the host triple, runs on x86."""

    def test_mandelbrot_runs_on_x86_cpu_device(self):
        tmpdir = tempfile.mkdtemp()
        try:
            # 1. Compile the actual example DEVICE UNIT to x86 IR (host triple
            #    as the device triple). No GPU, no nvcc, no PTX.
            ast = parse_file(_IMPL)
            result = PascalTypeChecker(source_file=_IMPL).check(ast)
            self.assertTrue(result.success, msg=result.errors)
            ir = compile_to_llvm(ast, source_file=_IMPL, device_triple='x86_64-pc-linux-gnu')
            # CPU-device shape guards: no GPU artifacts leaked in. These are
            # also device==host ABI guards: `ptx_kernel` would change the
            # calling convention (making the routine uncallable from C), and
            # `addrspace` would change the pointer parameter type. Both are
            # GPU-gated, so on x86 the kernel stays a plain C-callable function.
            self.assertNotIn('ptx_kernel', ir)
            self.assertNotIn('addrspace', ir)
            # The followups.md item 2 changes are visible but harmless on x86.
            # `select` is the wd/hd guard peephole (not triple-gated; the x86
            # backend accepts it). `contract` is present because the FMA flag
            # is device-gated (is_device_module), NOT GPU-gated -- so x86
            # CPU-device fp ops carry it too. That is a semantic decision, not
            # an ABI one: it does not change the kernel's calling convention or
            # parameter layout, only its float intermediates, and the integer
            # result is unchanged. Pinning it here guards the device==host
            # numerics contract.
            self.assertIn('select', ir)
            self.assertIn('contract', ir)

            ir_path = os.path.join(tmpdir, 'mandelbrot.ll')
            with open(ir_path, 'w') as f:
                f.write(ir)

            # 2. C harness that calls both kernels directly.
            harness_path = os.path.join(tmpdir, 'harness.c')
            with open(harness_path, 'w') as f:
                f.write(_HARNESS_C)

            # 3. Link and run. The kernel IR now references the thread-local
            #    index globals (__pas_tid_x etc.) defined in cpu_device_shim.c,
            #    so link that in too (no other Pascal runtime needed).
            shim_path = os.path.join(os.path.dirname(__file__), '..', '..', 'runtime', 'cpu_device_shim.c')
            exe_path = os.path.join(tmpdir, 'mandelbrot_x86')
            link = subprocess.run(['clang', ir_path, harness_path, shim_path, '-o', exe_path], capture_output=True, text=True)
            self.assertEqual(link.returncode, 0, msg=link.stderr)

            run = subprocess.run([exe_path], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            lines = [line.strip() for line in run.stdout.splitlines() if line.strip()]
            # Pixel (0,0) -> c=0 (inside the set) -> escape loop runs to
            # max_iter=10 for both the f32 and f64 kernels.
            self.assertEqual(lines, ['10', '10'], msg=run.stderr)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
