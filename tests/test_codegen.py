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

    def test_variable_assignment(self):
        """Variable assignment generates valid IR."""
        src = "PROGRAM P; VAR x: INTEGER; BEGIN x := 42; WRITELN(x) END."
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

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
        """CONST and CONS parameters are readable by-reference aliases."""
        src = """
        PROGRAM P;
        VAR a, b: INTEGER;
        PROCEDURE Show(CONST x: INTEGER; CONS y: INTEGER);
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


if __name__ == '__main__':
    unittest.main()
