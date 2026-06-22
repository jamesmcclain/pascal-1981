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

# The super-array pointee `PIXELS = SUPER ARRAY [0..*] OF INTEGER32` lowers to
# `[100 x i32]` on x86 (the default 100-element size for an open super array),
# so the C harness declares the output parameter as `int (*)[100]`.
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
            ir = compile_to_llvm(
                ast, source_file=_IMPL, device_triple='x86_64-pc-linux-gnu')
            # CPU-device shape guards: no GPU artifacts leaked in.
            self.assertNotIn('ptx_kernel', ir)
            self.assertNotIn('addrspace', ir)
            # The followups.md item 2 changes are visible but harmless on x86:
            # the select peephole fires on the wd/hd guards, and device fp ops
            # carry the contract flag.
            self.assertIn('select', ir)
            self.assertIn('contract', ir)

            ir_path = os.path.join(tmpdir, 'mandelbrot.ll')
            with open(ir_path, 'w') as f:
                f.write(ir)

            # 2. C harness that calls both kernels directly.
            harness_path = os.path.join(tmpdir, 'harness.c')
            with open(harness_path, 'w') as f:
                f.write(_HARNESS_C)

            # 3. Link and run. No Pascal runtime needed: the kernel is
            #    self-contained (no host I/O, no externs on the CPU-device
            #    path).
            exe_path = os.path.join(tmpdir, 'mandelbrot_x86')
            link = subprocess.run(
                ['clang', ir_path, harness_path, '-o', exe_path],
                capture_output=True, text=True)
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
