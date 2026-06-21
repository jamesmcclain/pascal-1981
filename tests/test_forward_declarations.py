"""FORWARD declaration support: a `PROCEDURE/FUNCTION p; FORWARD;` may be
completed by a later body definition without a redeclaration error, which is
what makes forward references and mutual recursion across sibling routines
expressible. EXTERN/EXTERNAL semantics are unchanged.
"""
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


def _check(src):
    return PascalTypeChecker().check(parse_source(src))


def _ok(src):
    r = _check(src)
    return r.success, ' '.join(str(e) for e in r.errors)


class TestForwardCompletion(unittest.TestCase):

    def test_simple_forward_reference(self):
        src = ("MODULE M;\n"
               "PROCEDURE later; FORWARD;\n"
               "PROCEDURE early; BEGIN later; END;\n"
               "PROCEDURE later; BEGIN END;\n"
               ".\n")
        ok, errs = _ok(src)
        self.assertTrue(ok, errs)

    def test_mutual_recursion_via_forward(self):
        src = ("MODULE M;\n"
               "PROCEDURE ping; FORWARD;\n"
               "PROCEDURE pong; BEGIN ping; END;\n"
               "PROCEDURE ping; BEGIN pong; END;\n"
               ".\n")
        ok, errs = _ok(src)
        self.assertTrue(ok, errs)

    def test_forward_function(self):
        src = ("MODULE M;\n"
               "FUNCTION f: INTEGER; FORWARD;\n"
               "FUNCTION g: INTEGER; BEGIN g := f; END;\n"
               "FUNCTION f: INTEGER; BEGIN f := 1; END;\n"
               ".\n")
        ok, errs = _ok(src)
        self.assertTrue(ok, errs)

    def test_forward_completion_emits_single_definition(self):
        # codegen must reuse the forward prototype, not emit a duplicate function.
        src = ("MODULE M;\n"
               "PROCEDURE ping; FORWARD;\n"
               "PROCEDURE pong; BEGIN ping; END;\n"
               "PROCEDURE ping; BEGIN pong; END;\n"
               ".\n")
        ir = compile_to_llvm(parse_source(src))
        self.assertEqual(ir.count('define i32 @"ping"'), 1)
        self.assertEqual(ir.count('define i32 @"pong"'), 1)


class TestDirectivesUnchanged(unittest.TestCase):

    def test_extern_then_body_still_errors(self):
        # EXTERN is not a forward declaration: defining a body for it is still a
        # redeclaration.
        src = ("MODULE M;\n"
               "PROCEDURE p; EXTERN;\n"
               "PROCEDURE p; BEGIN END;\n"
               ".\n")
        ok, errs = _ok(src)
        self.assertFalse(ok)
        self.assertIn('already declared', errs.lower())

    def test_duplicate_forward_still_errors(self):
        src = ("MODULE M;\n"
               "PROCEDURE p; FORWARD;\n"
               "PROCEDURE p; FORWARD;\n"
               ".\n")
        ok, errs = _ok(src)
        self.assertFalse(ok)
        self.assertIn('already declared', errs.lower())

    def test_genuine_duplicate_definition_still_errors(self):
        src = ("MODULE M;\n"
               "PROCEDURE p; BEGIN END;\n"
               "PROCEDURE p; BEGIN END;\n"
               ".\n")
        ok, errs = _ok(src)
        self.assertFalse(ok)
        self.assertIn('already declared', errs.lower())


class TestForwardInDeviceModule(unittest.TestCase):

    def test_forward_mutual_recursion_banned_in_device_module(self):
        # With FORWARD working, the device recursion recission is now reachable
        # via the forward path (not only via nested procedures).
        src = ("DEVICE MODULE M;\n"
               "PROCEDURE ping; FORWARD;\n"
               "PROCEDURE pong; BEGIN ping; END;\n"
               "PROCEDURE ping; BEGIN pong; END;\n"
               ".\n")
        ok, errs = _ok(src)
        self.assertFalse(ok)
        self.assertIn('recursion', errs.lower())


if __name__ == '__main__':
    unittest.main()
