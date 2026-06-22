import unittest

from pascal1981.codegen import compile_to_llvm
from tests.support import parse_source, requires_llvm, typecheck_source

DEVICE_STRING_BOUNDS_SRC = """
DEVICE MODULE M;
VAR s: STRING(10); l: LSTRING(10); x: INTEGER;
PROCEDURE go;
BEGIN
  x := LOWER(s) + UPPER(s) + LOWER(l) + UPPER(l)
END;
.
"""


class DeviceStringBoundsTypecheckTests(unittest.TestCase):

    def test_device_code_accepts_string_lower_upper(self):
        result = typecheck_source(DEVICE_STRING_BOUNDS_SRC)
        self.assertTrue(result.success, [e.message for e in result.errors])


@requires_llvm
class DeviceStringBoundsCodegenTests(unittest.TestCase):

    def _compile(self, src=DEVICE_STRING_BOUNDS_SRC, device_triple='x86_64-pc-linux-gnu'):
        result = typecheck_source(src)
        self.assertTrue(result.success, [e.message for e in result.errors])
        return compile_to_llvm(parse_source(src), device_triple=device_triple)

    def test_cpu_device_string_bounds_are_constants(self):
        ir = self._compile()
        self.assertIn('add i32 1, 10', ir)
        self.assertIn('add i32 %".2", 0', ir)
        self.assertIn('add i32 %".3", 10', ir)
        self.assertIn('store i16 %".5", i16* @"x"', ir)

    def test_nvptx_string_bounds_emit_no_host_runtime(self):
        ir = self._compile(device_triple='nvptx64-nvidia-cuda')
        self.assertIn('target triple = "nvptx64-nvidia-cuda"', ir)
        self.assertIn('add i32 1, 10', ir)
        self.assertIn('add i32 %".2", 0', ir)
        self.assertIn('add i32 %".3", 10', ir)
        for leaked in ('abort', 'fflush', 'malloc', 'free', 'printf'):
            self.assertNotIn(leaked, ir)


if __name__ == '__main__':
    unittest.main()
