"""
Supplementary codegen tests.

Covers two gaps found while reviewing the string/UPPER-LOWER patch:

  1. String ASSIGNMENT lowering. The existing string codegen tests only
     declare STRING/LSTRING variables (empty body) or assign NULL. None of
     them assign an ordinary string literal to a STRING/LSTRING variable, so
     the "7.1 ... Proven by tests.test_codegen" claim was never actually
     exercising assignment. These tests do. They are expected to PASS:
     both sides lower to i8*, so assignment is a pointer store.

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

from tests.support import requires_llvm
from tests.test_codegen import compile_to_ir


@requires_llvm
class TestStringAssignmentCodegen(unittest.TestCase):
    """String literal assignment to STRING/LSTRING storage (expected PASS)."""

    def test_string_literal_assignment_lowers_to_store(self):
        """Assigning a literal to a STRING(n) var emits a store of the bytes."""
        src = "PROGRAM P; VAR a: STRING(10); BEGIN a := 'abc' END."
        ir = compile_to_ir(src)
        # The literal bytes are emitted as a private global constant ...
        self.assertIn("abc", ir)
        # ... and the assignment lowers to a store into the string slot.
        self.assertIn("store", ir)

    def test_lstring_literal_assignment_lowers_to_store(self):
        """Assigning a literal to an LSTRING(n) var emits a store of the bytes."""
        src = "PROGRAM P; VAR b: LSTRING(10); BEGIN b := 'hi' END."
        ir = compile_to_ir(src)
        self.assertIn("hi", ir)
        self.assertIn("store", ir)

    def test_string_literal_with_doubled_quote_assignment(self):
        """A doubled-quote literal stores its decoded byte (a'b), not the escape."""
        src = "PROGRAM P; VAR a: STRING(10); BEGIN a := 'a''b' END."
        ir = compile_to_ir(src)
        # Decoded content is the three bytes a ' b -- the doubled quote must
        # have collapsed to a single quote in the emitted constant.
        self.assertIn("a'b", ir)
        self.assertIn("store", ir)


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

        EXPECTED TO FAIL until the codegen UPPER/LOWER path uses
        eval_const_expr instead of reading `.value` off the bound node.
        The type checker already accepts this program, so the failure is a
        layer mismatch, not a user error. Asserting success (rather than
        assertRaises) keeps the bug encoded as a defect, not as intended.
        """
        src = ("PROGRAM P; CONST n = 10; VAR a: ARRAY[1..n] OF INTEGER; "
               "BEGIN WRITELN(UPPER(a)) END.")
        ir = compile_to_ir(src)  # currently raises CodegenError
        self.assertIn("10", ir)

    def test_lower_named_const_bound_resolves(self):
        """LOWER must resolve a named-constant lower bound (EXPECTED TO FAIL until fix)."""
        src = ("PROGRAM P; CONST lo = 2; VAR a: ARRAY[lo..10] OF INTEGER; "
               "BEGIN WRITELN(LOWER(a)) END.")
        ir = compile_to_ir(src)  # currently raises CodegenError
        self.assertIn("2", ir)


if __name__ == "__main__":
    unittest.main()
