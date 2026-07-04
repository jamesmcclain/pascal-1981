"""Tests for the 8-bit extension types WORD8 (unsigned) and INTEGER8 (signed),
and the WRD8 retyping conversion.

Gating:
  * WORD8/INTEGER8 ride the ``wide-integers`` surface, exactly like
    INTEGER32/WORD32 (and, like WORD32, they are also available inside DEVICE
    code without the flag).
  * WRD8 exists exactly where WORD8 exists.

Semantics pinned here:
  * INTEGER8 is a true signed 8-bit integer, NOT a synonym for CHAR: it does
    arithmetic and WRITEs as a number; CHAR still WRITEs as a glyph.
  * Widening is implicit and one-way: WORD8 -> WORD/WORD32/WORD64 (zext) and
    WORD8 -> INTEGER/INTEGER32/INTEGER64 (value-preserving, every WORD8 fits);
    INTEGER8 -> INTEGER/INTEGER32/INTEGER64 (sext).  Narrowing into WORD8 or
    INTEGER8 is never implicit; WRD8(x) is the explicit truncating retype.
  * The signedness wall holds at equal width: INTEGER8 is not implicitly a
    WORD8 and mixing them in arithmetic draws the WORD/INTEGER mix diagnostic.
  * Literal range checks: WORD8 is 0..255, INTEGER8 is -128..127.
  * Across the C ABI, [C] routines tag WORD8 ``zeroext`` and INTEGER8
    ``signext`` (Phase 4 discipline extended down to 8 bits).
"""

import unittest

from pascal1981.features import extended_features
from pascal1981.type_system import (INTEGER8_TYPE, INTEGER32_TYPE, INTEGER_TYPE, WORD8_TYPE, WORD32_TYPE, WORD_TYPE, binary_op_result_type, can_assign)
from tests.support import (build_and_run_pascal_project, requires_exe, requires_llvm, typecheck_source)

WI = {"wide-integers": True}
EXT = extended_features()


def ok(src, feats=WI):
    return typecheck_source(src, features=feats).success


class TestGating(unittest.TestCase):

    def test_word8_integer8_need_wide_integers(self):
        self.assertTrue(ok("PROGRAM P; VAR b: WORD8; BEGIN b := 0 END."))
        self.assertTrue(ok("PROGRAM P; VAR i: INTEGER8; BEGIN i := 0 END."))
        self.assertFalse(ok("PROGRAM P; VAR b: WORD8; BEGIN b := 0 END.", None))
        self.assertFalse(ok("PROGRAM P; VAR i: INTEGER8; BEGIN i := 0 END.", None))

    def test_wrd8_needs_wide_integers(self):
        self.assertTrue(ok("PROGRAM P; VAR b: WORD8; BEGIN b := WRD8(300) END."))
        self.assertFalse(ok("PROGRAM P; VAR w: WORD; BEGIN w := WRD8(300) END.", None))

    def test_integer8_is_not_char(self):
        # A CHAR literal is not assignable to INTEGER8 and vice versa; they are
        # distinct types (INTEGER8 is an integer, CHAR is a character).
        self.assertFalse(ok("PROGRAM P; VAR i: INTEGER8; BEGIN i := 'A' END."))
        self.assertFalse(ok("PROGRAM P; VAR c: CHAR; VAR i: INTEGER8; BEGIN i := 0; c := i END."))


class TestTypeRules(unittest.TestCase):

    def test_widening_assignability(self):
        self.assertTrue(can_assign(WORD8_TYPE, WORD_TYPE))
        self.assertTrue(can_assign(WORD8_TYPE, WORD32_TYPE))
        self.assertTrue(can_assign(WORD8_TYPE, INTEGER_TYPE))
        self.assertTrue(can_assign(WORD8_TYPE, INTEGER32_TYPE))
        self.assertTrue(can_assign(INTEGER8_TYPE, INTEGER_TYPE))
        self.assertTrue(can_assign(INTEGER8_TYPE, INTEGER32_TYPE))

    def test_no_implicit_narrowing_or_signedness_crossing(self):
        self.assertFalse(can_assign(INTEGER_TYPE, WORD8_TYPE))
        self.assertFalse(can_assign(WORD_TYPE, WORD8_TYPE))
        self.assertFalse(can_assign(INTEGER_TYPE, INTEGER8_TYPE))
        self.assertFalse(can_assign(INTEGER8_TYPE, WORD8_TYPE))
        self.assertFalse(can_assign(WORD8_TYPE, INTEGER8_TYPE))

    def test_arithmetic_ranks(self):
        # Same-width arithmetic stays 8-bit; unsigned wins a same-width mix;
        # a wider operand's signedness wins a mixed-width expression.
        self.assertIs(binary_op_result_type(WORD8_TYPE, 'PLUS', WORD8_TYPE), WORD8_TYPE)
        self.assertIs(binary_op_result_type(INTEGER8_TYPE, 'MUL', INTEGER8_TYPE), INTEGER8_TYPE)
        self.assertIs(binary_op_result_type(WORD8_TYPE, 'PLUS', INTEGER8_TYPE), WORD8_TYPE)
        self.assertIs(binary_op_result_type(WORD8_TYPE, 'PLUS', INTEGER32_TYPE), INTEGER32_TYPE)
        self.assertIs(binary_op_result_type(WORD8_TYPE, 'PLUS', WORD_TYPE), WORD_TYPE)

    def test_literal_ranges(self):
        self.assertTrue(ok("PROGRAM P; VAR b: WORD8; BEGIN b := 255 END."))
        self.assertFalse(ok("PROGRAM P; VAR b: WORD8; BEGIN b := 256 END."))
        self.assertTrue(ok("PROGRAM P; VAR i: INTEGER8; BEGIN i := -128 END."))
        self.assertFalse(ok("PROGRAM P; VAR i: INTEGER8; BEGIN i := 128 END."))

    def test_same_width_mix_draws_word_int_diagnostic(self):
        src = ("PROGRAM P; VAR b: WORD8; i: INTEGER8; x: WORD8;\n"
               "BEGIN b := 1; i := 1; x := b + i END.")
        result = typecheck_source(src, features={"wide-integers": True, "strict-word-int": True})
        self.assertFalse(result.success)

    def test_read_of_byte_types_is_rejected(self):
        # READ remains narrower than WRITE; the 8-bit types are write-only
        # for now (like BOOLEAN input).
        self.assertFalse(ok("PROGRAM P(input); VAR b: WORD8; BEGIN READ(b) END."))
        self.assertFalse(ok("PROGRAM P(input); VAR i: INTEGER8; BEGIN READ(i) END."))


@requires_llvm
class TestByteTypesIR(unittest.TestCase):

    def _ir(self, src, features=WI):
        from pascal1981.codegen import compile_to_llvm
        from tests.support import parse_source as _parse
        ast = _parse(src)
        result = typecheck_source(src, features=features)
        assert result.success, [e.message for e in result.errors]
        return compile_to_llvm(ast, features=features)

    def test_byte_types_lower_to_i8(self):
        ir = self._ir("PROGRAM P; VAR b: WORD8; i: INTEGER8; BEGIN b := 1; i := 1 END.")
        self.assertIn('global i8', ir)

    def test_sizeof_is_one(self):
        ir = self._ir("PROGRAM P(output); VAR b: WORD8; i: INTEGER8;\n"
                      "BEGIN WRITELN(SIZEOF(b), ' ', SIZEOF(i)) END.")
        # SIZEOF is folded to a constant i16 1 at compile time.
        self.assertIn('i16 1', ir)

    def test_c_abi_sign_attrs(self):
        ir = self._ir("PROGRAM P;\n"
                      "FUNCTION f(b: WORD8): INTEGER8 [C]; EXTERN;\n"
                      "VAR i: INTEGER8;\n"
                      "BEGIN i := f(WRD8(1)) END.", features=EXT)
        self.assertIn('zeroext', ir)  # WORD8 parameter
        self.assertIn('signext', ir)  # INTEGER8 return


@requires_exe
class TestByteTypesBuildAndRun(unittest.TestCase):

    def _run(self, src, exe, features=WI, cfiles=None):
        files = {"p.pas": src}
        link = ["p.ll"]
        if cfiles:
            files.update(cfiles)
            link += list(cfiles)
        rc, out, err = build_and_run_pascal_project(files=files, compile_pairs=[("p.pas", "p.ll")], link_ir_relpaths=link, exe_name=exe, features=features)
        return rc, out, err

    def test_arithmetic_write_and_conversions(self):
        src = ("PROGRAM P(output);\n"
               "VAR b: WORD8; i: INTEGER8; w: WORD32; s: INTEGER32; c: CHAR;\n"
               "BEGIN\n"
               "  b := 200; b := b + 55;\n"
               "  i := -5;\n"
               "  w := b;              { zero-extends: 255 }\n"
               "  s := i;              { sign-extends: -5 }\n"
               "  c := CHR(b - 190);   { 65 = 'A' }\n"
               "  WRITELN(b, ' ', i, ' ', w, ' ', s, ' ', c, ' ', WRD8(300))\n"
               "END.")
        rc, out, err = self._run(src, 'byte-arith')
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), '255 -5 255 -5 A 44')

    def test_word8_buffer_loop(self):
        # The byte-buffer workload the type exists for: fill, then reduce.
        src = ("PROGRAM P(output);\n"
               "VAR a: ARRAY[0..7] OF WORD8; i: INTEGER; s: INTEGER32;\n"
               "BEGIN\n"
               "  FOR i := 0 TO 7 DO a[i] := WRD8(i * 30);\n"
               "  s := 0;\n"
               "  FOR i := 0 TO 7 DO s := s + a[i];\n"
               "  WRITELN(s, ' ', a[7])\n"
               "END.")
        rc, out, err = self._run(src, 'byte-buffer')
        self.assertEqual(rc, 0, msg=err)
        # i*30 for i=0..7 truncated to 8 bits: 0,30,60,90,120,150,180,210 -> 840
        self.assertEqual(out.strip(), '840 210')

    def test_c_ffi_roundtrip_uint8_int8(self):
        # WORD8 maps to C uint8_t (zeroext) and INTEGER8 to int8_t (signext),
        # including a negative int8_t return -- the 8-bit analog of the Phase 4
        # dirty-bit cases.
        src = ("PROGRAM P(output);\n"
               "FUNCTION add_u8(a: WORD8; b: WORD8): WORD8 [C]; EXTERN;\n"
               "FUNCTION neg_i8(a: INTEGER8): INTEGER8 [C]; EXTERN;\n"
               "VAR b: WORD8; i: INTEGER8;\n"
               "BEGIN\n"
               "  b := add_u8(WRD8(200), WRD8(56));  { wraps to 0 }\n"
               "  i := neg_i8(100);\n"
               "  WRITELN(b, ' ', i)\n"
               "END.")
        c = ("#include <stdint.h>\n"
             "uint8_t add_u8(uint8_t a, uint8_t b){ return (uint8_t)(a + b); }\n"
             "int8_t neg_i8(int8_t a){ return (int8_t)(-a); }\n")
        rc, out, err = self._run(src, 'byte-cffi', features=EXT, cfiles={'c.c': c})
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), '0 -100')


if __name__ == '__main__':
    unittest.main()
