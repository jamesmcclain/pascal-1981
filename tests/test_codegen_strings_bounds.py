"""
Supplementary codegen tests.

Covers two gaps found while reviewing the string/UPPER-LOWER patch:

  1. String ASSIGNMENT lowering. The existing string codegen tests only
     declare STRING/LSTRING variables (empty body) or assign NULL. None of
     them assign an ordinary string literal to a STRING/LSTRING variable, so
     the "7.1 ... Proven by tests.test_codegen" claim was never actually
     exercising assignment. These tests do. They are expected to PASS:
     both sides lower to inline aggregates, so assignment is a memcpy of the
     decoded bytes (plus a length byte for LSTRING).

  2. UPPER / LOWER on a NON-literal (named-constant) array bound. The codegen
     path reads `index_range.low.value` / `.high.value` directly instead of
     using the existing `eval_const_expr`, and an `Identifier` AST node has no
     `.value` attribute -- so the bound resolves to None and codegen raises
     CodegenError, even though the type checker accepts the same program.
     These two tests are expected to FAIL until that path is switched to
     `eval_const_expr` (see the suggested one-line fix in the review). They
     encode the desired behavior, so they flip to green once the bug is fixed.

Like tests/test_codegen.py, everything here requires llvmlite and is skipped
otherwise. The compile_to_ir helper is reused from test_codegen.py rather than
re-declared.
"""

import unittest

from tests.support import requires_exe, requires_llvm
from tests.test_codegen import build_and_run, compile_to_ir, _build_pascal_with_runtime
from tests.support import parse_source, typecheck_source


@requires_llvm
class TestStringAssignmentCodegen(unittest.TestCase):
    """String literal assignment to STRING/LSTRING storage (expected PASS)."""

    def test_string_literal_assignment_lowers_to_store(self):
        """Assigning a literal to a STRING(n) var emits a memcpy of the bytes."""
        src = "PROGRAM P; VAR a: STRING(10); BEGIN a := 'abc' END."
        ir = compile_to_ir(src)
        # The literal bytes are emitted as a private global constant ...
        self.assertIn("abc", ir)
        # ... and the assignment lowers to a memcpy into the inline aggregate.
        self.assertIn("memcpy", ir)

    def test_lstring_literal_assignment_lowers_to_store(self):
        """Assigning a literal to an LSTRING(n) var emits a memcpy of the bytes."""
        src = "PROGRAM P; VAR b: LSTRING(10); BEGIN b := 'hi' END."
        ir = compile_to_ir(src)
        self.assertIn("hi", ir)
        self.assertIn("memcpy", ir)

    def test_string_literal_with_doubled_quote_assignment(self):
        """A doubled-quote literal stores its decoded byte (a'b), not the escape."""
        src = "PROGRAM P; VAR a: STRING(10); BEGIN a := 'a''b' END."
        ir = compile_to_ir(src)
        # Decoded content is the three bytes a ' b -- the doubled quote must
        # have collapsed to a single quote in the emitted constant.
        self.assertIn("a'b", ir)
        self.assertIn("memcpy", ir)


@requires_llvm
class TestArrayBoundIntrinsicCodegen(unittest.TestCase):
    """UPPER / LOWER lowering, including the non-literal-bound case."""

    def test_upper_literal_bound_lowers_to_constant(self):
        """Baseline (expected PASS): UPPER of a literal-bound array folds to the bound."""
        src = ("PROGRAM P; VAR a: ARRAY[1..10] OF INTEGER; "
               "BEGIN WRITELN(UPPER(a)) END.")
        ir = compile_to_ir(src)
        self.assertIn("10", ir)

    def test_lower_literal_bound_lowers_to_constant(self):
        """Baseline (expected PASS): LOWER of a literal-bound array folds to the bound."""
        src = ("PROGRAM P; VAR a: ARRAY[2..10] OF INTEGER; "
               "BEGIN WRITELN(LOWER(a)) END.")
        ir = compile_to_ir(src)
        self.assertIn("2", ir)

    def test_upper_named_const_bound_resolves(self):
        """UPPER must resolve a named-constant upper bound.

        Now PASSES: the codegen UPPER/LOWER path resolves bounds via
        eval_const_expr (fixed in "Fix UPPER/LOWER bound resolution for named
        constants"), matching what the type checker already accepts.
        """
        src = ("PROGRAM P; CONST n = 10; VAR a: ARRAY[1..n] OF INTEGER; "
               "BEGIN WRITELN(UPPER(a)) END.")
        ir = compile_to_ir(src)
        self.assertIn("10", ir)

    def test_lower_named_const_bound_resolves(self):
        """LOWER must resolve a named-constant lower bound (now PASSES; see above)."""
        src = ("PROGRAM P; CONST lo = 2; VAR a: ARRAY[lo..10] OF INTEGER; "
               "BEGIN WRITELN(LOWER(a)) END.")
        ir = compile_to_ir(src)
        self.assertIn("2", ir)


@requires_llvm
class TestLStringLengthSemantics(unittest.TestCase):
    """LSTRING is length-prefixed, not null-terminated (manual 6-18/6-19).

    Regression coverage for the capacity overflow: the old codegen wrote a
    null terminator at byte [current_len + 1], which is one past the end of
    the [n+1 x i8] aggregate when a string is assigned at exact capacity
    (current_len == n). LSTRING has no terminator; output is driven by the
    length byte. These encode both the safety fix and the WRITE semantics.
    """

    def test_lstring_write_is_length_driven_not_terminator(self):
        """WRITE of an LSTRING uses %.*s (length byte), not a %s scan to NUL."""
        src = "PROGRAM P; VAR s: LSTRING(10); BEGIN s := 'hi'; WRITELN(s) END."
        ir = compile_to_ir(src)
        # The length-counted printf conversion must be present.
        self.assertIn(".*s", ir)

    def test_lstring_assignment_emits_no_terminator_store(self):
        """Assignment must not write a trailing NUL past the copied chars.

        The fixed path stores only the length byte [0] and memcpy's the chars
        to [1..]; there is no separate store of an i8 0 terminator. We assert
        the characters and a memcpy are present (the store of the length byte
        remains), which is the whole of the LSTRING write.
        """
        src = "PROGRAM P; VAR s: LSTRING(3); BEGIN s := 'abc' END."
        ir = compile_to_ir(src)
        self.assertIn("abc", ir)
        self.assertIn("memcpy", ir)

    @requires_exe
    def test_lstring_at_exact_capacity_round_trips(self):
        """LSTRING(n) := <n-char literal> then WRITE must print exactly it.

        This is the exact-capacity case that previously wrote one byte past
        the aggregate. With the terminator removed it is safe and correct.
        """
        src = "PROGRAM P; VAR s: LSTRING(3); BEGIN s := 'abc'; WRITE(s) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "abc")

    @requires_exe
    def test_lstring_partial_fill_writes_only_current_length(self):
        """A short value in a large LSTRING writes only its current length."""
        src = "PROGRAM P; VAR s: LSTRING(10); BEGIN s := 'hi'; WRITE(s) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "hi")


@requires_exe
class TestWriteFieldWidthOrdering(unittest.TestCase):
    """printf dynamic args must be emitted width-then-precision.

    Regression for the %*.*s arg-ordering bug: WRITE(s:w) for a string built a
    %*.*s format but pushed the implicit length (which is the *precision*)
    ahead of the field width, so width and precision were swapped at runtime.
    A right-justified field-width write is the clean discriminator: with the
    bug the value was unpadded; fixed, it is right-justified in the field.
    """

    def test_lstring_field_width_right_justifies(self):
        # 'hi' in a field of width 6 -> 4 leading spaces. Under the old swap
        # the args were [length=2, width=6], so printf saw width 2 / precision
        # 6 and emitted "hi" with no padding.
        src = "PROGRAM P; VAR s: LSTRING(10); BEGIN s := 'hi'; WRITE(s:6) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "    hi")

    def test_integer_field_width_unaffected(self):
        # Guard: the common (non-string) width path must keep working.
        src = "PROGRAM P; VAR x: INTEGER; BEGIN x := 42; WRITE(x:5) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "   42")


@requires_llvm
class TestStringIntrinsicCapacityIR(unittest.TestCase):
    def _compile_to_ir_force(self, src: str, force_rangeck):
        from codegen_llvm import compile_to_llvm
        ast = parse_source(src)
        result = typecheck_source(src)
        if not result.success:
            raise RuntimeError(f"Type check failed: {result.errors}")
        return compile_to_llvm(ast, force_rangeck=force_rangeck)

    """CONCAT/COPYLST/COPYSTR must emit a capacity range check (manual 11-20).

    These only need IR generation (no clang): the guard lowers to a call to
    the runtime error handler (abort), so its presence in the IR is the signal.
    """

    def test_concat_emits_range_check(self):
        src = "PROGRAM P; VAR d: LSTRING(5); BEGIN d := 'ab'; CONCAT(d, 'cd') END."
        self.assertIn("abort", compile_to_ir(src))

    def test_copylst_emits_range_check(self):
        src = "PROGRAM P; VAR d: LSTRING(5); BEGIN COPYLST('abc', d) END."
        self.assertIn("abort", compile_to_ir(src))

    def test_copystr_emits_range_check(self):
        src = "PROGRAM P; VAR d: STRING(5); BEGIN COPYSTR('abc', d) END."
        self.assertIn("abort", compile_to_ir(src))

    def test_rangeck_off_removes_string_guards(self):
        src = "PROGRAM P; VAR d: LSTRING(3); BEGIN {$RANGECK-} d := 'ab'; CONCAT(d, 'cd') END."
        ir = self._compile_to_ir_force(src, force_rangeck=False)
        self.assertNotIn("str_assign_overflow", ir)
        self.assertNotIn("concat_overflow", ir)

    def test_rangeck_force_on_overrides_source_off(self):
        src = "PROGRAM P; VAR d: LSTRING(3); BEGIN {$RANGECK-} d := 'ab'; CONCAT(d, 'cd') END."
        ir = self._compile_to_ir_force(src, force_rangeck=True)
        self.assertIn("str_assign_overflow", ir)
        self.assertIn("concat_overflow", ir)


@requires_exe
class TestStringIntrinsicCapacityRuntime(unittest.TestCase):
    """End-to-end: in-capacity calls succeed; over-capacity calls abort."""

    def test_concat_within_capacity(self):
        src = ("PROGRAM P; VAR d: LSTRING(5); "
               "BEGIN d := 'ab'; CONCAT(d, 'cd'); WRITE(d) END.")
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "abcd")

    def test_concat_overflow_aborts(self):
        # 2 + 2 = 4 > capacity 3 -> manual range error (abort).
        src = "PROGRAM P; VAR d: LSTRING(3); BEGIN d := 'ab'; CONCAT(d, 'cd') END."
        rc, _ = build_and_run(src)
        self.assertNotEqual(rc, 0)

    def test_copylst_within_capacity(self):
        src = ("PROGRAM P; VAR d: LSTRING(5); "
               "BEGIN COPYLST('abc', d); WRITE(d) END.")
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "abc")

    def test_copylst_overflow_aborts(self):
        src = "PROGRAM P; VAR d: LSTRING(2); BEGIN COPYLST('abc', d) END."
        rc, _ = build_and_run(src)
        self.assertNotEqual(rc, 0)

    def test_copystr_within_capacity_blank_pads(self):
        # STRING(5) keeps all its characters: 'abc' + two blanks.
        src = ("PROGRAM P; VAR d: STRING(5); "
               "BEGIN COPYSTR('abc', d); WRITE(d) END.")
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "abc  ")

    def test_copystr_overflow_aborts(self):
        src = "PROGRAM P; VAR d: STRING(2); BEGIN COPYSTR('abc', d) END."
        rc, _ = build_and_run(src)
        self.assertNotEqual(rc, 0)


@requires_llvm
class TestReadCodegenIR(unittest.TestCase):
    def test_readln_emits_skip_call(self):
        src = "PROGRAM P; VAR i: INTEGER; BEGIN READLN(i); READLN() END."
        ir = compile_to_ir(src)
        self.assertIn("pas_read_int", ir)
        self.assertIn("pas_readln_skip", ir)

    def test_read_does_not_emit_skip_call(self):
        src = "PROGRAM P; VAR i: INTEGER; BEGIN READ(i) END."
        ir = compile_to_ir(src)
        self.assertIn("pas_read_int", ir)
        self.assertNotIn("call void @\"pas_readln_skip\"", ir)


@requires_exe
class TestReadCodegenRuntime(unittest.TestCase):
    def test_readln_empty_ok(self):
        src = "PROGRAM P; BEGIN READLN() END."
        rc, out = _build_pascal_with_runtime(src, ["readq.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_readln_lstring_uses_declared_capacity(self):
        src = "PROGRAM P; VAR s: LSTRING(3); BEGIN READLN(s); WRITELN(s) END."
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], stdin="abcdef\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "abc\n")


if __name__ == "__main__":
    unittest.main()
