"""ADS memory-spaces: grammar + AST.

DEVICE MODULE, [SPACE(s)] attribute, ADS(s) OF T -- parsed ungated.
"""
import unittest

from pascal1981.ast_nodes import (Attribute, ImplementationUnit, InterfaceUnit,
                                   ModuleUnit, PointerType, TypeDecl, VarDecl,
                                   Identifier)
from tests.support import parse_source


def _module(body_decls: str, header: str = "MODULE Foo;") -> ModuleUnit:
    return parse_source(f"{header}\n{body_decls}\n.")


class TestDeviceModule(unittest.TestCase):
    def test_device_module_flag(self):
        unit = parse_source("DEVICE MODULE Foo;\n.")
        self.assertIsInstance(unit, ModuleUnit)
        self.assertTrue(unit.is_device)

    def test_plain_module_not_device(self):
        unit = parse_source("MODULE Foo;\n.")
        self.assertFalse(unit.is_device)

    def test_device_as_identifier_still_parses(self):
        # Contextual-keyword safety: `device` and `space` as ordinary names.
        unit = parse_source("MODULE Foo;\nVAR device: INTEGER; space: INTEGER;\n.")
        self.assertFalse(unit.is_device)


class TestUnitAstFlags(unittest.TestCase):
    def test_device_interface_flag(self):
        unit = parse_source("DEVICE INTERFACE;\nUNIT U (f);\nCONST K = 1;\nEND;\n")
        self.assertIsInstance(unit, InterfaceUnit)
        self.assertTrue(unit.is_device)
        self.assertFalse(unit.has_init)

    def test_plain_interface_defaults_not_device_and_no_init(self):
        unit = parse_source("INTERFACE;\nUNIT U;\nCONST K = 1;\nEND;\n")
        self.assertIsInstance(unit, InterfaceUnit)
        self.assertFalse(unit.is_device)
        self.assertFalse(unit.has_init)

    def test_interface_records_initializer_presence(self):
        unit = parse_source("INTERFACE;\nUNIT U;\nBEGIN\nEND;\n")
        self.assertIsInstance(unit, InterfaceUnit)
        self.assertFalse(unit.is_device)
        self.assertTrue(unit.has_init)

    def test_device_implementation_flag(self):
        unit = parse_source("DEVICE INTERFACE;\nUNIT U;\nEND;\nDEVICE IMPLEMENTATION OF U;\nBEGIN\nEND.\n")
        self.assertIsInstance(unit, ImplementationUnit)
        self.assertTrue(unit.is_device)
        self.assertIsNotNone(unit.init_body)

    def test_plain_implementation_defaults_not_device(self):
        unit = parse_source("INTERFACE;\nUNIT U;\nEND;\nIMPLEMENTATION OF U;\nBEGIN\nEND.\n")
        self.assertIsInstance(unit, ImplementationUnit)
        self.assertFalse(unit.is_device)
        self.assertIsNotNone(unit.init_body)

    def test_device_identifier_still_parses_in_plain_interface(self):
        unit = parse_source("INTERFACE;\nUNIT U;\nVAR device: INTEGER;\nEND;\n")
        self.assertIsInstance(unit, InterfaceUnit)
        self.assertFalse(unit.is_device)

    def test_device_identifier_still_parses_in_plain_implementation(self):
        unit = parse_source("INTERFACE;\nUNIT U;\nEND;\nIMPLEMENTATION OF U;\nVAR device: INTEGER;\n.\n")
        self.assertIsInstance(unit, ImplementationUnit)
        self.assertFalse(unit.is_device)


class TestSpaceAttribute(unittest.TestCase):
    def test_space_residence_attribute(self):
        unit = _module("VAR [SPACE(GLOBAL)] g: REAL;")
        vardecl = next(d for d in unit.decls if isinstance(d, VarDecl))
        attr = vardecl.attributes[0]
        self.assertIsInstance(attr, Attribute)
        self.assertEqual(attr.name, 'SPACE')
        self.assertIsInstance(attr.arg, Identifier)
        self.assertEqual(attr.arg.name, 'GLOBAL')

    def test_bare_attribute_is_attribute_node(self):
        unit = _module("VAR [READONLY] x: INTEGER;")
        vardecl = next(d for d in unit.decls if isinstance(d, VarDecl))
        attr = vardecl.attributes[0]
        self.assertEqual(attr.name, 'READONLY')
        self.assertIsNone(attr.arg)


class TestAdsPointeeSpace(unittest.TestCase):
    def test_ads_with_space(self):
        unit = _module("TYPE p = ADS(GLOBAL) OF REAL;")
        tdecl = next(d for d in unit.decls if isinstance(d, TypeDecl))
        ptr = tdecl.type_expr
        self.assertIsInstance(ptr, PointerType)
        self.assertEqual(ptr.flavor, 'ADS')
        self.assertIsInstance(ptr.space, Identifier)
        self.assertEqual(ptr.space.name, 'GLOBAL')

    def test_ads_without_space(self):
        unit = _module("TYPE p = ADS OF REAL;")
        tdecl = next(d for d in unit.decls if isinstance(d, TypeDecl))
        self.assertIsNone(tdecl.type_expr.space)


if __name__ == '__main__':
    unittest.main()
