"""ADS-value lowering + residence storage, plus the device-code recissions
(heap, host I/O, direct recursion) for DEVICE compilands.

These cover the ADS *value* gaps: the earlier suite checked ADS *type*
lowering but never ADS *value* production, which silently miscompiled
(`ADS x` produced a {ptr,i16} pair stored through a punning bitcast over an
addrspace(1) slot).
"""
import re
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


def _check(src):
    return PascalTypeChecker().check(parse_source(src))


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


# Producing an ADS value into a matching ADS(s) slot inside a device routine.
_VALUE_SRC = ("DEVICE MODULE M;\n"
              "VAR\n"
              "  [SPACE(GLOBAL)] g: INTEGER;\n"
              "  p: ADS(GLOBAL) OF INTEGER;\n"
              "PROCEDURE go;\n"
              "BEGIN\n"
              "  p := ADS g;\n"
              "END;\n"
              ".\n")


class TestAdsValueLowering(unittest.TestCase):

    def test_residence_global_is_placed_in_addrspace(self):
        ir = _compile(_VALUE_SRC, device_triple='nvptx64-nvidia-cuda')
        # g carries [SPACE(GLOBAL)] -> it must be an addrspace(1) global.
        gline = next(l for l in ir.splitlines() if l.startswith('@"g"'))
        self.assertIn('addrspace(1)', gline)

    def test_ads_value_is_an_addrspace_pointer_not_a_pair(self):
        ir = _compile(_VALUE_SRC, device_triple='nvptx64-nvidia-cuda')
        body = ir.split('@"go"', 1)[1]
        # The miscompile signature: a punning bitcast to the vintage {i16*, i16}
        # pair, and a struct store. Neither may appear.
        self.assertNotIn('{i16*, i16}', body)
        self.assertNotRegex(body, r'store\s*\{')
        # The store target should be a real addrspace(1) pointer.
        self.assertRegex(body, r'store .*addrspace\(1\)')

    def test_x86_device_value_collapses_to_addrspace_zero(self):
        # Same source on the CPU-device: addrspace 0 everywhere, still consistent.
        ir = _compile(_VALUE_SRC)  # device_triple defaults to x86
        self.assertNotIn('{i16*, i16}', ir.split('@"go"', 1)[1])

    def test_host_module_ads_value_unchanged(self):
        # Outside a device module, ADS x keeps the vintage {ptr, i16} pair.
        src = ("MODULE M; VAR g: INTEGER; p: ADS OF INTEGER;\n"
               "PROCEDURE go; BEGIN p := ADS g; END; .\n")
        ir = _compile(src)
        self.assertIn('{i16*, i16}', ir.split('@"go"', 1)[1])


class TestFirstRecissionTranche(unittest.TestCase):

    def _err(self, src):
        r = _check(src)
        self.assertFalse(r.success)
        return ' '.join(str(e) for e in r.errors).lower()

    def test_recursion_banned_in_device_module(self):
        src = ("DEVICE MODULE M;\n"
               "PROCEDURE loop; BEGIN loop; END; .\n")
        self.assertIn('recursion', self._err(src))

    def test_new_banned_in_device_module(self):
        src = ("DEVICE MODULE M;\n"
               "TYPE pt = ^INTEGER;\n"
               "VAR q: pt;\n"
               "PROCEDURE go; BEGIN NEW(q); END; .\n")
        self.assertIn('dynamic allocation', self._err(src))

    def test_host_io_banned_in_device_module(self):
        src = ("DEVICE MODULE M;\n"
               "PROCEDURE go; BEGIN WRITELN('hi'); END; .\n")
        self.assertIn('host i/o', self._err(src))

    def test_same_constructs_allowed_in_host_module(self):
        # Recursion / NEW / WRITELN are all fine outside a DEVICE MODULE.
        src = ("MODULE M;\n"
               "TYPE pt = ^INTEGER;\n"
               "VAR q: pt;\n"
               "PROCEDURE loop; BEGIN loop; END;\n"
               "PROCEDURE go; BEGIN NEW(q); WRITELN('hi'); END; .\n")
        r = _check(src)
        self.assertTrue(r.success, r.errors)


if __name__ == '__main__':
    unittest.main()
