"""Host-interop conveniences added alongside the WORD8/record-layout work.

Three small, self-contained behaviors that a shim-free host program (the
mandelbrot-gpu Pascal renderer is the motivating example) needs:

  * STRING/LSTRING character indexing: ``S[I]`` is the Ith character (STRING
    is 1-based; LSTRING index 0 is the length byte, viewed as a CHAR).  The
    codegen already lowered this by the length-prefix convention; the type
    checker now agrees.
  * The vintage "INTEGER constants change to WORD" rule generalized to the
    extension family: a compile-time constant integer expression whose value
    fits may be assigned/passed to a WORD8/WORD32/WORD64 (or
    INTEGER8/INTEGER32/INTEGER64) target.  Non-constant values keep the
    strict rules.
  * SIZEOF counts as a constant integer for that rule (and for the classic
    16-bit WORD exemption), so ``FILLC(ADR x, SIZEOF(x), CHR(0))`` type
    checks -- the record-zeroing idiom every C-struct transcription needs.
"""

import unittest

from pascal1981.features import extended_features
from tests.support import (build_and_run_pascal_project, requires_exe,
                           typecheck_source)

WI = {"wide-integers": True}
EXT = extended_features()


class TestStringIndexingTypecheck(unittest.TestCase):
    def test_lstring_and_string_indexing_accepted(self):
        src = ("PROGRAM P; VAR l: LSTRING(10); s: STRING(5); c: CHAR; i: INTEGER;\n"
               "BEGIN i := 1; c := l[i]; c := s[1]; l[0] := CHR(3) END.")
        self.assertTrue(typecheck_source(src).success)

    def test_string_index_must_be_integer(self):
        src = ("PROGRAM P; VAR l: LSTRING(10); c: CHAR; r: REAL;\n"
               "BEGIN r := 1.0; c := l[r] END.")
        self.assertFalse(typecheck_source(src).success)


class TestConstantAdaptationTypecheck(unittest.TestCase):
    def test_named_const_adapts_to_word32_field(self):
        src = ("PROGRAM P;\n"
               "CONST v = 640;\n"
               "TYPE r = RECORD w: WORD32 END;\n"
               "VAR x: r;\n"
               "BEGIN x.w := v END.")
        self.assertTrue(typecheck_source(src, features=WI).success)

    def test_const_out_of_range_still_rejected(self):
        src = ("PROGRAM P;\n"
               "CONST v = 300;\n"
               "VAR b: WORD8;\n"
               "BEGIN b := v END.")
        self.assertFalse(typecheck_source(src, features=WI).success)

    def test_nonconstant_integer_still_rejected(self):
        src = ("PROGRAM P;\n"
               "VAR i: INTEGER; w: WORD32;\n"
               "BEGIN i := 5; w := i END.")
        self.assertFalse(typecheck_source(src, features=WI).success)

    def test_sizeof_passes_word_parameter(self):
        # The record-zeroing idiom: FILLC's len parameter is WORD; SIZEOF is
        # a compile-time constant and is exempt from the WORD/INTEGER wall.
        src = ("PROGRAM P;\n"
               "TYPE r = RECORD a: WORD32; b: ARRAY[0..63] OF CHAR END;\n"
               "VAR x: r;\n"
               "BEGIN FILLC(ADR x, SIZEOF(x), CHR(0)) END.")
        self.assertTrue(typecheck_source(src, features=WI).success)


@requires_exe
class TestStringIndexingBuildAndRun(unittest.TestCase):
    def test_lstring_char_walk_and_nul_termination(self):
        # The filename idiom from the shim-free renderer: walk the LSTRING's
        # characters into a CHAR array and NUL-terminate it for a C API.
        src = ("PROGRAM P(output);\n"
               "VAR l: LSTRING(10); a: ARRAY[0..10] OF CHAR; i, len: INTEGER;\n"
               "FUNCTION cstrlen(s: ADRMEM): CINT [C]; EXTERN;\n"
               "BEGIN\n"
               "  l := 'abc';\n"
               "  len := ORD(l.LEN);\n"
               "  FOR i := 1 TO len DO a[i - 1] := l[i];\n"
               "  a[len] := CHR(0);\n"
               "  WRITELN(len, ' ', a[0], a[1], a[2], ' ', cstrlen(ADR a))\n"
               "END.")
        c = ("#include <string.h>\n"
             "#include <stdint.h>\n"
             "int32_t cstrlen(const char *s){ return (int32_t)strlen(s); }\n")
        rc, out, err = build_and_run_pascal_project(
            files={'p.pas': src, 'c.c': c}, compile_pairs=[('p.pas', 'p.ll')],
            link_ir_relpaths=['p.ll', 'c.c'], exe_name='lstring-index',
            features=EXT)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['3', 'abc', '3'])

    def test_string_indexing_is_one_based(self):
        src = ("PROGRAM P(output);\n"
               "VAR s: STRING(3);\n"
               "BEGIN s := 'xyz'; WRITELN(s[1], s[3]) END.")
        rc, out, err = build_and_run_pascal_project(
            files={'p.pas': src}, compile_pairs=[('p.pas', 'p.ll')],
            link_ir_relpaths=['p.ll'], exe_name='string-index')
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), 'xz')


if __name__ == '__main__':
    unittest.main()
