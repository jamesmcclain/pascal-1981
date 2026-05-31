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


@requires_exe
class TestCodegenBuildRun(unittest.TestCase):
    """Test native executable build and run (requires llvmlite + clang)."""

    def test_writeln_integer(self):
        """WRITELN(integer) runs and produces correct output."""
        src = "PROGRAM P; BEGIN WRITELN(42) END."
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("42", stdout)

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

    def test_nested_arithmetic(self):
        """Nested arithmetic expressions."""
        src = (
            "PROGRAM P; "
            "BEGIN WRITELN((2 + 3) * 4) END."
        )
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("20", stdout)


if __name__ == '__main__':
    unittest.main()
