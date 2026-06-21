"""ADS memory-spaces: per-module triple + addrspace lowering.

Scope: a DEVICE MODULE lowers against the device triple and lowers ADS(s) OF T
to a typed addrspace(k) pointer; host/plain-module output is byte-identical.
The ADS-value / residence-storage / coerce_arg rewrite is deferred.
"""
import re
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


def _addrspaces(ir):
    return sorted(set(int(n) for n in re.findall(r'addrspace\((\d+)\)', ir)))


class TestDeviceTriple(unittest.TestCase):
    def test_device_module_uses_device_triple(self):
        ir = _compile("DEVICE MODULE M; VAR g: ADS(GLOBAL) OF INTEGER; .",
                      device_triple='nvptx64-nvidia-cuda')
        self.assertIn('target triple = "nvptx64-nvidia-cuda"', ir)

    def test_plain_module_keeps_host_triple(self):
        ir = _compile("MODULE M; VAR x: INTEGER; .")
        self.assertIn('target triple = "x86_64-pc-linux-gnu"', ir)

    def test_host_triple_override_drives_host_units(self):
        ir = _compile("PROGRAM P; VAR x: INTEGER; BEGIN x := 1 END.",
                      host_triple='aarch64-unknown-linux-gnu')
        self.assertIn('target triple = "aarch64-unknown-linux-gnu"', ir)

    def test_host_and_device_triples_are_independent(self):
        # A DEVICE MODULE uses the device triple regardless of the host triple.
        ir = _compile("DEVICE MODULE M; VAR g: ADS(GLOBAL) OF INTEGER; .",
                      host_triple='aarch64-unknown-linux-gnu',
                      device_triple='nvptx64-nvidia-cuda')
        self.assertIn('target triple = "nvptx64-nvidia-cuda"', ir)


class TestAddrspaceLowering(unittest.TestCase):
    def test_space_table_on_gpu_triple(self):
        # GLOBAL=1, SHARED=3, CONSTANT=4, LOCAL=5 (design S3.2).
        ir = _compile(
            "DEVICE MODULE M; VAR g: ADS(GLOBAL) OF INTEGER; s: ADS(SHARED) OF INTEGER;"
            " c: ADS(CONSTANT) OF INTEGER; l: ADS(LOCAL) OF INTEGER; .",
            device_triple='nvptx64-nvidia-cuda')
        self.assertEqual(_addrspaces(ir), [1, 3, 4, 5])

    def test_x86_device_collapses_to_addrspace_zero(self):
        ir = _compile("DEVICE MODULE M; VAR g: ADS(GLOBAL) OF INTEGER; .")
        self.assertEqual(_addrspaces(ir), [])  # addrspace 0 is implicit/unprinted


class TestHostUnchanged(unittest.TestCase):
    def test_host_program_has_no_addrspaces(self):
        ir = _compile("PROGRAM P; VAR x: INTEGER; BEGIN x := 1 END.")
        self.assertEqual(_addrspaces(ir), [])

    def test_host_ads_still_segmented_struct(self):
        # Outside a device module, ADS keeps the vintage {ptr, i16} pair.
        ir = _compile("PROGRAM P; VAR p: ADS OF INTEGER; BEGIN END.")
        self.assertIn('i16', ir)
        self.assertEqual(_addrspaces(ir), [])


if __name__ == '__main__':
    unittest.main()
