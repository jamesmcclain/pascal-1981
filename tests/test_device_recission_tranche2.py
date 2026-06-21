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


class TestLabeledStatementRecission(unittest.TestCase):
    """Companion to the GOTO ban: a label is GOTO machinery unless it sits on a
    loop (where it names a BREAK/CYCLE target). Device code rescinds GOTO, so a
    label on a non-loop statement -- a dead landing pad -- is rejected too.
    Loop labels stay legal so labeled BREAK/CYCLE keeps working on device."""

    def test_bare_label_banned_in_device_module(self):
        src = ("DEVICE MODULE M;\nPROCEDURE go;\nLABEL 1;\nVAR i: INTEGER;\n"
               "BEGIN\n  1: i := 1;\nEND;\n.\n")
        self.assertIn('labeled statement', _err(src))

    def test_label_on_if_banned_in_device_module(self):
        src = ("DEVICE MODULE M;\nPROCEDURE go;\nLABEL 1;\nVAR i: INTEGER;\n"
               "BEGIN\n  1: IF i = 1 THEN i := 2;\nEND;\n.\n")
        self.assertIn('labeled statement', _err(src))

    def test_bare_label_allowed_in_host_module(self):
        src = ("MODULE M;\nPROCEDURE go;\nLABEL 1;\nVAR i: INTEGER;\n"
               "BEGIN\n  1: i := 1;\nEND;\n.\n")
        self.assertTrue(_check(src).success, _check(src).errors)

    def test_labeled_while_for_break_cycle_kept_in_device_module(self):
        # Loop labels are BREAK/CYCLE targets -- structured, reducible, kept.
        while_break = ("DEVICE MODULE M;\nPROCEDURE go;\nLABEL OUTER;\nVAR i: INTEGER;\n"
                       "BEGIN\n  OUTER: WHILE i < 3 DO BEGIN i := i + 1;\n"
                       "  IF i = 2 THEN BREAK OUTER END;\nEND;\n.\n")
        for_cycle = ("DEVICE MODULE M;\nPROCEDURE go;\nLABEL L;\nVAR i: INTEGER;\n"
                     "BEGIN\n  L: FOR i := 1 TO 3 DO BEGIN IF i = 2 THEN CYCLE L END;\nEND;\n.\n")
        repeat_lbl = ("DEVICE MODULE M;\nPROCEDURE go;\nLABEL L;\nVAR i: INTEGER;\n"
                      "BEGIN\n  i := 0; L: REPEAT i := i + 1 UNTIL i >= 3;\nEND;\n.\n")
        for src in (while_break, for_cycle, repeat_lbl):
            self.assertTrue(_check(src).success, _check(src).errors)

    def test_bare_label_banned_in_device_implementation_unit(self):
        # The ban follows in_device_module, so it covers UNIT forms, not just
        # the single-file DEVICE MODULE the older comment named.
        import os, shutil, tempfile
        from pascal1981.parser import parse_file
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, 'u'), 'w') as f:
                f.write("DEVICE INTERFACE;\nUNIT U;\nPROCEDURE go;\nEND;\n")
            impl = os.path.join(tmp, 'u.pas')
            with open(impl, 'w') as f:
                f.write("(*$INCLUDE:'u'*)\nDEVICE IMPLEMENTATION OF U;\nPROCEDURE go;\n"
                        "LABEL 1;\nVAR i: INTEGER;\nBEGIN\n 1: i := 1;\nEND;\n.\n")
            r = PascalTypeChecker(source_file=impl).check(parse_file(impl))
            self.assertFalse(r.success)
            self.assertIn('labeled statement', ' '.join(str(e) for e in r.errors).lower())
        finally:
            shutil.rmtree(tmp)


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
