"""
Codegen test suite.

Tests LLVM IR generation and native executable build/run.
Requires llvmlite for IR generation and clang for native builds.

This module is the only place llvmlite and codegen_llvm are imported,
keeping the dependency isolated and optional.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from tests.support import requires_llvm, requires_exe, parse_source, typecheck_source


# Codegen helpers (only imported here, not in support.py)
def compile_to_ir(src: str) -> str:
    """
    Parse, type-check, then compile to LLVM IR.
    Returns the IR text as a string.
    Requires llvmlite.
    """
    from codegen_llvm import compile_to_llvm
    
    ast = parse_source(src)
    result = typecheck_source(src)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    
    return compile_to_llvm(ast)


def build_and_run(src: str, stdin: str = "") -> tuple:
    """
    Compile Pascal source to native executable, run it, capture output.
    
    Returns: (returncode: int, stdout: str)
    Requires llvmlite + clang.
    """
    from codegen_llvm import compile_to_llvm
    
    ast = parse_source(src)
    result = typecheck_source(src)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    
    ir = compile_to_llvm(ast)
    
    tmpdir = tempfile.mkdtemp()
    try:
        # Write IR to a temp .ll file
        ll_path = os.path.join(tmpdir, "prog.ll")
        with open(ll_path, 'w') as f:
            f.write(ir)
        
        # Compile to native executable
        exe_path = os.path.join(tmpdir, "prog")
        result = subprocess.run(
            ["clang", ll_path, "-o", exe_path],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"clang failed: {result.stderr}")
        
        # Run the executable
        run_result = subprocess.run(
            [exe_path],
            input=stdin,
            capture_output=True,
            text=True
        )
        return run_result.returncode, run_result.stdout
    finally:
        import shutil
        shutil.rmtree(tmpdir)


@requires_llvm
class TestCodegenIR(unittest.TestCase):
    """Test LLVM IR generation (requires llvmlite)."""

    def test_simple_writeln(self):
        """Simple WRITELN generates valid IR."""
        src = "PROGRAM P; BEGIN WRITELN(42) END."
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)
        # IR should contain some basic structure
        self.assertIn("define", ir)

    def test_predeclared_maxint_maxword_constants(self):
        """MAXINT and MAXWORD lower as folded constants."""
        src = "PROGRAM P; BEGIN WRITELN(MAXINT); WRITELN(MAXWORD) END."
        ir = compile_to_ir(src)
        self.assertIn("2147483647", ir)
        self.assertIn("65535", ir)

    def test_null_lowers_as_empty_string_pointer(self):
        """NULL lowers to a pointer to the empty LSTRING constant."""
        src = "PROGRAM P; VAR s: LSTRING(10); BEGIN s := NULL END."
        ir = compile_to_ir(src)
        self.assertIn("nullstr", ir)
        self.assertIn("i8*", ir)

    def test_pred_lowers_to_subtraction(self):
        """PRED lowers to integer subtraction by one."""
        src = "PROGRAM P; BEGIN WRITELN(PRED(3)) END."
        ir = compile_to_ir(src)
        self.assertIn("sub i32", ir)

    def test_sqr_lowers_to_multiply(self):
        """SQR lowers to multiplication of the operand by itself."""
        src = "PROGRAM P; BEGIN WRITELN(SQR(3)) END."
        ir = compile_to_ir(src)
        self.assertIn("mul i32", ir)

    def test_upper_lower_lowers_to_array_bounds(self):
        """UPPER and LOWER lower to constant array bound values."""
        src = "PROGRAM P; VAR a: ARRAY[1..10] OF INTEGER; BEGIN WRITELN(UPPER(a)); WRITELN(LOWER(a)) END."
        ir = compile_to_ir(src)
        self.assertIn("10", ir)
        self.assertIn("1", ir)

    def test_hibyte_lobyte_lowers_to_byte_extraction(self):
        """HIBYTE and LOBYTE lower to shifts and truncation."""
        src = "PROGRAM P; BEGIN WRITELN(HIBYTE(4660)); WRITELN(LOBYTE(4660)) END."
        ir = compile_to_ir(src)
        self.assertIn("lshr", ir)
        self.assertIn("trunc", ir)

    def test_trunc_lowers_to_fptosi(self):
        """TRUNC lowers to a direct fptosi (truncate-toward-zero) instruction."""
        src = "PROGRAM P; VAR x: INTEGER; BEGIN x := TRUNC(3.7) END."
        ir_text = compile_to_ir(src)
        self.assertIn("fptosi", ir_text)

    def test_round_lowers_to_half_adjust_then_fptosi(self):
        """ROUND lowers to a ±0.5 adjustment + fptosi (half-away-from-zero, no libm)."""
        src = "PROGRAM P; VAR x: INTEGER; BEGIN x := ROUND(1.6) END."
        ir_text = compile_to_ir(src)
        # The ±0.5 select-and-add pattern must appear, followed by fptosi
        self.assertIn("fadd", ir_text)
        self.assertIn("fptosi", ir_text)

    def test_variable_assignment(self):
        """Variable assignment generates valid IR."""
        src = "PROGRAM P; VAR x: INTEGER; BEGIN x := 42; WRITELN(x) END."
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_string_types_lower_as_byte_pointers(self):
        """STRING(n) and LSTRING(n) lower to inline aggregate storage."""
        src = "PROGRAM P; VAR a: STRING(10); VAR b: LSTRING(10); BEGIN END."
        ir = compile_to_ir(src)
        # STRING(10) lowers to [10 x i8], LSTRING(10) lowers to [11 x i8]
        self.assertIn("[10 x i8]", ir)
        self.assertIn("[11 x i8]", ir)

    def test_set_variable_uses_bitvector_storage(self):
        """SET variables lower to a fixed 256-bit bitvector."""
        src = "PROGRAM P; VAR x: SET OF 1..10; BEGIN END."
        ir = compile_to_ir(src)
        self.assertIn("global [4 x i64] zeroinitializer", ir)

    def test_set_constructor_constant_lowers_to_bitvector(self):
        """Constant set constructors fold into four-word set constants."""
        src = "PROGRAM P; TYPE S = SET OF 1..10; VAR x: S; BEGIN x := [1, 2..4] END."
        ir = compile_to_ir(src)
        self.assertIn("store [4 x i64] [i64 30, i64 0, i64 0, i64 0]", ir)

    def test_typed_set_constructor_lowers_to_bitvector(self):
        """Type-prefixed constant set constructors fold to set constants."""
        src = "PROGRAM P; TYPE S = SET OF 1..10; VAR x: S; BEGIN x := S[1..3] END."
        ir = compile_to_ir(src)
        self.assertIn("store [4 x i64] [i64 14, i64 0, i64 0, i64 0]", ir)

    def test_set_arithmetic_ops_lower_to_bitwise_ops(self):
        """Set +, -, and * lower to bitwise operations on set words."""
        src = "PROGRAM P; VAR a, b, c: SET OF 1..10; BEGIN a := [1]; b := [2]; c := a + b; c := c * a; c := c - b END."
        ir = compile_to_ir(src)
        self.assertIn(" or ", ir)
        self.assertIn(" and ", ir)
        self.assertIn(" xor ", ir)

    def test_set_membership_lowers_to_bit_test(self):
        """IN lowers to word selection, shift, mask, and compare."""
        src = "PROGRAM P; VAR a: SET OF 1..10; VAR ok: BOOLEAN; BEGIN a := [1, 3]; ok := 3 IN a END."
        ir = compile_to_ir(src)
        self.assertIn("udiv", ir)
        self.assertIn("urem", ir)
        self.assertIn("shl", ir)
        self.assertIn("icmp ne", ir)

    def test_set_comparisons_lower_to_boolean_logic(self):
        """Set comparisons lower to aggregate word comparisons."""
        src = "PROGRAM P; VAR a, b: SET OF 1..10; VAR ok: BOOLEAN; BEGIN a := [1]; b := [1, 2]; ok := a <= b; ok := a <> b END."
        ir = compile_to_ir(src)
        self.assertIn("icmp eq", ir)
        self.assertIn(" and ", ir)

    def test_real_literal_and_assignment(self):
        """REAL literals and assignment generate valid IR."""
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 1.5; WRITELN(x) END."
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertIn("double", ir)

    def test_arithmetic_expression(self):
        """Arithmetic expression generates valid IR."""
        src = "PROGRAM P; VAR x: INTEGER; BEGIN x := 1 + 2 * 3; WRITELN(x) END."
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_if_statement(self):
        """IF statement generates valid IR."""
        src = "PROGRAM P; BEGIN IF TRUE THEN WRITELN(1) END."
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_while_loop(self):
        """WHILE loop generates valid IR."""
        src = (
            "PROGRAM P; "
            "VAR x: INTEGER; "
            "BEGIN x := 0; WHILE x < 5 DO x := x + 1 END."
        )
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_for_loop(self):
        """FOR loop generates valid IR."""
        src = (
            "PROGRAM P; "
            "VAR i: INTEGER; "
            "BEGIN FOR i := 1 TO 5 DO WRITELN(i) END."
        )
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_for_static_loop_variable_uses_fixed_storage(self):
        """FOR STATIC uses fixed storage for the control variable."""
        src = (
            "PROGRAM P; "
            "VAR i: INTEGER; "
            "BEGIN FOR STATIC i := 1 TO 5 DO WRITELN(i) END."
        )
        ir = compile_to_ir(src)
        self.assertIn("__for_static", ir)
        self.assertIn("internal global", ir)

    def test_readonly_local_variable_is_emitted_as_immutable_storage(self):
        """READONLY variables should still codegen cleanly."""
        ir = compile_to_ir("PROGRAM P; VAR [READONLY] x: INTEGER; BEGIN WRITELN(x) END.")
        self.assertIn("x", ir)

    def test_procedure_call(self):
        """Procedure call generates valid IR."""
        src = (
            "PROGRAM P; "
            "PROCEDURE PrintOne; BEGIN WRITELN(1) END; "
            "BEGIN PrintOne END."
        )
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_function_call(self):
        """Function call generates valid IR."""
        src = (
            "PROGRAM P; "
            "FUNCTION Add(x: INTEGER; y: INTEGER): INTEGER; "
            "BEGIN Add := x + y END; "
            "BEGIN WRITELN(Add(2, 3)) END."
        )
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_real_function_parameter_and_return(self):
        """REAL function parameters and return values generate valid IR."""
        src = (
            "PROGRAM P; "
            "FUNCTION Twice(x: REAL): REAL; "
            "BEGIN Twice := x + x END; "
            "BEGIN WRITELN(Twice(1.5)) END."
        )
        ir = compile_to_ir(src)
        self.assertIn("double", ir)
        self.assertIn("Twice", ir)

    def test_abs_and_sqrt_generate_valid_ir(self):
        """ABS and SQRT generate valid IR for integer/real operands."""
        src = (
            "PROGRAM P; VAR x: REAL; BEGIN "
            "WRITELN(ABS(-5)); "
            "x := SQRT(9); "
            "WRITELN(x) END."
        )
        ir = compile_to_ir(src)
        self.assertIn("sqrt", ir)
        self.assertIn("double", ir)

    def test_short_circuit_generates_branching_ir(self):
        """AND THEN / OR ELSE lower to branch + PHI, not eager bitwise ops."""
        src = (
            "PROGRAM P; VAR a, b: BOOLEAN; BEGIN "
            "a := TRUE; b := FALSE; "
            "IF a AND THEN b THEN WRITELN(1); "
            "IF a OR ELSE b THEN WRITELN(2) END."
        )
        ir = compile_to_ir(src)
        self.assertIn("sc_rhs", ir)
        self.assertIn("sc_merge", ir)
        self.assertIn("sc_result", ir)


@requires_exe
class TestCodegenBuildRun(unittest.TestCase):
    """Test native executable build and run (requires llvmlite + clang)."""

    def test_writeln_integer(self):
        """WRITELN(integer) runs and produces correct output."""
        src = "PROGRAM P; BEGIN WRITELN(42) END."
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("42", stdout)

    def test_real_assignment_and_output(self):
        """REAL assignment and output runs and produces correct output."""
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 1.5; WRITELN(x) END."
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("1.5", stdout)

    def test_real_function_parameter_and_return(self):
        """REAL function parameter and return values run and produce output."""
        src = (
            "PROGRAM P; "
            "FUNCTION Twice(x: REAL): REAL; "
            "BEGIN Twice := x + x END; "
            "BEGIN WRITELN(Twice(1.5)) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("3", stdout)

    def test_abs_and_sqrt_run(self):
        """ABS and SQRT run and produce correct output."""
        src = (
            "PROGRAM P; VAR x: REAL; BEGIN "
            "WRITELN(ABS(-5)); "
            "x := SQRT(9); "
            "WRITELN(x) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("5", stdout)
        self.assertIn("3", stdout)

    def test_trunc_run(self):
        """TRUNC truncates toward zero (not floor) for both positive and negative reals."""
        src = (
            "PROGRAM P; VAR i: INTEGER; BEGIN "
            "i := TRUNC(3.7); WRITELN(i); "
            "i := TRUNC(-3.7); WRITELN(i) "
            "END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        lines = stdout.strip().splitlines()
        self.assertEqual(lines[0].strip(), "3")    # truncate positive
        self.assertEqual(lines[1].strip(), "-3")   # truncate toward zero, NOT floor (-4)

    def test_round_run(self):
        """ROUND rounds away from zero (IBM Pascal manual 11-7)."""
        src = (
            "PROGRAM P; VAR i: INTEGER; BEGIN "
            "i := ROUND(2.4); WRITELN(i); "
            "i := ROUND(1.6); WRITELN(i); "
            "i := ROUND(-1.6); WRITELN(i); "
            "i := ROUND(3.5); WRITELN(i); "
            "i := ROUND(-3.5); WRITELN(i) "
            "END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        lines = stdout.strip().splitlines()
        self.assertEqual(lines[0].strip(), "2")    # round down
        self.assertEqual(lines[1].strip(), "2")    # round up
        self.assertEqual(lines[2].strip(), "-2")   # round toward zero (away from zero magnitude)
        self.assertEqual(lines[3].strip(), "4")    # tie: away from zero
        self.assertEqual(lines[4].strip(), "-4")   # tie: away from zero

    def test_nil_codegen(self):
        """NIL lowers to a null pointer value."""
        src = "PROGRAM P; VAR p: ^INTEGER; BEGIN p := NIL END."
        ir = compile_to_ir(src)
        self.assertIn("null", ir)

    def test_ads_codegen_uses_pointer_plus_zero_segment(self):
        """ADS lowers as an address pair: R is the LLVM pointer, S is zero."""
        src = "PROGRAM P; VAR x: INTEGER; s: ADS OF INTEGER; BEGIN s := ADS x END."
        ir = compile_to_ir(src)
        self.assertIn("{i32*,i16}", ir.replace(" ", ""))
        self.assertIn("i16 0", ir)

    def test_short_circuit_skips_rhs_runtime(self):
        """Short-circuit operators must not evaluate an unnecessary RHS call."""
        src = (
            "PROGRAM P; "
            "FUNCTION Bad: BOOLEAN; BEGIN WRITELN(99); Bad := TRUE END; "
            "BEGIN "
            "IF FALSE AND THEN Bad() THEN WRITELN(1); "
            "IF TRUE OR ELSE Bad() THEN WRITELN(2) "
            "END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertNotIn("99", stdout)
        self.assertNotIn("1", stdout)
        self.assertIn("2", stdout)

    def test_simple_arithmetic(self):
        """Simple arithmetic: 2 + 3 = 5."""
        src = "PROGRAM P; BEGIN WRITELN(2 + 3) END."
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("5", stdout)

    def test_set_operations_runtime(self):
        """Set union/intersection/difference and IN produce correct runtime results."""
        src = """
        PROGRAM P;
        VAR a, b, c: SET OF 1..10;
        BEGIN
            a := [1, 3];
            b := [3, 4];
            c := a + b;
            IF 4 IN c THEN WRITELN(1);
            c := a * b;
            IF 3 IN c THEN WRITELN(2);
            c := a - b;
            IF 3 IN c THEN WRITELN(9);
            IF 1 IN c THEN WRITELN(3)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "1\n2\n3")

    def test_typed_set_constructor_runtime(self):
        """Type-prefixed set constants execute through the set backend."""
        src = """
        PROGRAM P;
        TYPE S = SET OF 1..10;
        VAR a: S;
        BEGIN
            a := S[2..4];
            IF 2 IN a THEN WRITELN(2);
            IF 5 IN a THEN WRITELN(5);
            IF a = S[2..4] THEN WRITELN(4)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "2\n4")

    def test_set_dynamic_element_runtime(self):
        """Set constructors with runtime element values build the right set."""
        src = """
        PROGRAM P;
        VAR s: SET OF 0..31;
        VAR i: INTEGER;
        BEGIN
            i := 3;
            s := [i, 5, 10];
            IF 3 IN s THEN WRITELN(3);
            IF 5 IN s THEN WRITELN(5);
            IF 4 IN s THEN WRITELN(99)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "3\n5")

    def test_set_dynamic_range_runtime(self):
        """Set constructors with runtime range bounds set the whole span; reversed range is empty."""
        src = """
        PROGRAM P;
        VAR s: SET OF 0..31;
        VAR i, lo, hi, cnt: INTEGER;
        BEGIN
            lo := 4; hi := 8;
            s := [lo..hi, 20];
            cnt := 0;
            FOR i := 0 TO 31 DO IF i IN s THEN cnt := cnt + 1;
            WRITELN(cnt);
            lo := 9; hi := 2;
            s := [lo..hi];
            cnt := 0;
            FOR i := 0 TO 31 DO IF i IN s THEN cnt := cnt + 1;
            WRITELN(cnt)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "6\n0")

    def test_char_set_membership_runtime(self):
        """CHAR-based sets honor element values (regression: char literals kept quotes)."""
        src = """
        PROGRAM P;
        VAR s: SET OF 'A'..'Z';
        VAR c: CHAR;
        VAR cnt: INTEGER;
        BEGIN
            c := 'B';
            s := [c, 'D'];
            cnt := 0;
            IF 'B' IN s THEN cnt := cnt + 1;
            IF 'C' IN s THEN cnt := cnt + 100;
            IF 'D' IN s THEN cnt := cnt + 1000;
            WRITELN(cnt)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "1001")

    def test_enum_set_membership_runtime(self):
        """SET OF an enum type lowers members to ordinals and tests membership."""
        src = """
        PROGRAM P;
        TYPE Color = (Red, Green, Blue, Yellow);
        VAR s: SET OF Color;
        VAR c: Color;
        VAR cnt: INTEGER;
        BEGIN
            c := Red;
            s := [c, Blue];
            cnt := 0;
            IF Red IN s THEN cnt := cnt + 1;
            IF Green IN s THEN cnt := cnt + 100;
            IF Blue IN s THEN cnt := cnt + 1000;
            WRITELN(cnt)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "1001")

    def test_variable_assignment_and_output(self):
        """Assign to variable and output."""
        src = (
            "PROGRAM P; "
            "VAR x: INTEGER; "
            "BEGIN x := 10; WRITELN(x) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("10", stdout)

    def test_for_loop_output(self):
        """FOR loop outputs 1 to 3."""
        src = (
            "PROGRAM P; "
            "VAR i: INTEGER; "
            "BEGIN FOR i := 1 TO 3 DO WRITELN(i) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        # Output should contain 1, 2, 3 (possibly with newlines)
        self.assertIn("1", stdout)
        self.assertIn("2", stdout)
        self.assertIn("3", stdout)

    def test_procedure_with_parameter(self):
        """Procedure with parameter compiles and runs."""
        src = (
            "PROGRAM P; "
            "PROCEDURE Print(x: INTEGER); BEGIN WRITELN(x) END; "
            "BEGIN Print(99) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("99", stdout)

    def test_integer_to_real_procedure_parameter(self):
        """INTEGER actual coerces into REAL procedure parameter at codegen."""
        src = (
            "PROGRAM P; "
            "PROCEDURE PrintReal(x: REAL); BEGIN WRITELN(x) END; "
            "BEGIN PrintReal(7) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("7", stdout)

    # ------------------------------------------------------------------
    # 9.1 REAL hardening tests
    # ------------------------------------------------------------------

    def test_integer_slash_produces_real(self):
        """INTEGER / INTEGER (SLASH) always yields a REAL result (not truncated int div)."""
        src = "PROGRAM P; VAR i: INTEGER; BEGIN i := 7; WRITELN(i / 2) END."
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        # 7 / 2 must be 3.5, not 3
        self.assertIn("3.5", stdout)

    def test_integer_literal_slash_produces_real(self):
        """Literal INTEGER / INTEGER yields REAL at compile time."""
        src = "PROGRAM P; BEGIN WRITELN(1 / 4) END."
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("0.25", stdout)

    def test_real_const_declaration_and_use(self):
        """REAL CONST can be declared and used in expressions without crashing."""
        src = (
            "PROGRAM P; "
            "CONST PI = 3.14159; "
            "VAR r: REAL; "
            "BEGIN r := PI; WRITELN(r) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("3.14", stdout)

    def test_negative_real_const(self):
        """Unary minus applied to a REAL constant generates valid IR and correct output."""
        src = (
            "PROGRAM P; "
            "CONST NEGPI = -3.14159; "
            "VAR r: REAL; "
            "BEGIN r := NEGPI; WRITELN(r) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("-3.14", stdout)

    def test_real_const_in_expression(self):
        """REAL constant participates in arithmetic expressions correctly."""
        src = (
            "PROGRAM P; "
            "CONST TWO = 2.0; NEGPI = -3.14159; "
            "FUNCTION Scale(x: REAL): REAL; "
            "BEGIN Scale := x * TWO + NEGPI END; "
            "BEGIN WRITELN(Scale(1.0) : 10 : 4) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        # 1.0 * 2.0 + (-3.14159) = -1.14159
        self.assertIn("-1.1416", stdout)

    def test_unary_minus_real_variable(self):
        """Unary minus on a REAL variable generates valid IR (not integer neg)."""
        src = (
            "PROGRAM P; VAR x: REAL; "
            "BEGIN x := 2.5; WRITELN(-x) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("-2.5", stdout)

    def test_real_comparison_produces_boolean(self):
        """REAL comparisons evaluate and branch correctly."""
        src = (
            "PROGRAM P; CONST NEGPI = -3.14159; "
            "BEGIN "
            "IF NEGPI < 0.0 THEN WRITELN(1) ELSE WRITELN(0); "
            "IF 0.5 = 0.5 THEN WRITELN(1) ELSE WRITELN(0) "
            "END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        lines = stdout.strip().split()
        self.assertEqual(lines, ['1', '1'])

    def test_mixed_int_real_arithmetic(self):
        """Mixed INTEGER and REAL operands widen to REAL correctly."""
        src = (
            "PROGRAM P; VAR x: REAL; i: INTEGER; "
            "BEGIN x := 3.0; i := 7; WRITELN(x + i : 8 : 2) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("10.00", stdout)

    def test_nested_arithmetic(self):
        """Nested arithmetic expressions."""
        src = (
            "PROGRAM P; "
            "BEGIN WRITELN((2 + 3) * 4) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("20", stdout)

    def test_unlabeled_break_runtime(self):
        """BREAK exits the nearest enclosing loop."""
        src = """
        PROGRAM P;
        VAR i: INTEGER;
        BEGIN
            i := 0;
            WHILE TRUE DO
            BEGIN
                i := i + 1;
                IF i = 3 THEN BREAK
            END;
            WRITELN(i)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "3")

    def test_unlabeled_cycle_runtime(self):
        """CYCLE skips to the next nearest loop iteration."""
        src = """
        PROGRAM P;
        VAR i, sum: INTEGER;
        BEGIN
            sum := 0;
            FOR i := 1 TO 3 DO
            BEGIN
                IF i = 2 THEN CYCLE;
                sum := sum + i
            END;
            WRITELN(sum)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "4")

    def test_labeled_break_runtime(self):
        """BREAK label exits the enclosing loop with that statement label."""
        src = """
        PROGRAM P;
        LABEL OUTER;
        VAR i, j: INTEGER;
        BEGIN
            i := 0;
            OUTER: WHILE i < 3 DO
            BEGIN
                i := i + 1;
                j := 0;
                WHILE j < 3 DO
                BEGIN
                    j := j + 1;
                    IF j = 2 THEN BREAK OUTER
                END
            END;
            WRITELN(i);
            WRITELN(j)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "1\n2")

    def test_labeled_cycle_runtime(self):
        """CYCLE label continues the enclosing loop with that statement label."""
        src = """
        PROGRAM P;
        LABEL OUTER;
        VAR i, j, sum: INTEGER;
        BEGIN
            sum := 0;
            OUTER: FOR i := 1 TO 3 DO
            BEGIN
                FOR j := 1 TO 3 DO
                BEGIN
                    IF j = 2 THEN CYCLE OUTER;
                    sum := sum + 10 * i + j
                END
            END;
            WRITELN(sum)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "63")

    def test_var_and_vars_parameters_runtime(self):
        """VAR and VARS parameters are writable by-reference aliases."""
        src = """
        PROGRAM P;
        VAR a, b: INTEGER;
        PROCEDURE Bump(VAR x: INTEGER; VARS y: INTEGER);
        BEGIN
            x := x + 1;
            y := y + 10
        END;
        BEGIN
            a := 1;
            b := 2;
            Bump(a, b);
            WRITELN(a);
            WRITELN(b)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "2\n12")

    def test_const_and_cons_parameters_runtime(self):
        """CONST and CONSTS parameters are readable by-reference aliases."""
        src = """
        PROGRAM P;
        VAR a, b: INTEGER;
        PROCEDURE Show(CONST x: INTEGER; CONSTS y: INTEGER);
        BEGIN
            WRITELN(x + y)
        END;
        BEGIN
            a := 7;
            b := 8;
            Show(a, b)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "15")

    def test_string_concat_runtime(self):
        """CONCAT appends a string to an LSTRING."""
        src = """
        PROGRAM P;
        VAR
            dest: LSTRING(20);
        BEGIN
            dest := 'hello';
            CONCAT(dest, ' world');
            WRITELN(dest)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "hello world")

    def test_string_copylst_runtime(self):
        """COPYLST copies a STRING to an LSTRING."""
        src = """
        PROGRAM P;
        VAR
            src_str: STRING(10);
            dest_lstr: LSTRING(20);
        BEGIN
            src_str := 'pascal';
            COPYLST(src_str, dest_lstr);
            WRITELN(dest_lstr)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "pascal")

    def test_string_copystr_runtime(self):
        """COPYSTR copies a STRING to a STRING and space-pads it."""
        src = """
        PROGRAM P;
        VAR
            src_str: STRING(5);
            dest_str: STRING(10);
        BEGIN
            src_str := 'abc';
            COPYSTR(src_str, dest_str);
            WRITELN(dest_str)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        # dest_str should be 'abc' followed by 7 spaces
        self.assertEqual(stdout.rstrip('\r\n'), "abc       ")


if __name__ == '__main__':
    unittest.main()
