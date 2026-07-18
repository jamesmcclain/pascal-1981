"""Tests for the gcc-style CLI on the compiler drivers.

Covers the `pascal1981` (compile_to_llvm) driver:
  • stage flags: -S (assembly), -c (object via clang), default (compile+link)
  • default output naming (a.out / <base>.ll / <base>.o / <base>.ptx, cwd)
  • -o FILE and the -o - stdout extension (with -S)
  • -O0..-O3 (bare -O = -O1): host -S pipeline, clang forwarding, PTX pipeline
  • -print-file-name=libpascalrt.a as the gcc-style runtime-path query
  • nvptx --device-triple gating (-S required; -c rejected; --target retired)
  • -### dry run and -l/-L/-Wl passthrough to the clang link step
  • -h/--help
and the compile_to_ptx driver's default output naming.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pascal1981 import runtime_lib_path
from pascal1981.compile_to_llvm import main as llvm_main
from pascal1981.compile_to_ptx import main as ptx_main

from tests.support import requires_exe, requires_llvm, temporary_pascal_project

_MINIMAL = "PROGRAM P;\nBEGIN\n  WRITELN('phase2 ok')\nEND.\n"

# Minimal DEVICE unit (same sources as tests/test_compile_to_ptx.py) for the
# PTX driver's default-output-name path.
_PTX_IFACE = """DEVICE INTERFACE;
UNIT FILL (fill_indices);
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
END;
"""

_PTX_IMPL = """(*$INCLUDE:'fill'*)
DEVICE IMPLEMENTATION OF FILL;
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    outp^[i] := i
END;
.
"""


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


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write_minimal(tmp):
    src = os.path.join(tmp, 'p.pas')
    with open(src, 'w') as f:
        f.write(_MINIMAL)
    return src


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
class TestDashS(unittest.TestCase):

    def test_dash_S_writes_ll_in_cwd_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, _ = _run_main(llvm_main, ['pascal1981', '-S', 'p.pas'])
                self.assertEqual(rc, 0)
                with open('p.ll') as f:
                    self.assertIn('target triple', f.read())

    def test_dash_S_o_dash_writes_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, out, _ = _run_main(llvm_main, ['pascal1981', '-S', '-o', '-', 'p.pas'])
                self.assertEqual(rc, 0)
                self.assertIn('target triple', out)
                self.assertFalse(os.path.exists('p.ll'))

    def test_dash_S_dash_o_names_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, _ = _run_main(llvm_main, ['pascal1981', '-S', 'p.pas', '-o', 'x.ll'])
                self.assertEqual(rc, 0)
                with open('x.ll') as f:
                    self.assertIn('target triple', f.read())


@requires_exe
class TestClangStages(unittest.TestCase):

    def test_default_invocation_links_a_out_and_it_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, err = _run_main(llvm_main, ['pascal1981', 'p.pas'])
                self.assertEqual(rc, 0, err)
                self.assertTrue(os.path.exists('a.out'))
                run = subprocess.run(['./a.out'], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0)
                self.assertIn('phase2 ok', run.stdout)

    def test_dash_o_names_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, err = _run_main(llvm_main, ['pascal1981', 'p.pas', '-o', 'myprog'])
                self.assertEqual(rc, 0, err)
                run = subprocess.run(['./myprog'], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0)
                self.assertIn('phase2 ok', run.stdout)

    def test_dash_c_writes_object_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, err = _run_main(llvm_main, ['pascal1981', '-c', 'p.pas'])
                self.assertEqual(rc, 0, err)
                with open('p.o', 'rb') as f:
                    self.assertEqual(f.read(4), b'\x7fELF')

    def test_verbose_echoes_clang_and_forwards_dash_O(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, err = _run_main(llvm_main, ['pascal1981', '-v', '-O2', 'p.pas'])
                self.assertEqual(rc, 0, err)
                self.assertIn('+ clang', err)
                self.assertIn('-O2', err)


@requires_llvm
class TestStageGates(unittest.TestCase):

    def test_dash_S_and_dash_c_are_mutually_exclusive(self):
        rc, _, err = _run_main(llvm_main, ['pascal1981', '-S', '-c', 'prog.pas'])
        self.assertEqual(rc, 2)
        self.assertIn('not allowed with argument', err)

    def test_o_dash_requires_dash_S(self):
        rc, _, err = _run_main(llvm_main, ['pascal1981', 'prog.pas', '-o', '-'])
        self.assertEqual(rc, 2)
        self.assertIn('only meaningful with -S', err)

    def test_target_flag_retired(self):
        rc, _, err = _run_main(llvm_main, ['pascal1981', 'prog.pas', '--target', 'ptx'])
        self.assertEqual(rc, 2)
        self.assertIn('unrecognized arguments', err)

    def test_nvptx_device_triple_requires_dash_S(self):
        rc, _, err = _run_main(llvm_main, ['pascal1981', 'prog.pas', '--device-triple', 'nvptx64-nvidia-cuda'])
        self.assertEqual(rc, 2)
        self.assertIn('use -S', err)

    def test_nvptx_device_triple_dash_c_rejected(self):
        rc, _, err = _run_main(llvm_main, ['pascal1981', '-c', 'prog.pas', '--device-triple', 'nvptx64-nvidia-cuda'])
        self.assertEqual(rc, 2)
        self.assertIn('ptxas', err)


@requires_llvm
class TestDashOptLevel(unittest.TestCase):

    def test_dash_O_level_is_validated(self):
        rc, _, err = _run_main(llvm_main, ['pascal1981', '-S', 'prog.pas', '-O9'])
        self.assertEqual(rc, 2)
        self.assertIn('invalid choice', err)

    def test_bare_dash_O_means_O1(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp), \
                    mock.patch('pascal1981.compile_to_llvm._optimize_ir_text',
                               side_effect=lambda ir, level: ir) as opt:
                rc, _, _ = _run_main(llvm_main, ['pascal1981', '-S', '-o', '-', 'p.pas', '-O'])
                self.assertEqual(rc, 0)
                self.assertEqual(opt.call_args[0][1], 1)

    def test_dash_O2_runs_host_pipeline_with_dash_S(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, out, err = _run_main(llvm_main, ['pascal1981', '-S', '-O2', '-o', '-', 'p.pas'])
                self.assertEqual(rc, 0, err)
                self.assertIn('define', out)


@requires_llvm
class TestHelp(unittest.TestCase):

    def test_help_long_and_short(self):
        for flag in ('-h', '--help'):
            with self.subTest(flag=flag):
                rc, out, _ = _run_main(llvm_main, ['pascal1981', flag])
                self.assertEqual(rc, 0)
                self.assertIn('usage:', out)
                for needle in ('-S', '-c', '-O', '-o', '-###', '-print-file-name'):
                    self.assertIn(needle, out)


@requires_llvm
class TestDryRun(unittest.TestCase):

    def test_triple_hash_prints_commands_without_executing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, err = _run_main(llvm_main, ['pascal1981', '-###', 'p.pas'])
                self.assertEqual(rc, 0, err)
                self.assertIn('+ clang', err)
                self.assertFalse(os.path.exists('a.out'))


@requires_llvm
class TestLinkFlagPassthrough(unittest.TestCase):

    def test_dash_l_L_Wl_forwarded_to_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, err = _run_main(llvm_main, ['pascal1981', '-###', 'p.pas',
                                                   '-L', '/x', '-lm', '-Wl,--foo'])
                self.assertEqual(rc, 0, err)
                self.assertIn('-L/x', err)
                self.assertIn('-lm', err)
                self.assertIn('-Wl,--foo', err)

    @requires_exe
    def test_dash_l_real_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_minimal(tmp)
            with _cwd(tmp):
                rc, _, err = _run_main(llvm_main, ['pascal1981', 'p.pas', '-l', 'm'])
                self.assertEqual(rc, 0, err)
                run = subprocess.run(['./a.out'], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0)
                self.assertIn('phase2 ok', run.stdout)


@requires_llvm
class TestNvptxDeviceTriple(unittest.TestCase):

    def test_nvptx_device_triple_dash_S_emits_ptx(self):
        with temporary_pascal_project({'fill': _PTX_IFACE, 'fill.pas': _PTX_IMPL}) as project_dir:
            with _cwd(project_dir):
                rc, _, err = _run_main(llvm_main, ['pascal1981', '-S', 'fill.pas',
                                                   '--device-triple', 'nvptx64-nvidia-cuda'])
                self.assertEqual(rc, 0, err)
                with open('fill.ptx') as f:
                    self.assertIn('.visible .entry fill_indices', f.read())


@requires_llvm
class TestPtxDriver(unittest.TestCase):

    def test_ptx_driver_default_output_name(self):
        with temporary_pascal_project({'fill': _PTX_IFACE, 'fill.pas': _PTX_IMPL}) as project_dir:
            with _cwd(project_dir):
                rc, _, err = _run_main(ptx_main, ['pascal1981.compile_to_ptx', 'fill.pas'])
                self.assertEqual(rc, 0, err)
                with open('fill.ptx') as f:
                    self.assertIn('.visible .entry fill_indices', f.read())


if __name__ == '__main__':
    unittest.main()
