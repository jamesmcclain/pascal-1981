import os
import subprocess
import sys
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.compile_to_ptx import compile_file_to_ptx, llvm_ir_to_ptx
from tests.support import (parse_source, requires_llvm, temporary_pascal_project, typecheck_module, typecheck_source)

_IFACE = """DEVICE INTERFACE;
UNIT FILL (fill_indices);
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
END;
"""

_IMPL = """(*$INCLUDE:'fill'*)
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


class DeviceInteger32IndexTests(unittest.TestCase):

    def test_device_code_accepts_integer32_array_index(self):
        result = typecheck_module(_IFACE, _IMPL, module_name='FILL')
        self.assertTrue(result.success, result.errors)

    def test_host_integer32_array_index_still_rejected(self):
        src = """
PROGRAM P;
VAR a: ARRAY [0..7] OF INTEGER32; i: INTEGER32;
BEGIN
  i := 0;
  a[i] := 1
END.
"""
        result = typecheck_source(src, features={'wide-integers': True})
        self.assertFalse(result.success)
        self.assertTrue(any('Array index must be INTEGER, got INTEGER32' in e.message for e in result.errors), result.errors)


@requires_llvm
class CompileToPtxTests(unittest.TestCase):

    def test_llvm_ir_to_ptx_emits_special_register_read(self):
        # Use a minimal module smoke for raw backend conversion.
        ir = compile_to_llvm(
            parse_source("DEVICE MODULE M; VAR [SPACE(GLOBAL)] x: INTEGER32; PROCEDURE go; BEGIN x := THREADIDX_X END; ."),
            device_triple='nvptx64-nvidia-cuda',
        )
        ptx = llvm_ir_to_ptx(ir, cpu='sm_70')
        self.assertIn('mov.u32', ptx)
        self.assertIn('%tid.x', ptx)

    def test_compile_file_to_ptx_emits_buffer_store_kernel(self):
        with temporary_pascal_project({'fill': _IFACE, 'fill.pas': _IMPL}) as project_dir:
            ptx = compile_file_to_ptx(os.path.join(project_dir, 'fill.pas'), cpu='sm_70')
        self.assertIn('.visible .entry fill_indices', ptx)
        self.assertIn('%tid.x', ptx)
        self.assertIn('%ctaid.x', ptx)
        self.assertIn('%ntid.x', ptx)
        self.assertRegex(ptx, r'st\.global\.[ub]32', 'expected a global 32-bit store to the buffer (size- or bit-typed spelling)')
        self.assertNotIn('abort', ptx)
        self.assertNotIn('fflush', ptx)

    def test_compile_to_ptx_cli_writes_ptx_and_optional_llvm(self):
        with temporary_pascal_project({'fill': _IFACE, 'fill.pas': _IMPL}) as project_dir:
            ptx_path = os.path.join(project_dir, 'fill.ptx')
            ll_path = os.path.join(project_dir, 'fill.ll')
            result = subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'pascal1981.compile_to_ptx',
                    os.path.join(project_dir, 'fill.pas'),
                    ptx_path,
                    '--emit-llvm',
                    ll_path,
                    '--cpu',
                    'sm_70',
                ],
                env={
                    **os.environ, 'PYTHONPATH': os.path.abspath('src')
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(ptx_path) as f:
                ptx = f.read()
            with open(ll_path) as f:
                ir = f.read()
        self.assertIn('.visible .entry fill_indices', ptx)
        self.assertIn('define ptx_kernel', ir)


if __name__ == '__main__':
    unittest.main()
