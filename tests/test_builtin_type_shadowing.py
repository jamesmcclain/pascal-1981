"""
Shadowing a predeclared type name.

IBM Pascal, Aug 1981, p.3-7 ("Predeclared Identifiers"): "The following are
predeclared identifiers. They can be re-defined by the programmer, but doing
this is not recommended." None of INTEGER/WORD/BOOLEAN/CHAR/REAL/TEXT appear in
the reserved-word list -- the manual draws that contrast explicitly for NIL,
which "cannot be redefined by the programmer" precisely "since it is a reserved
word in ISO Pascal."

So a user TYPE of that name wins wherever the source names it, while the
compiler's own internal uses of the built-in meaning are unaffected (p.6228:
BOOLEAN "can be re-defined by the programmer, but the old type is implicitly
used by the compiler for things like the IF statement and Boolean
expressions").

Before this was fixed, resolve_type and llvm_type matched the built-in names
before consulting the user type table while get_string_type_info did the
opposite, so `TYPE Word = LSTRING(64)` typechecked as the i16 built-in WORD and
then died in codegen_var_decl with "'IntType' object has no attribute 'count'".
"""

import unittest

from tests.support import requires_exe, requires_llvm
from tests.test_codegen import build_and_run, compile_to_ir


@requires_exe
class TestBuiltinTypeShadowing(unittest.TestCase):

    def test_word_shadowed_by_lstring(self):
        """The original reproducer: TYPE Word = LSTRING(64)."""
        rc, out = build_and_run("""
PROGRAM ShadowWord(output);
TYPE
  Word = LSTRING(64);
VAR
  current: Word;
BEGIN
  current := 'shadowed';
  WRITELN(current);
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), 'shadowed')

    def test_shadowing_is_case_insensitive(self):
        """Spelling the shadow canonically must work too, not just `Word`."""
        rc, out = build_and_run("""
PROGRAM ShadowWordCaps(output);
TYPE
  WORD = LSTRING(64);
VAR
  current: WORD;
BEGIN
  current := 'caps too';
  WRITELN(current);
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), 'caps too')

    def test_builtin_name_shadowed_by_record(self):
        """A record, not just a super array -- exercises named_record_struct."""
        rc, out = build_and_run("""
PROGRAM ShadowBoolean(output);
TYPE
  BOOLEAN = RECORD
    x: INTEGER;
  END;
VAR
  c: BOOLEAN;
BEGIN
  c.x := 42;
  WRITELN(c.x);
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), '42')

    def test_parameterised_spelling_stays_builtin(self):
        """STRING(n)/LSTRING(n) carry a param and are the built-in super-array
        constructor, so they keep their meaning where the bare name is a user
        type."""
        rc, out = build_and_run("""
PROGRAM ShadowStringName(output);
TYPE
  STRING = RECORD
    tag: INTEGER;
  END;
  LSTRING = RECORD
    tag: INTEGER;
  END;
VAR
  r: STRING;
  l: LSTRING;
  s: STRING(16);
  ls: LSTRING(16);
BEGIN
  r.tag := 7;
  l.tag := 9;
  s := 'still builtin   ';
  ls := 'also builtin    ';
  WRITELN(r.tag);
  WRITELN(l.tag);
  WRITELN(s);
  WRITELN(ls);
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ['7', '9', 'still', 'builtin', 'also', 'builtin'])

    def test_unshadowed_builtins_are_unaffected(self):
        """The compiler's own internal uses keep the predeclared meaning."""
        rc, out = build_and_run("""
PROGRAM NoShadow(output);
VAR
  b: BOOLEAN;
  n: INTEGER;
BEGIN
  b := TRUE;
  n := 3;
  IF b THEN WRITELN(n);
END.
""")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), '3')


@requires_llvm
class TestShadowedTypeIR(unittest.TestCase):

    def test_shadowed_word_lowers_as_a_string_not_i16(self):
        ir = compile_to_ir("""
PROGRAM ShadowWordIR(output);
TYPE
  Word = LSTRING(64);
VAR
  current: Word;
BEGIN
  current := 'xy';
END.
""")
        self.assertIn('[65 x i8]', ir)
        self.assertNotIn('@current = internal global i16', ir)


if __name__ == '__main__':
    unittest.main()
