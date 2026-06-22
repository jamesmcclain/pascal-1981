"""Mandelbrot DEVICE source -> PTX drop-in substitution test.

This drives the `examples/device_ptx/mandelbrot` DEVICE UNIT through the PTX
artifact path and asserts that the emitted kernels are a drop-in match for the
CUDA kernels in the companion mandelbrot-gpu repository (`mandelbrot.cu`):

    extern "C" __global__ void mandelbrot_f32(
        int* output, int width, int height, int max_iter,
        float  x_min, float  x_max, float  y_min, float  y_max);
    extern "C" __global__ void mandelbrot_f64(
        int* output, int width, int height, int max_iter,
        double x_min, double x_max, double y_min, double y_max);

The substitution contract that must hold for PyCUDA's
`module_from_file(...).get_function("mandelbrot_f32"|"mandelbrot_f64")` plus a
positional `kernel(output, w, h, max_iter, xmin, xmax, ymin, ymax, ...)` launch:

* both kernels are launchable `.visible .entry` points (not `.func`);
* each returns void (no `func_retval` slot -- cuLaunchKernel has no return slot);
* parameter order/widths: a global pointer, three `.u32`, then four floats whose
  width is `.f32` for the f32 kernel and `.f64` for the f64 kernel;
* the f32 kernel computes in single precision (no `.f64` ops leak in);
* 2-D CUDA indexing and a 32-bit global store are present.

It does NOT launch on a GPU (no device here); that is the remaining
`@requires_gpu` rung of the validation ladder. Everything up to "valid PTX with
the right ABI" is checked.
"""

import os
import re
import subprocess
import sys
import unittest

from tests.support import requires_llvm


def _entry_block(ptx: str, name: str) -> str:
    """Return the text of one `.visible .entry NAME(...)` parameter+body block,
    from the entry line up to the start of the next entry (or end of file)."""
    start = ptx.index(f'.visible .entry {name}')
    nxt = ptx.find('.visible .entry ', start + 1)
    return ptx[start:] if nxt == -1 else ptx[start:nxt]


@requires_llvm
class TestDeviceMandelbrotPtxSubstitution(unittest.TestCase):

    def _emit(self, cpu='sm_86'):
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        example_dir = os.path.join(repo, 'examples', 'device_ptx', 'mandelbrot')
        ptx_path = os.path.join(example_dir, 'mandelbrot.test.ptx')
        ll_path = os.path.join(example_dir, 'mandelbrot.test.ll')
        result = subprocess.run(
            [sys.executable, '-m', 'pascal1981.compile_to_ptx', 'mandelbrot.pas', ptx_path, '--emit-llvm', ll_path, '--cpu', cpu],
            cwd=example_dir,
            env={
                **os.environ, 'PYTHONPATH': os.path.join(repo, 'src')
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(ptx_path) as f:
            ptx = f.read()
        with open(ll_path) as f:
            ll = f.read()
        for path in (ptx_path, ll_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        return ptx, ll

    def test_both_kernels_are_void_entries_with_cuda_matching_abi(self):
        ptx, ll = self._emit()

        # Both kernels are exported, void-returning PTX entries (IR proof).
        self.assertIn('define ptx_kernel void @"mandelbrot_f32"', ll)
        self.assertIn('define ptx_kernel void @"mandelbrot_f64"', ll)
        self.assertIn('.visible .entry mandelbrot_f32', ptx)
        self.assertIn('.visible .entry mandelbrot_f64', ptx)

        # No return slot anywhere: a kernel must not declare func_retval.
        self.assertNotIn('func_retval', ptx)

        f32 = _entry_block(ptx, 'mandelbrot_f32')
        f64 = _entry_block(ptx, 'mandelbrot_f64')

        # Parameter ABI, in CUDA order: output*, width, height, max_iter, 4 reals.
        for blk in (f32, f64):
            self.assertRegex(blk, r'\.param \.u64 \.ptr \.global[^\n]*_param_0')
            self.assertIn('_param_1', blk)
            self.assertIn('_param_2', blk)
            self.assertIn('_param_3', blk)
            # exactly 8 parameters
            self.assertEqual(len(re.findall(r'_param_\d+', blk.split(')')[0])), 8)

        # The real parameters are .f32 in the f32 kernel, .f64 in the f64 kernel.
        self.assertEqual(len(re.findall(r'\.param \.f32 ', f32.split(')')[0])), 4)
        self.assertEqual(len(re.findall(r'\.param \.f64 ', f64.split(')')[0])), 4)

        # f32 kernel is genuinely single precision: real arithmetic is .f32 and
        # no .f64 op leaks in.
        self.assertNotIn('.f64', f32)
        self.assertTrue(re.search(r'\b(mul|add|sub|div)\.rn\.f32\b', f32), 'expected single-precision arithmetic in mandelbrot_f32')

        # 2-D CUDA indexing and the 32-bit global store, in both kernels.
        for blk in (f32, f64):
            for sreg in ('%tid.x', '%tid.y', '%ctaid.x', '%ctaid.y', '%ntid.x', '%ntid.y'):
                self.assertIn(sreg, blk)
            self.assertIn('st.global.u32', blk)

        # followups.md item 2 (pointer alignment): the output pointer param is
        # `ADS(GLOBAL) OF INTEGER32`, so its natural alignment is 4 (the i32
        # element), not the backend's conservative `.align 1`. nvcc emits the
        # tighter hint; we now match.
        for blk in (f32, f64):
            self.assertRegex(blk, r'\.param \.u64 \.ptr \.global \.align 4 [^\n]*_param_0')
            self.assertNotRegex(blk, r'\.ptr \.global \.align 1', 'conservative .align 1 leaked onto a device pointer param')

        # followups.md item 2 (predication): the `IF width > 1 THEN wd := width
        # - 1 ELSE wd := 1` bounds guards lower to branchless `selp`, not a
        # divergent `bra` diamond. Two guards per kernel (wd, hd).
        for blk in (f32, f64):
            self.assertEqual(len(re.findall(r'selp', blk)), 2, 'expected two selp guards (wd, hd)')

    def test_no_phantom_input_output_externs(self):
        # followups.md item 2: a DEVICE compiland has no host I/O, so the
        # predeclared INPUT/OUTPUT host-stream globals must not appear in the
        # device artifact.  They used to leak in as two dead module-level
        # declarations -- the one purely cosmetic difference from the nvcc PTX.
        ptx, ll = self._emit()
        for name in ('input', 'output'):
            self.assertNotRegex(ptx, r'\.extern\s+\.global[^\n]*\b' + name + r'\b', f'phantom `.extern .global ... {name}` leaked into device PTX')
            self.assertNotRegex(ll, r'@"?' + name + r'"?\s*=\s*[^\n]*\bglobal\b', f'phantom @{name} host-stream global leaked into device IR')


if __name__ == '__main__':
    unittest.main()
