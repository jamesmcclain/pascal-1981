import unittest

from pascal1981.codegen import compile_to_llvm
from tests.support import parse_source, typecheck_source, requires_llvm


DEVICE_SRC = """
DEVICE MODULE M;
VAR x: INTEGER;
PROCEDURE go;
BEGIN
  x := 1;
  SYNCTHREADS;
  x := x + 1
END;
.
"""


class DeviceSyncthreadsTypecheckTests(unittest.TestCase):
    def test_host_code_rejects_syncthreads(self):
        result = typecheck_source("PROGRAM P; BEGIN SYNCTHREADS END.")
        self.assertFalse(result.success)
        self.assertTrue(any('SYNCTHREADS is only available in DEVICE code' in e.message for e in result.errors), result.errors)

    def test_device_code_accepts_syncthreads(self):
        result = typecheck_source(DEVICE_SRC)
        self.assertTrue(result.success, result.errors)

    def test_syncthreads_requires_no_arguments(self):
        result = typecheck_source("DEVICE MODULE M; PROCEDURE go; BEGIN SYNCTHREADS(1) END; .")
        self.assertFalse(result.success)
        self.assertTrue(any("Procedure 'SYNCTHREADS' expects 0 arguments" in e.message for e in result.errors), result.errors)


@requires_llvm
class DeviceSyncthreadsCodegenTests(unittest.TestCase):
    def _compile(self, src=DEVICE_SRC, device_triple='x86_64-pc-linux-gnu'):
        result = typecheck_source(src)
        self.assertTrue(result.success, result.errors)
        return compile_to_llvm(parse_source(src), device_triple=device_triple)

    def test_cpu_device_syncthreads_is_noop(self):
        ir = self._compile()
        self.assertNotIn('barrier', ir)
        self.assertIn('store i16 1', ir)
        self.assertIn('store i16 %', ir)

    def test_nvptx_syncthreads_lowers_to_barrier0(self):
        ir = self._compile(device_triple='nvptx64-nvidia-cuda')
        self.assertIn('llvm.nvvm.barrier0', ir)
        self.assertNotIn('declare void @abort', ir)
        self.assertNotIn('declare i32 @fflush', ir)

    def test_amdgpu_syncthreads_lowers_to_s_barrier(self):
        ir = self._compile(device_triple='amdgcn-amd-amdhsa')
        self.assertIn('llvm.amdgcn.s.barrier', ir)


if __name__ == '__main__':
    unittest.main()
