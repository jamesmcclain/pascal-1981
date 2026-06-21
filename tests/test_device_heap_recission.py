import unittest

from tests.support import parse_source, typecheck_module, typecheck_source


class DeviceHeapRecissionTests(unittest.TestCase):
    def test_device_module_rejects_short_form_new_with_clear_diagnostic(self):
        src = (
            "DEVICE MODULE M;\n"
            "TYPE pt = ^INTEGER;\n"
            "VAR q: pt;\n"
            "PROCEDURE go; BEGIN NEW(q) END;\n"
            ".\n"
        )
        result = typecheck_source(src)
        self.assertFalse(result.success)
        messages = [e.message for e in result.errors]
        self.assertIn("dynamic allocation ('NEW') is not available in device code", messages)

    def test_device_module_rejects_super_array_long_form_new_before_codegen(self):
        src = (
            "DEVICE MODULE M;\n"
            "TYPE VECT = SUPER ARRAY [0..*] OF INTEGER;\n"
            "VAR q: ^VECT;\n"
            "PROCEDURE go; BEGIN NEW(q, 10) END;\n"
            ".\n"
        )
        ast = parse_source(src)
        result = typecheck_source(src)
        self.assertFalse(result.success)
        messages = [e.message for e in result.errors]
        self.assertIn("dynamic allocation ('NEW') is not available in device code", messages)
        # If type checking rejects the construct, the normal-code lowering that
        # calls malloc is unreachable for DEVICE code.
        self.assertIsNotNone(ast)

    def test_device_implementation_rejects_super_array_long_form_new(self):
        iface = "DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n"
        impl = (
            "DEVICE IMPLEMENTATION OF U;\n"
            "TYPE VECT = SUPER ARRAY [0..*] OF INTEGER;\n"
            "VAR q: ^VECT;\n"
            "PROCEDURE go; BEGIN NEW(q, 10) END;\n"
            ".\n"
        )
        result = typecheck_module(iface_code=iface, impl_code=impl, module_name='U')
        self.assertFalse(result.success)
        self.assertIn("dynamic allocation ('NEW') is not available in device code",
                      [e.message for e in result.errors])

    def test_device_module_rejects_dispose_with_clear_diagnostic(self):
        src = (
            "DEVICE MODULE M;\n"
            "TYPE pt = ^INTEGER;\n"
            "VAR q: pt;\n"
            "PROCEDURE go; BEGIN DISPOSE(q) END;\n"
            ".\n"
        )
        result = typecheck_source(src)
        self.assertFalse(result.success)
        self.assertIn("dynamic allocation ('DISPOSE') is not available in device code",
                      [e.message for e in result.errors])


if __name__ == '__main__':
    unittest.main()
