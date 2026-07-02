"""End-to-end Pascal DEVICE source -> PTX artifact smoke test."""

import os
import subprocess
import sys
import unittest

from tests.support import requires_llvm


@requires_llvm
class TestDevicePtxArtifactIntegration(unittest.TestCase):

    def test_fill_indices_example_emits_inspectable_ptx(self):
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        example_dir = os.path.join(repo, 'examples', 'device_ptx', 'fill_indices')
        ptx_path = os.path.join(example_dir, 'fill.test.ptx')
        ll_path = os.path.join(example_dir, 'fill.test.ll')
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'pascal1981.compile_to_ptx',
                    'fill.pas',
                    ptx_path,
                    '--emit-llvm',
                    ll_path,
                    '--cpu',
                    'sm_70',
                ],
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
            self.assertIn('.visible .entry fill_indices', ptx)
            self.assertIn('%tid.x', ptx)
            self.assertIn('%ctaid.x', ptx)
            self.assertIn('%ntid.x', ptx)
            self.assertRegex(ptx, r'st\.global\.[ub]32', 'expected a global 32-bit store to the buffer (size- or bit-typed spelling)')
        finally:
            for path in (ptx_path, ll_path):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


if __name__ == '__main__':
    unittest.main()
