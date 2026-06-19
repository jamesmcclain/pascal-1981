"""Second recission tranche for DEVICE MODULEs: GOTO and dynamic set-range
construction. The set *core* (constant ranges, union/intersect/membership,
dynamic singletons) stays -- only a set range with a non-constant bound, which
needs a runtime loop, is rejected. See docs/ads-implementation-plan.md Step 0.5.
"""
import unittest

from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


def _check(src):
    return PascalTypeChecker().check(parse_source(src))


def _err(src):
    r = _check(src)
    return ' '.join(str(e) for e in r.errors).lower() if not r.success else ''


class TestGotoRecission(unittest.TestCase):
    def test_goto_banned_in_device_module(self):
        src = "DEVICE MODULE M;\nPROCEDURE go;\nBEGIN\n  GOTO 1;\nEND;\n.\n"
        self.assertIn('goto', _err(src))

    def test_goto_allowed_in_host_module(self):
        src = "MODULE M;\nPROCEDURE go;\nBEGIN\n  GOTO 1;\nEND;\n.\n"
        self.assertTrue(_check(src).success)


class TestDynamicSetRangeRecission(unittest.TestCase):
    def test_dynamic_range_banned_in_device_module(self):
        src = ("DEVICE MODULE M;\nVAR s: SET OF CHAR; x: CHAR;\n"
               "PROCEDURE go;\nBEGIN s := ['A'..x]; END;\n.\n")
        self.assertIn('dynamic set-range', _err(src))

    def test_constant_range_kept_in_device_module(self):
        src = ("DEVICE MODULE M;\nVAR s: SET OF CHAR;\n"
               "PROCEDURE go;\nBEGIN s := ['A'..'Z']; END;\n.\n")
        self.assertTrue(_check(src).success, _check(src).errors)

    def test_dynamic_singleton_kept_in_device_module(self):
        # [x] is a single shift, branch-free and GPU-friendly -- NOT rescinded.
        src = ("DEVICE MODULE M;\nVAR s: SET OF CHAR; x: CHAR;\n"
               "PROCEDURE go;\nBEGIN s := [x]; END;\n.\n")
        self.assertTrue(_check(src).success, _check(src).errors)

    def test_dynamic_range_allowed_in_host_module(self):
        src = ("MODULE M;\nVAR s: SET OF CHAR; x: CHAR;\n"
               "PROCEDURE go;\nBEGIN s := ['A'..x]; END;\n.\n")
        self.assertTrue(_check(src).success, _check(src).errors)


if __name__ == '__main__':
    unittest.main()
