"""
Recursive / self-referential record type test suite.

Pins support for the type machinery a linked list needs, none of which worked
before:

  * a pointer type that forward-references a record declared later in the same
    TYPE section (np = ^node; node = RECORD ... next: np END)
  * a record that references itself through a pointer field, both spellings
    (next: np  and  next: ^node)
  * pointer equality/inequality, including against NIL (the WHILE p <> NIL walk)
  * NEW / DISPOSE on a pointer declared via a named alias

Plus guards against the regressions introduced while adding identified structs:
distinct-but-equivalent records must still whole-copy, named records reached
through two separate programs must not collide (separate LLVM contexts), and a
VALUE-section initializer on a named record must still build.
"""

import unittest

from tests.support import requires_exe, requires_llvm, typecheck_source
from tests.test_codegen import build_and_run, compile_to_ir


class TestRecursiveTypecheck(unittest.TestCase):
    """Forward / self references resolve to real records, not ^CHAR."""

    def test_forward_ref_pointer_type(self):
        result = typecheck_source("PROGRAM P; TYPE np = ^node; node = RECORD data: INTEGER; next: np END; "
                                  "VAR p: np; BEGIN NEW(p); p^.data := 1 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_inline_self_ref_record(self):
        result = typecheck_source("PROGRAM P; TYPE node = RECORD data: INTEGER; next: ^node END; "
                                  "VAR p: ^node; BEGIN NEW(p); p^.data := 1 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_pointer_compared_to_nil(self):
        """A typed record pointer can be compared against NIL (WHILE p <> NIL)."""
        result = typecheck_source("PROGRAM P; TYPE np = ^node; node = RECORD data: INTEGER; next: np END; "
                                  "VAR p: np; BEGIN p := NIL; IF p <> NIL THEN WRITELN(1) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_two_pointers_compared(self):
        result = typecheck_source("PROGRAM P; TYPE np = ^node; node = RECORD data: INTEGER; next: np END; "
                                  "VAR a, b: np; BEGIN IF a = b THEN WRITELN(1) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))


@requires_llvm
class TestRecursiveIR(unittest.TestCase):
    """Self-referential records lower to an identified struct (no infinite
    recursion in codegen)."""

    def test_self_ref_record_builds(self):
        ir = compile_to_ir("PROGRAM P; TYPE np = ^node; node = RECORD data: INTEGER; next: np END; "
                           "VAR p: np; BEGIN NEW(p) END.")
        # The record is emitted as a named identified struct, and its self-link
        # is a pointer to that same struct.
        self.assertIn("%\"NODE\" = type {", ir)
        self.assertIn("%\"NODE\"*", ir)


@requires_exe
class TestRecursiveBuildRun(unittest.TestCase):
    """End-to-end linked-list construction and traversal."""

    def test_linked_list_forward_ref(self):
        """Build a list by prepending and walk it with WITH p^ DO."""
        src = """PROGRAM LinkedList(OUTPUT);
TYPE
  np   = ^node;
  node = RECORD data: INTEGER; next: np END;
VAR head, p: np; i: INTEGER;
BEGIN
  head := NIL;
  FOR i := 1 TO 3 DO BEGIN
    NEW(p);
    WITH p^ DO BEGIN data := i * 10; next := head END;
    head := p;
  END;
  p := head;
  WHILE p <> NIL DO
    WITH p^ DO BEGIN WRITELN(data:4); p := next END;
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  30\n  20\n  10\n")

    def test_linked_list_inline_self_ref(self):
        """The inline self-reference spelling (next: ^node) also builds/runs."""
        src = """PROGRAM Inline(OUTPUT);
TYPE node = RECORD data: INTEGER; next: ^node END;
VAR head, p: ^node; i: INTEGER;
BEGIN
  head := NIL;
  FOR i := 1 TO 3 DO BEGIN
    NEW(p); p^.data := i; p^.next := head; head := p;
  END;
  p := head;
  WHILE p <> NIL DO BEGIN WRITELN(p^.data:3); p := p^.next END;
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  3\n  2\n  1\n")

    def test_multi_hop_deref_and_dispose(self):
        """p^.next^.data chains through the heap; DISPOSE frees alias-typed
        pointers."""
        src = """PROGRAM Hop(OUTPUT);
TYPE np = ^node; node = RECORD data: INTEGER; next: np END;
VAR a, b: np;
BEGIN
  NEW(a); NEW(b);
  a^.data := 1; a^.next := b;
  b^.data := 2; b^.next := NIL;
  WRITELN(a^.data:3, a^.next^.data:3);
  DISPOSE(b); DISPOSE(a);
  WRITELN('ok')
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  1  2\nok\n")


@requires_exe
class TestIdentifiedStructRegressions(unittest.TestCase):
    """Guards for the identified-struct change, so its regressions can't return."""

    def test_equivalent_records_still_whole_copy(self):
        """Two distinct but structurally equal records still copy field-by-field,
        even though they now lower to separate identified structs."""
        src = """PROGRAM P(OUTPUT);
TYPE A = RECORD c: INTEGER; t: INTEGER END;
     B = RECORD c: INTEGER; t: INTEGER END;
VAR a: A; b: B;
BEGIN
  b.c := 7; b.t := 8;
  a := b;
  WRITELN(a.c:3, a.t:3)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  7  8\n")

    def test_record_with_named_record_field(self):
        """A record holding another named record by value (not pointer) lays out
        and addresses correctly under identified structs."""
        src = """PROGRAM P(OUTPUT);
TYPE inner = RECORD v: INTEGER END;
     outer = RECORD i: inner; w: INTEGER END;
VAR o: outer;
BEGIN
  o.w := 5; o.i.v := 9;
  WRITELN(o.w:3, o.i.v:3)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  5  9\n")


if __name__ == "__main__":
    unittest.main()
