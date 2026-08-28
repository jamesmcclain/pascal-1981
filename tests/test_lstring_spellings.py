"""
Every spelling an LSTRING reaches codegen as.

LSTRING is a predeclared identifier, not a reserved word (IBM Pascal, Aug 1981,
p.3-7), so `LSTRING(n)` parses as a NamedType carrying a parameter -- there is
no dedicated AST node for it any more.  Codegen has to recognise that spelling
everywhere it used to match the AST/resolved node classes, and it has to cope
with a bound the parser stored as an identifier rather than an integer:

  * `s.LEN` -- directly, through a user TYPE alias, and through a record field.
    Matching only the node classes sent these into the record-field path, which
    fails the compile with "Cannot access field 'LEN'".
  * `LSTRING(N)` / `STRING(N)` with a named constant bound.  parse_type keeps a
    non-literal bound as the identifier's text, so int(param) escaped codegen as
    a bare ValueError instead of allocating N+1 bytes.
"""

import unittest

from tests.support import requires_exe, requires_llvm
from tests.test_codegen import build_and_run, compile_to_ir


@requires_exe
class TestLStringLenSpellings(unittest.TestCase):

    def test_len_on_parameterised_lstring(self):
        rc, out = build_and_run("""
PROGRAM P(output);
VAR s: LSTRING(10);
BEGIN
  s := 'hi';
  WRITELN(ORD(s.LEN));
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ['2'])

    def test_len_through_user_alias(self):
        rc, out = build_and_run("""
PROGRAM P(output);
TYPE Line = LSTRING(20);
VAR s: Line;
BEGIN
  s := 'abcd';
  WRITELN(ORD(s.LEN));
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ['4'])

    def test_len_through_record_field(self):
        rc, out = build_and_run("""
PROGRAM P(output);
TYPE Entry = RECORD nm: LSTRING(16) END;
VAR e: Entry;
BEGIN
  e.nm := 'abc';
  WRITELN(ORD(e.nm.LEN));
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ['3'])


@requires_llvm
class TestNamedConstantBound(unittest.TestCase):

    def test_lstring_named_constant_bound_matches_literal(self):
        ir = compile_to_ir("""
PROGRAM P(output);
CONST N = 20;
VAR s: LSTRING(N);
BEGIN
  s := 'hi';
END.
""")
        self.assertIn('[21 x i8]', ir)

    def test_string_named_constant_bound_matches_literal(self):
        ir = compile_to_ir("""
PROGRAM P(output);
CONST N = 20;
VAR s: STRING(N);
BEGIN
  s := 'exactly twenty chars';
END.
""")
        self.assertIn('[20 x i8]', ir)


@requires_exe
class TestNamedConstantBoundRuntime(unittest.TestCase):

    def test_lstring_named_constant_bound_runs(self):
        rc, out = build_and_run("""
PROGRAM P(output);
CONST N = 12;
VAR s: LSTRING(N);
BEGIN
  s := 'hello';
  WRITELN(s, ' ', ORD(s.LEN));
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ['hello', '5'])


if __name__ == '__main__':
    unittest.main()
