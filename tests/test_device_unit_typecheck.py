"""DEVICE UNIT type-checker parity tests for Phase 1.3.

These prove that DEVICE INTERFACE / DEVICE IMPLEMENTATION reuse the same
checker-level device context as DEVICE MODULE.
"""

import unittest

from tests.support import typecheck_module, typecheck_source


def _errs(result):
    return " ".join(str(e) for e in result.errors)


class TestDeviceInterfaceContext(unittest.TestCase):
    def test_address_space_type_inside_device_interface_accepted(self):
        r = typecheck_source(
            "DEVICE INTERFACE;\n"
            "UNIT U;\n"
            "TYPE p = ADS(GLOBAL) OF REAL;\n"
            "END;\n"
        )
        self.assertTrue(r.success, msg=_errs(r))

    def test_address_space_type_inside_plain_interface_rejected(self):
        r = typecheck_source(
            "INTERFACE;\n"
            "UNIT U;\n"
            "TYPE p = ADS(GLOBAL) OF REAL;\n"
            "END;\n"
        )
        self.assertFalse(r.success)
        self.assertIn("DEVICE MODULE", _errs(r))


class TestDeviceImplementationRecissions(unittest.TestCase):
    _IFACE = (
        "DEVICE INTERFACE;\n"
        "UNIT U (go, loop);\n"
        "PROCEDURE go;\n"
        "PROCEDURE loop;\n"
        "END;\n"
    )

    def _err(self, src, iface_code=_IFACE):
        r = typecheck_module(iface_code=iface_code, impl_code=src, module_name='U')
        self.assertFalse(r.success)
        return _errs(r).lower()

    def test_host_io_banned_in_device_implementation(self):
        src = (
            "DEVICE IMPLEMENTATION OF U;\n"
            "PROCEDURE go; BEGIN WRITELN('hi'); END;\n"
            "PROCEDURE loop; BEGIN END;\n"
            ".\n"
        )
        self.assertIn("host i/o", self._err(src))

    def test_new_banned_in_device_implementation(self):
        src = (
            "DEVICE IMPLEMENTATION OF U;\n"
            "TYPE pt = ^INTEGER;\n"
            "VAR q: pt;\n"
            "PROCEDURE go; BEGIN NEW(q); END;\n"
            "PROCEDURE loop; BEGIN END;\n"
            ".\n"
        )
        self.assertIn("dynamic allocation", self._err(src))

    def test_recursion_banned_in_device_implementation(self):
        src = (
            "DEVICE IMPLEMENTATION OF U;\n"
            "PROCEDURE go; BEGIN END;\n"
            "PROCEDURE loop; BEGIN loop; END;\n"
            ".\n"
        )
        self.assertIn("recursion", self._err(src))

    def test_same_constructs_allowed_in_plain_implementation(self):
        plain_iface = (
            "INTERFACE;\n"
            "UNIT U (go, loop);\n"
            "PROCEDURE go;\n"
            "PROCEDURE loop;\n"
            "END;\n"
        )
        src = (
            "IMPLEMENTATION OF U;\n"
            "TYPE pt = ^INTEGER;\n"
            "VAR q: pt;\n"
            "PROCEDURE loop; BEGIN loop; END;\n"
            "PROCEDURE go; BEGIN NEW(q); WRITELN('hi'); END;\n"
            ".\n"
        )
        r = typecheck_module(iface_code=plain_iface, impl_code=src, module_name='U')
        self.assertTrue(r.success, r.errors)


class TestDeviceUnitConsistency(unittest.TestCase):
    def test_device_implementation_requires_device_interface(self):
        iface = (
            "INTERFACE;\n"
            "UNIT TEST (go);\n"
            "PROCEDURE go;\n"
            "END;\n"
        )
        impl = (
            "DEVICE IMPLEMENTATION OF TEST;\n"
            "PROCEDURE go;\n"
            "BEGIN\n"
            "END;\n"
            ".\n"
        )
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertFalse(result.success)
        self.assertIn("device-ness of implementation must match its interface", _errs(result))

    def test_plain_implementation_requires_plain_interface(self):
        iface = (
            "DEVICE INTERFACE;\n"
            "UNIT TEST (go);\n"
            "PROCEDURE go;\n"
            "END;\n"
        )
        impl = (
            "IMPLEMENTATION OF TEST;\n"
            "PROCEDURE go;\n"
            "BEGIN\n"
            "END;\n"
            ".\n"
        )
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertFalse(result.success)
        self.assertIn("device-ness of implementation must match its interface", _errs(result))


if __name__ == '__main__':
    unittest.main()
