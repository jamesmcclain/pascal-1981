"""Item 7 (docs/followups.md): !range metadata on device index intrinsics,
end to end through PTX emission.

Verifies the metadata survives parse_assembly/verify/emit_assembly (so it is
not stripped or rejected by the backend) on both shipped examples, at
opt-level 0 and 2.

Anti-confabulation note: the original followup's suggested verification was
"PTX diff showing e.g. mul.wide.u32/dropped cvt instructions ... at O2". That
specific claim was tested empirically (both on fill_indices/mandelbrot and on
a minimal synthetic repro replicating the tid+ctaid*ntid indexing pattern
outside this codebase) and DID NOT hold on the LLVM 20.1.8 bundled with this
repo's pinned llvmlite==0.47.0: the PTX for both examples is byte-identical
with vs. without the !range metadata at --opt-level 2. This file therefore
asserts what was actually observed -- valid metadata that survives the whole
pipeline -- and does not assert the specific instruction-selection win, which
would be a false claim on this toolchain.
"""

import os
import subprocess
import sys
import unittest

from tests.support import requires_llvm


def _emit_llvm(example_dir, source, ll_path, *extra_args):
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    ptx_path = ll_path + '.ptx'
    result = subprocess.run(
        [sys.executable, '-m', 'pascal1981.compile_to_ptx', source, ptx_path, '--emit-llvm', ll_path, *extra_args],
        cwd=example_dir,
        env={
            **os.environ, 'PYTHONPATH': os.path.join(repo, 'src')
        },
        capture_output=True,
        text=True,
    )
    return result, ptx_path


@requires_llvm
class TestDeviceRangeMetadataPtx(unittest.TestCase):

    def _example_dir(self, *parts):
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        return os.path.join(repo, 'examples', 'device_ptx', *parts)

    def test_fill_indices_ir_carries_range_and_ptx_stays_valid_at_o0_and_o2(self):
        example_dir = self._example_dir('fill_indices')
        ll = os.path.join(example_dir, 'fill.range.ll')
        try:
            for opt_level in ('0', '2'):
                result, ptx_path = _emit_llvm(example_dir, 'fill.pas', ll, '--cpu', 'sm_70', '--opt-level', opt_level)
                self.assertEqual(result.returncode, 0, result.stderr)
                with open(ll) as f:
                    ir_text = f.read()
                self.assertIn('!range', ir_text)
                self.assertIn('!{ i32 0, i32 1024 }', ir_text)  # tid.x
                with open(ptx_path) as f:
                    ptx = f.read()
                self.assertIn('.visible .entry fill_indices', ptx)
                os.unlink(ptx_path)
        finally:
            try:
                os.unlink(ll)
            except FileNotFoundError:
                pass

    def test_mandelbrot_ptx_unaffected_by_range_metadata_at_o2(self):
        """Documents the actual (negative) empirical result: on this
        toolchain, !range on the sreg reads changes nothing observable in the
        emitted PTX for this kernel at --opt-level 2, even though the
        metadata is present and valid in the IR. See module docstring."""
        example_dir = self._example_dir('mandelbrot')
        ll = os.path.join(example_dir, 'mandelbrot.range.ll')
        try:
            result, ptx_path = _emit_llvm(example_dir, 'mandelbrot.pas', ll, '--cpu', 'sm_86', '--opt-level', '2')
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(ll) as f:
                ir_text = f.read()
            self.assertIn('!range', ir_text)
            with open(ptx_path) as f:
                ptx = f.read()
            self.assertIn('.visible .entry mandelbrot_f32', ptx)
            self.assertIn('.visible .entry mandelbrot_f64', ptx)
            os.unlink(ptx_path)
        finally:
            try:
                os.unlink(ll)
            except FileNotFoundError:
                pass


if __name__ == '__main__':
    unittest.main()
