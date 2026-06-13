"""End-to-end READ/READLN tests over a compiled Pascal executable with piped stdin.

These are the only tests that can see a READ dispatch bug: IR tests show which
runtime helper is called, and C driver tests verify readq.c in isolation, but
only a full Pascal compile-link-run proves the value lands intact in the
variable. This layer was missing when the READ dispatch shipped broken twice;
keep it.

Requires llvmlite + clang (auto-skipped otherwise, like the rest of the
@requires_exe suite).
"""

import unittest

from tests.support import requires_exe
from tests.test_codegen import _build_pascal_with_runtime


@requires_exe
class TestReadAllTypesEndToEnd(unittest.TestCase):

    def test_read_every_readable_type_roundtrip(self):
        """One program covering every readable type and the line-handling rules.

        Pins, in a single run: negative INTEGER; WORD at its upper bound (65535);
        REAL with :10:3 fixed-point formatting; READ of a CHAR with NO leading
        whitespace skip; READLN() consuming the remainder of a line (the 'x'
        after the char); and LSTRING input truncated to its declared capacity.
        """
        src = ("PROGRAM full;\n"
               "VAR i: INTEGER; w: WORD; r: REAL; c: CHAR; s: LSTRING(5);\n"
               "BEGIN\n"
               "  READLN(i); READLN(w); READLN(r); READ(c); READLN(); READLN(s);\n"
               "  WRITELN('i=', i); WRITELN('w=', w); WRITELN(r:10:3);\n"
               "  WRITELN('c=', c); WRITELN('s=', s)\n"
               "END.\n")
        stdin = "-7\n65535\n3.5\nQx\nhello world\n"
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin=stdin)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out,
            "i=-7\n"
            "w=65535\n"
            "     3.500\n"
            "c=Q\n"
            "s=hello\n",
        )

    def test_word_input_out_of_range_aborts(self):
        src = ("PROGRAM P; VAR w: WORD; BEGIN READLN(w); WRITELN(w) END.")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="70000\n")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("70000", out)

    def test_malformed_integer_input_aborts(self):
        src = ("PROGRAM P; VAR i: INTEGER; BEGIN READLN(i); WRITELN(i) END.")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="abc\n")
        self.assertNotEqual(rc, 0)

    def test_readln_skips_trailing_junk_on_line(self):
        src = ("PROGRAM P; VAR i, j: INTEGER;\n"
               "BEGIN READLN(i); READLN(j); WRITELN(i + j) END.\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="40 junk on this line\n2\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "42")

    def test_enum_read_numeric_by_default(self):
        src = ("PROGRAM P; TYPE Color=(RED,GREEN,BLUE); VAR x: Color;\n"
               "BEGIN READLN(x); WRITELN(ORD(x)) END.\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="1\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "1\n")

    def test_enum_read_rejects_symbolic_by_default(self):
        src = ("PROGRAM P; TYPE Color=(RED,GREEN,BLUE); VAR x: Color;\n"
               "BEGIN READLN(x); WRITELN(ORD(x)) END.\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="GREEN\n")
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")

    def test_enum_symbolic_read_under_feature(self):
        src = ("PROGRAM P; TYPE Color=(RED,GREEN,BLUE); VAR x: Color;\n"
               "BEGIN READLN(x); WRITELN(ORD(x)) END.\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="green\n", features={'symbolic-enum-io': True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "1\n")

    def test_enum_roundtrip_default_and_symbolic_modes(self):
        src = ("PROGRAM P; TYPE Color=(RED,GREEN,BLUE); VAR x: Color;\n"
               "BEGIN READLN(x); WRITELN(x) END.\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="2\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "2\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="BLUE\n", features={'symbolic-enum-io': True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "BLUE\n")

    def test_symbolic_enum_out_of_range_write_falls_back_to_ordinal(self):
        src = ("PROGRAM P; TYPE Color=(RED,GREEN); VAR x: Color;\n"
               "BEGIN READLN(x); WRITELN(x) END.\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="5\n", features={'symbolic-enum-io': True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "5\n")


if __name__ == "__main__":
    unittest.main()
