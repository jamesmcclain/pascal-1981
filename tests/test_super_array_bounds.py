"""Tests for the heap super-array dynamic-bound ABI (docs/super-array-bounds-abi.md).

Follow-up item "Super-array remediation residue and device-heap boundary"
settled the runtime representation for the shipped one-dimensional heap
super-array subset:

  * Long-form ``NEW(p, u)`` prepends an 8-byte header holding ``u`` as an i64
    and points ``p`` at the element data just past it.
  * ``UPPER(p^)`` reads the dynamic bound back from that header;
    ``LOWER(p^)`` stays the static declared lower bound.
  * ``DISPOSE(p)`` for a super-array pointer frees from the header, not the
    data pointer.
  * ``$INDEXCK`` on ``p^[i]`` checks the static lower bound and the dynamic
    header upper bound (previously it aborted on any index above the declared
    lower bound, because the check guessed ``(low, low)`` for ``[low..*]``).
  * DEVICE code keeps heap allocation rescinded, so ``UPPER(p^)`` on a super
    array is rejected during type checking there — device buffers carry their
    bounds as explicit kernel parameters (the drop-in CUDA pointer ABI), and
    no header ever exists to read.

Parser/typecheck tests need no toolchain; IR tests need llvmlite; run tests
need clang (guarded by the usual decorators).
"""

import os
import subprocess
import tempfile
import unittest

from tests.support import (parse_source, requires_exe, requires_llvm, typecheck_source)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNTIME_LIB = os.path.join(_REPO_ROOT, "runtime", "build", "libpascalrt.a")


def compile_to_ir(src: str) -> str:
    from pascal1981.codegen_llvm import compile_to_llvm
    from pascal1981.type_checker import PascalTypeChecker
    ast = parse_source(src)
    result = PascalTypeChecker().check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    return compile_to_llvm(ast)


def build_run(src: str) -> tuple:
    """Compile, link against the runtime archive, run; return (rc, stdout)."""
    ir = compile_to_ir(src)
    tmpdir = tempfile.mkdtemp()
    try:
        ll_path = os.path.join(tmpdir, "prog.ll")
        with open(ll_path, "w") as f:
            f.write(ir)
        exe_path = os.path.join(tmpdir, "prog")
        cc = subprocess.run(["clang", "-w", ll_path, _RUNTIME_LIB, "-o", exe_path, "-lm"], capture_output=True, text=True)
        if cc.returncode != 0:
            raise RuntimeError(f"clang failed: {cc.stderr}")
        run = subprocess.run([exe_path], capture_output=True, text=True)
        return run.returncode, run.stdout
    finally:
        import shutil
        shutil.rmtree(tmpdir)


_VECT0 = "TYPE VECT = SUPER ARRAY [0..*] OF INTEGER; "


class TestUpperLowerDerefParsing(unittest.TestCase):

    def test_upper_deref_parses(self):
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; BEGIN NEW(p, 5); WRITELN(UPPER(p^)) END.")
        self.assertIsNotNone(parse_source(src))

    def test_lower_deref_parses(self):
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; BEGIN NEW(p, 5); WRITELN(LOWER(p^)) END.")
        self.assertIsNotNone(parse_source(src))

    def test_plain_form_still_parses(self):
        src = ("PROGRAM P; VAR a: ARRAY[1..3] OF INTEGER; "
               "BEGIN WRITELN(UPPER(a)); WRITELN(LOWER(a)) END.")
        self.assertIsNotNone(parse_source(src))


class TestUpperLowerDerefTypecheck(unittest.TestCase):

    def test_super_array_deref_accepted_in_host_code(self):
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; n: INTEGER; "
               "BEGIN NEW(p, 5); n := UPPER(p^); n := LOWER(p^) END.")
        self.assertTrue(typecheck_source(src).success)

    def test_fixed_array_deref_accepted(self):
        src = ("PROGRAM P; TYPE A5 = ARRAY[1..5] OF INTEGER; "
               "VAR p: ^A5; n: INTEGER; "
               "BEGIN NEW(p); n := UPPER(p^); n := LOWER(p^) END.")
        self.assertTrue(typecheck_source(src).success)

    def test_non_pointer_deref_rejected(self):
        src = ("PROGRAM P; VAR a: ARRAY[1..3] OF INTEGER; n: INTEGER; "
               "BEGIN n := UPPER(a^) END.")
        result = typecheck_source(src)
        self.assertFalse(result.success)
        self.assertTrue(any("requires a pointer variable" in e.message for e in result.errors))

    def test_non_array_pointee_rejected(self):
        src = ("PROGRAM P; VAR p: ^INTEGER; n: INTEGER; "
               "BEGIN NEW(p); n := UPPER(p^) END.")
        result = typecheck_source(src)
        self.assertFalse(result.success)
        self.assertTrue(any("expects an array pointee" in e.message for e in result.errors))

    def test_device_module_rejects_super_array_upper_deref(self):
        """Device code has no heap and no bound header; bounds travel as
        explicit kernel parameters (drop-in CUDA pointer ABI)."""
        src = ("DEVICE MODULE M;\n" + _VECT0 + "\n"
               "VAR q: ^VECT;\n"
               "PROCEDURE go; VAR n: INTEGER; BEGIN n := UPPER(q^) END;\n"
               ".\n")
        result = typecheck_source(src)
        self.assertFalse(result.success)
        self.assertTrue(any("dynamic super array bounds are not available in device code" in e.message for e in result.errors))


@requires_llvm
class TestBoundHeaderIR(unittest.TestCase):

    def test_new_long_form_allocates_header_and_stores_bound(self):
        """NEW(p, u) allocates data + 8 header bytes and stores u as an i64."""
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; BEGIN NEW(p, 10) END.")
        ir = compile_to_ir(src)
        # count * elem_size, then + 8 for the header, then malloc of the sum.
        self.assertIn("mul i64", ir)
        self.assertIn("add i64", ir)
        self.assertIn('call i8* @"malloc"(i64 %', ir)
        # The dynamic upper bound is stored through an i64* into the block.
        self.assertIn("store i64", ir)

    def test_upper_deref_loads_header(self):
        """UPPER(p^) loads the i64 one slot before the data pointer."""
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; BEGIN NEW(p, 10); WRITELN(UPPER(p^)) END.")
        ir = compile_to_ir(src)
        self.assertIn("i64 -1", ir)
        self.assertIn("load i64", ir)

    def test_lower_deref_is_static_constant(self):
        """LOWER(p^) is the declared lower bound; no header read is emitted."""
        src = ("PROGRAM P; TYPE VECT = SUPER ARRAY [3..*] OF INTEGER; "
               "VAR p: ^VECT; BEGIN NEW(p, 9); WRITELN(LOWER(p^)) END.")
        ir = compile_to_ir(src)
        # A single header access may exist for INDEXCK-free programs? No:
        # this program has no indexing and no UPPER, so no -1 GEP at all.
        self.assertNotIn("i64 -1", ir)

    def test_dispose_frees_from_header(self):
        """DISPOSE(p) on a super-array pointer steps back to the header."""
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; BEGIN NEW(p, 10); DISPOSE(p) END.")
        ir = compile_to_ir(src)
        self.assertIn("i64 -8", ir)
        self.assertIn('call void @"free"', ir)

    def test_dispose_of_plain_pointer_unchanged(self):
        """Non-super pointees keep the plain free(p) lowering."""
        src = "PROGRAM P; VAR p: ^INTEGER; BEGIN NEW(p); DISPOSE(p) END."
        ir = compile_to_ir(src)
        self.assertNotIn("i64 -8", ir)
        self.assertIn('call void @"free"', ir)


@requires_exe
class TestBoundHeaderRuntime(unittest.TestCase):

    def test_upper_and_lower_round_trip(self):
        """The bound written by NEW is the bound read back by UPPER."""
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; n: INTEGER; BEGIN "
               "n := 10; NEW(p, n); "
               "WRITELN(LOWER(p^)); WRITELN(UPPER(p^)); DISPOSE(p) END.")
        rc, out = build_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "0\n10\n")

    def test_nonzero_lower_bound_indexing_and_bounds(self):
        """Elements land where the declared lower bound says; both bound
        queries and full-range writes work for [3..*]."""
        src = ("PROGRAM P; TYPE VECT = SUPER ARRAY [3..*] OF INTEGER; "
               "VAR p: ^VECT; i: INTEGER; BEGIN "
               "NEW(p, 7); "
               "FOR i := LOWER(p^) TO UPPER(p^) DO p^[i] := i; "
               "WRITELN(p^[3]); WRITELN(p^[7]); DISPOSE(p) END.")
        rc, out = build_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "3\n7\n")

    def test_full_range_write_no_longer_aborts(self):
        """Previously $INDEXCK guessed bounds (low, low) for [low..*] and
        aborted on any index above the lower bound; now the whole allocated
        range is writable."""
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; i: INTEGER; BEGIN "
               "NEW(p, 10); "
               "FOR i := 0 TO UPPER(p^) DO p^[i] := i * 2; "
               "WRITELN(p^[10]); DISPOSE(p) END.")
        rc, out = build_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "20\n")

    def test_index_above_dynamic_bound_aborts(self):
        """$INDEXCK checks the dynamic header bound: p^[5] on NEW(p, 4)
        aborts at run time instead of writing past the allocation."""
        src = ("PROGRAM P; " + _VECT0 + "VAR p: ^VECT; i: INTEGER; BEGIN "
               "NEW(p, 4); i := 5; p^[i] := 1 END.")
        rc, out = build_run(src)
        self.assertNotEqual(rc, 0)

    def test_index_below_lower_bound_aborts(self):
        src = ("PROGRAM P; TYPE VECT = SUPER ARRAY [3..*] OF INTEGER; "
               "VAR p: ^VECT; i: INTEGER; BEGIN "
               "NEW(p, 7); i := 2; p^[i] := 1 END.")
        rc, out = build_run(src)
        self.assertNotEqual(rc, 0)

    def test_two_allocations_carry_independent_bounds(self):
        src = ("PROGRAM P; " + _VECT0 + "VAR p, q: ^VECT; BEGIN "
               "NEW(p, 4); NEW(q, 9); "
               "WRITELN(UPPER(p^)); WRITELN(UPPER(q^)); "
               "DISPOSE(p); DISPOSE(q) END.")
        rc, out = build_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "4\n9\n")

    def test_fixed_array_pointee_bounds_are_static(self):
        src = ("PROGRAM P; TYPE A5 = ARRAY[2..5] OF INTEGER; "
               "VAR p: ^A5; BEGIN NEW(p); "
               "WRITELN(LOWER(p^)); WRITELN(UPPER(p^)); DISPOSE(p) END.")
        rc, out = build_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "2\n5\n")


if __name__ == '__main__':
    unittest.main()
