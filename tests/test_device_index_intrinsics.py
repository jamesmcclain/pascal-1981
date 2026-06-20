import unittest

from pascal1981.codegen import compile_to_llvm
from tests.support import parse_source, typecheck_source, requires_llvm


DEVICE_SRC = """
DEVICE MODULE M;
VAR x: INTEGER32;
PROCEDURE go;
BEGIN
  x := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X + GRIDDIM_X
END;
.
"""


class DeviceIndexIntrinsicTypecheckTests(unittest.TestCase):
    def test_normal_host_code_rejects_threadidx(self):
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := THREADIDX_X END.")
        self.assertFalse(result.success)
        self.assertTrue(any('THREADIDX_X is only available in DEVICE code' in e.message for e in result.errors), result.errors)

    def test_device_code_accepts_integer32_and_index_reads_without_feature_flag(self):
        result = typecheck_source(DEVICE_SRC)
        self.assertTrue(result.success, result.errors)

    def test_device_index_read_requires_no_arguments(self):
        result = typecheck_source("DEVICE MODULE M; VAR x: INTEGER32; PROCEDURE go; BEGIN x := THREADIDX_X(1) END; .")
        self.assertFalse(result.success)
        self.assertTrue(any("Function 'THREADIDX_X' expects 0 arguments" in e.message for e in result.errors), result.errors)


@requires_llvm
class DeviceIndexIntrinsicCodegenTests(unittest.TestCase):
    def _compile(self, src, device_triple='x86_64-pc-linux-gnu'):
        result = typecheck_source(src)
        self.assertTrue(result.success, result.errors)
        ast = parse_source(src)
        return compile_to_llvm(ast, device_triple=device_triple)

    def test_cpu_device_lowers_reads_to_one_thread_grid_constants(self):
        ir = self._compile(DEVICE_SRC)
        self.assertNotIn('llvm.nvvm.read.ptx.sreg', ir)
        self.assertIn('mul i32 0, 1', ir)
        self.assertIn('add i32 %".3", 1', ir)

    def test_nvptx_lowers_reads_to_special_register_intrinsics(self):
        ir = self._compile(DEVICE_SRC, device_triple='nvptx64-nvidia-cuda')
        for name in [
            'llvm.nvvm.read.ptx.sreg.tid.x',
            'llvm.nvvm.read.ptx.sreg.ctaid.x',
            'llvm.nvvm.read.ptx.sreg.ntid.x',
            'llvm.nvvm.read.ptx.sreg.nctaid.x',
        ]:
            self.assertIn(name, ir)
        self.assertNotIn('declare void @abort', ir)
        self.assertNotIn('declare i32 @fflush', ir)


if __name__ == '__main__':
    unittest.main()
