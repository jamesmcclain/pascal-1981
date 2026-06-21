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
        self.assertIn("device code", _errs(r).lower())


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
            "(*$INCLUDE:'u'*)\n"
            "DEVICE IMPLEMENTATION OF U;\n"
            "PROCEDURE go; BEGIN WRITELN('hi'); END;\n"
            "PROCEDURE loop; BEGIN END;\n"
            ".\n"
        )
        err = self._err(src)
        self.assertIn("host i/o", err)
        self.assertIn("device code", err)
        self.assertNotIn("device module", err)

    def test_new_banned_in_device_implementation(self):
        src = (
            "(*$INCLUDE:'u'*)\n"
            "DEVICE IMPLEMENTATION OF U;\n"
            "TYPE pt = ^INTEGER;\n"
            "VAR q: pt;\n"
            "PROCEDURE go; BEGIN NEW(q); END;\n"
            "PROCEDURE loop; BEGIN END;\n"
            ".\n"
        )
        err = self._err(src)
        self.assertIn("dynamic allocation", err)
        self.assertIn("device code", err)
        self.assertNotIn("device module", err)

    def test_recursion_banned_in_device_implementation(self):
        src = (
            "(*$INCLUDE:'u'*)\n"
            "DEVICE IMPLEMENTATION OF U;\n"
            "PROCEDURE go; BEGIN END;\n"
            "PROCEDURE loop; BEGIN loop; END;\n"
            ".\n"
        )
        err = self._err(src)
        self.assertIn("recursion", err)
        self.assertIn("device code", err)
        self.assertNotIn("device module", err)

    def test_same_constructs_allowed_in_plain_implementation(self):
        plain_iface = (
            "INTERFACE;\n"
            "UNIT U (go, loop);\n"
            "PROCEDURE go;\n"
            "PROCEDURE loop;\n"
            "END;\n"
        )
        src = (
            "(*$INCLUDE:'u'*)\n"
            "IMPLEMENTATION OF U;\n"
            "TYPE pt = ^INTEGER;\n"
            "VAR q: pt;\n"
            "PROCEDURE loop; BEGIN loop; END;\n"
            "PROCEDURE go; BEGIN NEW(q); WRITELN('hi'); END;\n"
            ".\n"
        )
        r = typecheck_module(iface_code=plain_iface, impl_code=src, module_name='U')
        self.assertTrue(r.success, r.errors)


class TestDeviceUnitInitializerBan(unittest.TestCase):
    def test_device_interface_initializer_block_rejected(self):
        r = typecheck_source(
            "DEVICE INTERFACE;\n"
            "UNIT U;\n"
            "BEGIN\n"
            "END;\n"
        )
        self.assertFalse(r.success)
        self.assertIn("initializer code is not available in a DEVICE UNIT", _errs(r))

    def test_plain_interface_initializer_block_still_allowed(self):
        r = typecheck_source(
            "INTERFACE;\n"
            "UNIT U;\n"
            "BEGIN\n"
            "END;\n"
        )
        self.assertTrue(r.success, msg=_errs(r))

    def test_device_implementation_initializer_block_rejected(self):
        iface = (
            "DEVICE INTERFACE;\n"
            "UNIT TEST (go);\n"
            "PROCEDURE go;\n"
            "END;\n"
        )
        impl = (
            "(*$INCLUDE:'test'*)\n"
            "DEVICE IMPLEMENTATION OF TEST;\n"
            "PROCEDURE go;\n"
            "BEGIN\n"
            "END;\n"
            "BEGIN\n"
            "END.\n"
        )
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertFalse(result.success)
        self.assertIn("initializer code is not available in a DEVICE UNIT", _errs(result))

    def test_plain_implementation_initializer_block_still_allowed(self):
        iface = (
            "INTERFACE;\n"
            "UNIT TEST (go);\n"
            "PROCEDURE go;\n"
            "END;\n"
        )
        impl = (
            "(*$INCLUDE:'test'*)\n"
            "IMPLEMENTATION OF TEST;\n"
            "PROCEDURE go;\n"
            "BEGIN\n"
            "END;\n"
            "BEGIN\n"
            "END.\n"
        )
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertTrue(result.success, msg=_errs(result))


class TestDeviceUnitConsistency(unittest.TestCase):
    def test_device_implementation_requires_device_interface(self):
        iface = (
            "INTERFACE;\n"
            "UNIT TEST (go);\n"
            "PROCEDURE go;\n"
            "END;\n"
        )
        impl = (
            "(*$INCLUDE:'test'*)\n"
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
            "(*$INCLUDE:'test'*)\n"
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
