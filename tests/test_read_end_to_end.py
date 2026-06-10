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


if __name__ == "__main__":
    unittest.main()
