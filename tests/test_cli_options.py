"""Tests for the gcc-style CLI spellings on the compiler drivers.

Covers the `pascal1981` (compile_to_llvm) driver:
  • -o/--output names the output file; without it output goes to stdout
  • -O0..-O3 optimization spelling; a bare -O means -O1
  • -print-file-name=libpascalrt.a as the gcc-style runtime-path query
The --save-llvm / -o spellings on the PTX paths are exercised end-to-end by
tests/test_compile_to_ptx.py and tests/integration/test_device_ptx_*.py.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

from pascal1981 import runtime_lib_path
from pascal1981.compile_to_llvm import main as llvm_main

from tests.support import requires_llvm


def _run_main(main, argv):
    """Invoke an argparse-based driver main(); return (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    with mock.patch.object(sys, 'argv', argv), \
            contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        try:
            rc = main()
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


@requires_llvm
class TestPrintFileName(unittest.TestCase):

    def test_print_file_name_libpascalrt_prints_runtime_path(self):
        rc, out, _ = _run_main(llvm_main, ['pascal1981', '-print-file-name=libpascalrt.a'])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), runtime_lib_path())

    def test_print_file_name_unknown_library_echoes_input_like_gcc(self):
        rc, out, _ = _run_main(llvm_main, ['pascal1981', '-print-file-name=libbogus.a'])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), 'libbogus.a')


@requires_llvm
class TestDashO(unittest.TestCase):

    def test_dash_o_writes_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'p.pas')
            with open(src, 'w') as f:
                f.write('PROGRAM P;\nBEGIN\nEND.\n')
            out_path = os.path.join(tmp, 'p.ll')
            rc, _, _ = _run_main(llvm_main, ['pascal1981', src, '-o', out_path])
            self.assertEqual(rc, 0)
            with open(out_path) as f:
                self.assertIn('target triple', f.read())

    def test_without_dash_o_writes_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'p.pas')
            with open(src, 'w') as f:
                f.write('PROGRAM P;\nBEGIN\nEND.\n')
            rc, out, _ = _run_main(llvm_main, ['pascal1981', src])
            self.assertEqual(rc, 0)
            self.assertIn('target triple', out)


@requires_llvm
class TestDashOptLevel(unittest.TestCase):

    def _argv(self, *flags):
        return ['pascal1981', 'prog.pas', *flags]

    def test_bare_dash_O_means_O1_and_is_rejected_with_target_host(self):
        # A bare -O parses as level 1; were it 0 the --target host guard below
        # would not fire, so reaching this error proves the const=1 parse.
        rc, _, err = _run_main(llvm_main, self._argv('-O'))
        self.assertEqual(rc, 2)
        self.assertIn('-O is only meaningful with --target ptx', err)

    def test_dash_O_level_is_validated(self):
        rc, _, err = _run_main(llvm_main, self._argv('-O9'))
        self.assertEqual(rc, 2)
        self.assertIn('invalid choice', err)

    def test_dash_O2_rejected_with_target_host(self):
        rc, _, err = _run_main(llvm_main, self._argv('-O2'))
        self.assertEqual(rc, 2)
        self.assertIn('-O is only meaningful with --target ptx', err)


if __name__ == '__main__':
    unittest.main()
