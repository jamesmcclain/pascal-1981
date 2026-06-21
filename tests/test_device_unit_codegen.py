"""DEVICE UNIT codegen parity tests."""

import os
import re
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


def _compile_module(iface_src, impl_src, module_name='U', **kw):
    tmpdir = tempfile.mkdtemp()
    try:
        iface_path = os.path.join(tmpdir, module_name.lower())
        impl_path = os.path.join(tmpdir, f'{module_name.lower()}.pas')
        with open(iface_path, 'w') as f:
            f.write(iface_src)
        with open(impl_path, 'w') as f:
            f.write(impl_src)
        ast = parse_file(impl_path)
        r = PascalTypeChecker(source_file=impl_path).check(ast)
        assert r.success, r.errors
        return compile_to_llvm(ast, source_file=impl_path, **kw)
    finally:
        shutil.rmtree(tmpdir)


def _addrspaces(ir_text):
    return sorted(set(int(n) for n in re.findall(r'addrspace\((\d+)\)', ir_text)))


class TestDeviceInterfaceCodegen(unittest.TestCase):

    def test_device_interface_uses_device_triple(self):
        ir = _compile(
            "DEVICE INTERFACE;\n"
            "UNIT U;\n"
            "VAR g: ADS(GLOBAL) OF INTEGER;\n"
            "END;\n",
            device_triple='nvptx64-nvidia-cuda',
        )
        self.assertIn('target triple = "nvptx64-nvidia-cuda"', ir)

    def test_plain_interface_keeps_host_triple(self):
        ir = _compile("INTERFACE;\n"
                      "UNIT U;\n"
                      "CONST k = 1;\n"
                      "END;\n")
        self.assertIn('target triple = "x86_64-pc-linux-gnu"', ir)

    def test_device_interface_lowers_addrspaces_on_gpu(self):
        ir = _compile(
            "DEVICE INTERFACE;\n"
            "UNIT U;\n"
            "VAR g: ADS(GLOBAL) OF INTEGER; s: ADS(SHARED) OF INTEGER;\n"
            "END;\n",
            device_triple='nvptx64-nvidia-cuda',
        )
        self.assertEqual(_addrspaces(ir), [1, 3])


class TestDeviceImplementationCodegen(unittest.TestCase):
    _IFACE = ("DEVICE INTERFACE;\n"
              "UNIT U (go);\n"
              "PROCEDURE go;\n"
              "END;\n")
    _IMPL = ("(*$INCLUDE:'u'*)\n"
             "DEVICE IMPLEMENTATION OF U;\n"
             "VAR\n"
             "  [SPACE(GLOBAL)] g: CHAR;\n"
             "  [SPACE(SHARED)] s: CHAR;\n"
             "PROCEDURE go;\n"
             "BEGIN\n"
             "  MOVESL(ADS g, ADS s, WRD(1));\n"
             "END;\n"
             ".\n")

    def test_device_implementation_uses_device_triple(self):
        ir = _compile_module(self._IFACE, self._IMPL, device_triple='nvptx64-nvidia-cuda')
        self.assertIn('target triple = "nvptx64-nvidia-cuda"', ir)

    def test_device_implementation_lowers_spaces_and_bridge_on_gpu(self):
        ir = _compile_module(self._IFACE, self._IMPL, device_triple='nvptx64-nvidia-cuda')
        self.assertIn('@"g" = addrspace(1) global i8 0', ir)
        self.assertIn('@"s" = addrspace(3) global i8 0', ir)
        body = ir.split('@"go"', 1)[1]
        self.assertRegex(body, r'load i8, i8 addrspace\(1\)\*')
        self.assertRegex(body, r'store i8 %[^,]+, i8 addrspace\(3\)\*')

    def test_device_implementation_x86_device_collapses_spaces(self):
        ir = _compile_module(self._IFACE, self._IMPL)
        self.assertEqual(_addrspaces(ir), [])
        body = ir.split('@"go"', 1)[1]
        self.assertIn('load i8', body)
        self.assertIn('store i8', body)


class TestHostUnitsUnchanged(unittest.TestCase):

    def test_plain_implementation_keeps_host_triple(self):
        ir = _compile("INTERFACE;\n"
                      "UNIT U (go);\n"
                      "PROCEDURE go;\n"
                      "END;\n"
                      "IMPLEMENTATION OF U;\n"
                      "PROCEDURE go;\n"
                      "BEGIN\n"
                      "END;\n"
                      ".\n")
        self.assertIn('target triple = "x86_64-pc-linux-gnu"', ir)
        self.assertEqual(_addrspaces(ir), [])


if __name__ == '__main__':
    unittest.main()
