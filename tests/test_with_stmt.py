"""
WITH-statement test suite.

Pins the behavior of WITH over RECORD targets across all three pipeline
layers:

  * parse + type-check acceptance / rejection (pure Python, always runs)
  * LLVM IR generation                          (@requires_llvm)
  * native build + run                          (@requires_exe)

Regression anchor: WITH was previously a codegen no-op (the body emitted no
stores) and was never type-checked at all (the WithStmt branch was missing
from check_statement, so the body passed vacuously). These tests assert that
the body actually executes, that bare field names resolve to the target's
fields, that multiple comma-separated targets nest left-to-right (rightmost
shadows), that chained-selector targets work, and that a non-record target is
rejected.
"""

import unittest

from tests.support import requires_exe, requires_llvm, typecheck_source
# Codegen helpers live in test_codegen (the only module that imports llvmlite);
# reuse them rather than duplicating the parse/check/build plumbing.
from tests.test_codegen import build_and_run, compile_to_ir


class TestWithTypecheck(unittest.TestCase):
    """Acceptance / rejection at the type-checking layer (no llvmlite needed)."""

    def test_bare_field_assignment_accepted(self):
        """Bare field names inside WITH resolve to the record's fields."""
        result = typecheck_source("PROGRAM P; TYPE r = RECORD a: INTEGER END; VAR d: r; "
                                  "BEGIN WITH d DO a := 1 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_body_is_actually_checked(self):
        """The WITH body is type-checked (it was previously skipped entirely):
        an undefined name inside the body must be reported, not passed over."""
        result = typecheck_source("PROGRAM P; TYPE r = RECORD a: INTEGER END; VAR d: r; "
                                  "BEGIN WITH d DO a := nonesuch END.")
        self.assertFalse(result.success)
        self.assertIn("Undefined", " ".join(str(e) for e in result.errors))

    def test_non_record_target_rejected(self):
        """WITH over a non-record target is a type error."""
        result = typecheck_source("PROGRAM P; VAR n: INTEGER; BEGIN WITH n DO WRITELN('x') END.")
        self.assertFalse(result.success)
        self.assertIn("record", " ".join(str(e) for e in result.errors).lower())

    def test_field_alias_does_not_leak_past_with(self):
        """Field names are visible only inside the body; referencing them after
        the WITH ends is an undefined-variable error."""
        result = typecheck_source("PROGRAM P; TYPE r = RECORD a: INTEGER END; VAR d: r; "
                                  "BEGIN WITH d DO a := 1; a := 2 END.")
        self.assertFalse(result.success)
        self.assertIn("Undefined", " ".join(str(e) for e in result.errors))


@requires_llvm
class TestWithIR(unittest.TestCase):
    """IR-level assertions (requires llvmlite)."""

    def test_with_body_emits_field_store(self):
        """The body must lower to a real store of the assigned constant; the
        regression was that WITH emitted no stores at all."""
        ir = compile_to_ir("PROGRAM P; TYPE r = RECORD a: INTEGER END; VAR d: r; "
                           "BEGIN WITH d DO a := 2026 END.")
        self.assertIn("store i16 2026", ir)


@requires_exe
class TestWithBuildRun(unittest.TestCase):
    """End-to-end build + run (requires llvmlite + clang)."""

    def test_bare_fields_assigned(self):
        """Bare field assignments inside WITH take effect on the record."""
        src = """PROGRAM P;
TYPE date = RECORD day: INTEGER; month: INTEGER; year: INTEGER END;
VAR d: date;
BEGIN
  d.day := 1; d.month := 1; d.year := 1;
  WITH d DO BEGIN year := 2026; month := 6; day := 15 END;
  WRITELN(d.year:5, d.month:3, d.day:3)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, " 2026  6 15\n")

    def test_non_assignment_body_executes(self):
        """A WITH body that is not an assignment still runs (regression: the
        whole body used to be dropped)."""
        src = """PROGRAM P;
TYPE r = RECORD a: INTEGER END;
VAR d: r;
BEGIN
  WITH d DO WRITELN('inside-with')
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "inside-with\n")

    def test_multi_target_rightmost_shadows(self):
        """WITH a, b DO ... is nested left-to-right, so on a field-name clash
        the rightmost target wins; the earlier target is untouched."""
        src = """PROGRAM P;
TYPE r = RECORD x: INTEGER; y: INTEGER END;
VAR a, b: r;
BEGIN
  a.x := 10; a.y := 11;
  b.x := 20; b.y := 21;
  WITH a, b DO BEGIN x := 99; y := 88 END;
  WRITELN(a.x:3, a.y:3, b.x:3, b.y:3)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        # a is left untouched (10, 11); b receives the assignments (99, 88).
        self.assertEqual(out, " 10 11 99 88\n")

    def test_nested_with(self):
        """A WITH nested inside another resolves the inner record's fields while
        the outer record's fields remain visible."""
        src = """PROGRAM P;
TYPE inner = RECORD v: INTEGER END;
     outer = RECORD i: inner; w: INTEGER END;
VAR o: outer;
BEGIN
  WITH o DO BEGIN
    w := 7;
    WITH i DO v := 42
  END;
  WRITELN(o.w:3, o.i.v:3)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  7 42\n")

    def test_chained_selector_target(self):
        """A WITH target reached through array subscript and pointer deref
        (arr[i].p^) opens the pointed-at record. This exercises the case the
        grammar previously carried only as [INFERRED] for runtime."""
        src = """PROGRAM P;
TYPE node = RECORD a: INTEGER; b: INTEGER END;
     np   = ^node;
     rec  = RECORD p: np END;
VAR arr: ARRAY[1..3] OF rec;
    n1: node;
    i: INTEGER;
BEGIN
  i := 2;
  arr[i].p := ADR n1;
  WITH arr[i].p^ DO BEGIN a := 100; b := 200 END;
  WRITELN(n1.a:4, n1.b:4)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, " 100 200\n")

    def test_field_read_inside_with(self):
        """Bare field names also resolve as r-values, not just assignment
        targets."""
        src = """PROGRAM P;
TYPE r = RECORD a: INTEGER; b: INTEGER END;
VAR d: r;
BEGIN
  d.a := 5;
  WITH d DO b := a + 3;
  WRITELN(d.b:3)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  8\n")


if __name__ == "__main__":
    unittest.main()
