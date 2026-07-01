"""Tests for the wide unsigned types WORD32/WORD64 and the WORD16/INTEGER16
synonyms.

Gating:
  * WORD32/WORD64 are the unsigned siblings of INTEGER32/INTEGER64 and require
    the same mode (``wide-integers``).
  * WORD16 (= WORD) and INTEGER16 (= INTEGER) are synonyms that exist if and
    only if REAL64 exists, i.e. under ``wide-reals`` (or device code).

Semantics pinned here:
  * Unsigned widening WORD -> WORD32 -> WORD64 is implicit; narrowing is not; a
    signed INTEGER does not implicitly become an unsigned WORD32 (convert via
    WRD into WORD first, then widen).
  * Mixed-width arithmetic takes the wider operand's signedness (WORD +
    INTEGER32 -> INTEGER32, the WORD value zero-extending in); same-width mixes
    go unsigned (WORD32 + INTEGER32 -> WORD32).
  * WORD32/WORD64 print and widen as unsigned (zero-extend).
"""

import unittest

from pascal1981.type_system import (INTEGER_TYPE, INTEGER32_TYPE, WORD_TYPE,
                                     WORD32_TYPE, WORD64_TYPE,
                                     binary_op_result_type)
from tests.support import (build_and_run_pascal_project, parse_source,
                           requires_exe, typecheck_source)

WI = {"wide-integers": True}
WR = {"wide-reals": True}
WIWR = {"wide-integers": True, "wide-reals": True}


def ok(src, feats):
    return typecheck_source(src, features=feats).success


def run(src, feats, exe):
    files = {"p.pas": src}
    rc, out, err = build_and_run_pascal_project(
        files=files, compile_pairs=[("p.pas", "p.ll")],
        link_ir_relpaths=["p.ll"], exe_name=exe, features=feats)
    return rc, out, err


class TestGating(unittest.TestCase):
    def test_word32_word64_need_wide_integers(self):
        self.assertTrue(ok("PROGRAM P; VAR w: WORD32; BEGIN w := 0 END.", WI))
        self.assertTrue(ok("PROGRAM P; VAR w: WORD64; BEGIN w := 0 END.", WI))
        self.assertFalse(ok("PROGRAM P; VAR w: WORD32; BEGIN w := 0 END.", None))
        self.assertFalse(ok("PROGRAM P; VAR w: WORD64; BEGIN w := 0 END.", None))
        # WORD32/WORD64 are NOT gated by wide-reals.
        self.assertFalse(ok("PROGRAM P; VAR w: WORD32; BEGIN w := 0 END.", WR))

    def test_word16_integer16_track_wide_integers(self):
        # The 16-bit synonyms ride the wide-integer surface, like INTEGER32/WORD32.
        self.assertTrue(ok("PROGRAM P; VAR w: WORD16; BEGIN w := 0 END.", WI))
        self.assertTrue(ok("PROGRAM P; VAR i: INTEGER16; BEGIN i := 0 END.", WI))
        # Absent in the vintage dialect, and absent under wide-reals alone
        # (REAL64 being on does NOT bring the integer synonyms in).
        self.assertFalse(ok("PROGRAM P; VAR w: WORD16; BEGIN w := 0 END.", None))
        self.assertFalse(ok("PROGRAM P; VAR i: INTEGER16; BEGIN i := 0 END.", None))
        self.assertFalse(ok("PROGRAM P; VAR w: WORD16; BEGIN w := 0 END.", WR))
        self.assertFalse(ok("PROGRAM P; VAR i: INTEGER16; BEGIN i := 0 END.", WR))


class TestSynonyms(unittest.TestCase):
    def test_word16_is_word_integer16_is_integer(self):
        # A WORD16 value flows where WORD is expected and vice versa (same type).
        self.assertTrue(ok(
            "PROGRAM P; VAR a: WORD16; b: WORD; BEGIN a := 5; b := a; a := b END.", WI))
        self.assertTrue(ok(
            "PROGRAM P; VAR a: INTEGER16; b: INTEGER; BEGIN a := 5; b := a; a := b END.", WI))


class TestWidening(unittest.TestCase):
    def test_unsigned_widening_chain(self):
        self.assertTrue(ok("PROGRAM P; VAR w: WORD; d: WORD32; BEGIN d := w END.", WI))
        self.assertTrue(ok("PROGRAM P; VAR a: WORD32; b: WORD64; BEGIN b := a END.", WI))
        self.assertTrue(ok("PROGRAM P; VAR w: WORD; b: WORD64; BEGIN b := w END.", WI))

    def test_narrowing_rejected(self):
        self.assertFalse(ok("PROGRAM P; VAR w: WORD; d: WORD32; BEGIN w := d END.", WI))
        self.assertFalse(ok("PROGRAM P; VAR a: WORD32; b: WORD64; BEGIN a := b END.", WI))

    def test_signed_to_unsigned_wide_rejected(self):
        # The WORD/INTEGER signedness wall extends to the wide unsigned types.
        self.assertFalse(ok("PROGRAM P; VAR i: INTEGER; d: WORD32; BEGIN d := i END.", WI))
        # ...but WRD into WORD, then widen, is fine.
        self.assertTrue(ok("PROGRAM P; VAR i: INTEGER; d: WORD32; BEGIN d := WRD(i) END.", WI))


class TestArithmeticResultTypes(unittest.TestCase):
    def test_same_width_unsigned_sticky(self):
        self.assertEqual(binary_op_result_type(WORD32_TYPE, 'PLUS', WORD32_TYPE), WORD32_TYPE)
        self.assertEqual(binary_op_result_type(WORD32_TYPE, 'PLUS', INTEGER32_TYPE), WORD32_TYPE)
        self.assertEqual(binary_op_result_type(WORD_TYPE, 'PLUS', INTEGER_TYPE), WORD_TYPE)

    def test_wider_signedness_wins(self):
        # WORD (16) mixed with INTEGER32 (32): the wider signed type wins; the
        # WORD value zero-extends in.
        self.assertEqual(binary_op_result_type(WORD_TYPE, 'PLUS', INTEGER32_TYPE), INTEGER32_TYPE)
        self.assertEqual(binary_op_result_type(WORD32_TYPE, 'PLUS', WORD64_TYPE), WORD64_TYPE)


@requires_exe
class TestBuildAndRun(unittest.TestCase):
    def test_word32_prints_unsigned(self):
        # 4000000000 > INTEGER32 max but valid as unsigned WORD32.
        src = ("PROGRAM P(output);\nVAR a: WORD32;\n"
               "BEGIN a := 4000000000; WRITELN(a) END.")
        rc, out, err = run(src, WI, "word32-unsigned")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), "4000000000")

    def test_word_zero_extends_into_word32_and_word64(self):
        src = ("PROGRAM P(output);\nVAR w: WORD; c: WORD32; b: WORD64;\n"
               "BEGIN w := 60000; c := w; b := c; WRITELN(c); WRITELN(b) END.")
        rc, out, err = run(src, WI, "word-zext-chain")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["60000", "60000"])

    def test_synonyms_run(self):
        src = ("PROGRAM P(output);\nVAR a: WORD16; b: INTEGER16;\n"
               "BEGIN a := 60000; b := -100; WRITELN(a); WRITELN(b) END.")
        rc, out, err = run(src, WI, "word16-int16")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["60000", "-100"])


class TestWideMaxConstants(unittest.TestCase):
    """MAXWORD32 / MAXWORD64: the unsigned siblings of MAXINT32 / MAXINT64.

    Gated on ``wide-integers`` exactly like the wide types and the signed wide
    max constants.  They carry full WORD32 / WORD64 type identity (assignable to
    their own type, widen WORD32 -> WORD64, but not assignment-compatible with
    INTEGER and not narrowable).
    """

    def test_gated_on_wide_integers(self):
        self.assertTrue(ok("PROGRAM P; VAR w: WORD32; BEGIN w := MAXWORD32 END.", WI))
        self.assertTrue(ok("PROGRAM P; VAR w: WORD64; BEGIN w := MAXWORD64 END.", WI))
        # Absent in the vintage dialect, and absent under wide-reals alone.
        self.assertFalse(ok("PROGRAM P; VAR w: WORD32; BEGIN w := MAXWORD32 END.", None))
        self.assertFalse(ok("PROGRAM P; VAR w: WORD64; BEGIN w := MAXWORD64 END.", None))
        self.assertFalse(ok("PROGRAM P; VAR w: WORD32; BEGIN w := MAXWORD32 END.", WR))

    def test_type_identity(self):
        # WORD32 widens to WORD64; the constants follow the same rules as values.
        self.assertTrue(ok("PROGRAM P; VAR w: WORD64; BEGIN w := MAXWORD32 END.", WI))
        # WORD-family is not assignment-compatible with signed INTEGER.
        self.assertFalse(ok("PROGRAM P; VAR i: INTEGER; BEGIN i := MAXWORD32 END.", WI))
        # No implicit narrowing WORD64 -> WORD32.
        self.assertFalse(ok("PROGRAM P; VAR w: WORD32; BEGIN w := MAXWORD64 END.", WI))


@requires_exe
class TestWideMaxConstantsRun(unittest.TestCase):
    def test_maxword32_prints_unsigned(self):
        # 2**32-1; has the high bit set, so it must print unsigned (not -1).
        src = "PROGRAM P(output);\nBEGIN WRITELN(MAXWORD32) END."
        rc, out, err = run(src, WI, "maxword32-print")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), "4294967295")

    def test_maxword64_prints_unsigned(self):
        # 2**64-1; exceeds the signed i64 max, so _const_ir must emit it at i64
        # and the formatter must print it unsigned (not -1).
        src = "PROGRAM P(output);\nBEGIN WRITELN(MAXWORD64) END."
        rc, out, err = run(src, WI, "maxword64-print")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), "18446744073709551615")

    def test_constants_match_type_maxima(self):
        # The constant equals the widened max of its type: a WORD32 var set to
        # its largest literal prints the same as MAXWORD32, and likewise WORD64.
        src = ("PROGRAM P(output);\nVAR a: WORD32; b: WORD64;\n"
               "BEGIN a := MAXWORD32; b := MAXWORD64;\n"
               "WRITELN(a); WRITELN(b) END.")
        rc, out, err = run(src, WI, "maxword-roundtrip")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()],
                         ["4294967295", "18446744073709551615"])


if __name__ == "__main__":
    unittest.main()
