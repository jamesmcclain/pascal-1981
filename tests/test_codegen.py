"""
Codegen test suite.

Tests LLVM IR generation and native executable build/run.
Requires llvmlite for IR generation and clang for native builds.

This module is the only place llvmlite and codegen_llvm are imported,
keeping the dependency isolated and optional.
"""

import glob
import os
import subprocess
import sys
import tempfile
import unittest

from tests.support import (parse_source, requires_exe, requires_llvm, typecheck_source)


# Codegen helpers (only imported here, not in support.py)
def compile_to_ir(src: str, features=None) -> str:
    """
    Parse, type-check, then compile to LLVM IR.
    Returns the IR text as a string.
    Requires llvmlite.
    """
    from pascal1981.codegen_llvm import compile_to_llvm
    from pascal1981.type_checker import PascalTypeChecker

    ast = parse_source(src)
    checker = PascalTypeChecker(features=features)
    result = checker.check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")

    return compile_to_llvm(ast, features=features)


def build_and_run(src: str, stdin: str = "", features=None) -> tuple:
    """
    Compile Pascal source to native executable, run it, capture output.
    
    Returns: (returncode: int, stdout: str)
    Requires llvmlite + clang.
    """
    from pascal1981.codegen_llvm import compile_to_llvm
    from pascal1981.type_checker import PascalTypeChecker

    ast = parse_source(src)
    checker = PascalTypeChecker(features=features)
    result = checker.check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")

    ir = compile_to_llvm(ast, features=features)

    tmpdir = tempfile.mkdtemp()
    try:
        # Write IR to a temp .ll file
        ll_path = os.path.join(tmpdir, "prog.ll")
        with open(ll_path, 'w') as f:
            f.write(ir)

        # Compile to native executable
        exe_path = os.path.join(tmpdir, "prog")
        result = subprocess.run(["clang", ll_path, "-o", exe_path, "-lm"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"clang failed: {result.stderr}")

        # Run the executable
        run_result = subprocess.run([exe_path], input=stdin, capture_output=True, text=True)
        return run_result.returncode, run_result.stdout
    finally:
        import shutil
        shutil.rmtree(tmpdir)


@requires_llvm
class TestCodegenIR(unittest.TestCase):
    """Test LLVM IR generation (requires llvmlite)."""

    def test_file_buffer_model_ir(self):
        """FILE OF T lowers to an inline file-control block plus typed F^ buffer
        access through pas_file_buffer; no heap allocation (so nothing leaks)."""
        src = "PROGRAM P; VAR f: FILE OF INTEGER; x: INTEGER; BEGIN f^ := 42; x := f^ END."
        ir = compile_to_ir(src)
        self.assertIn('declare external i8* @"pas_file_buffer"', ir)
        # The control block and its buffer are stack/global allocations, not malloc.
        self.assertNotIn('@"pas_file_create"', ir)
        self.assertNotIn('call i8* @"malloc"', ir)
        # F^ resolves to a typed pointer into the component buffer.
        self.assertIn('bitcast i8*', ir)
        self.assertIn('to i16*', ir)
        # Binary FILE OF T records structure 0 in the FCB.
        self.assertIn('store i32 0', ir)

    def test_text_buffer_touch_and_predeclared_files_ir(self):
        """TEXT/INPUT/OUTPUT are ASCII file handles; TEXT^ goes through the touch
        hook, which now performs real bookkeeping (sets the touched flag) rather
        than being an empty body."""
        src = "PROGRAM P; VAR t: TEXT; c: CHAR; BEGIN c := t^ END."
        ir = compile_to_ir(src)
        self.assertIn('@"input" = global i8* null', ir)
        self.assertIn('@"output" = global i8* null', ir)
        self.assertIn('declare external void @"pas_file_touch_buffer"', ir)
        self.assertIn('call void @"pas_file_touch_buffer"', ir)
        # ASCII/TEXT records structure 1 in the FCB.
        self.assertIn('store i32 1', ir)
        # No per-file heap allocation.
        self.assertNotIn('@"pas_file_create"', ir)

    def test_codegen_error_imported_for_expr_failures(self):
        """Codegen expression errors should raise CodegenError, not NameError."""
        from pascal1981.codegen import CodegenError
        from pascal1981.codegen_llvm import compile_to_llvm

        src = ("PROGRAM P; VAR i: INTEGER; "
               "BEGIN FOR i := 1 TO 1 DO IF MissingName = 1 THEN WRITELN(i) END.")
        ast = parse_source(src)
        with self.assertRaisesRegex(CodegenError, "Undefined variable: MissingName"):
            compile_to_llvm(ast)

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
        self.assertIn("32767", ir)
        self.assertIn("65535", ir)

    def test_predeclared_fillc_works_with_extern_declaration(self):
        """FILLC should work both as a predeclared extern and when declared extern in source."""
        src = ("PROGRAM P; "
               "PROCEDURE fillc (loc: ADRMEM; len: WORD; val: CHAR); extern; "
               "VAR buf: ARRAY[1..4] OF CHAR; "
               "BEGIN FILLC(ADR buf, WRD(4), 'X') END.")
        ir = compile_to_ir(src)
        self.assertIn("fillc", ir.lower())
        self.assertIn("external", ir.lower())

    def test_predeclared_fillsc_works_with_extern_declaration(self):
        """FILLSC is the segmented sibling of FILLC: it takes ADSMEM (not ADRMEM)
        and must work both predeclared and when declared extern in source."""
        src = ("PROGRAM P; "
               "PROCEDURE fillsc (loc: ADSMEM; len: WORD; val: CHAR); extern; "
               "VAR buf: ARRAY[1..4] OF CHAR; "
               "BEGIN FILLSC(ADS buf, WRD(4), 'X') END.")
        ir = compile_to_ir(src)
        self.assertIn("fillsc", ir.lower())
        self.assertIn("external", ir.lower())

    def test_predeclared_movel_works_with_extern_declaration(self):
        """MOVEL should work both as a predeclared extern and when declared extern in source."""
        src = ("PROGRAM P; "
               "PROCEDURE movel (src, dst: ADRMEM; len: WORD); extern; "
               "VAR buf: ARRAY[1..4] OF CHAR; "
               "BEGIN MOVEL(ADR buf, ADR buf, WRD(4)) END.")
        ir = compile_to_ir(src)
        self.assertIn("movel", ir.lower())
        self.assertIn("external", ir.lower())

    def test_predeclared_mover_works_with_extern_declaration(self):
        """MOVER should work both as a predeclared extern and when declared extern in source."""
        src = ("PROGRAM P; "
               "PROCEDURE mover (src, dst: ADRMEM; len: WORD); extern; "
               "VAR buf: ARRAY[1..4] OF CHAR; "
               "BEGIN MOVER(ADR buf, ADR buf, WRD(4)) END.")
        ir = compile_to_ir(src)
        self.assertIn("mover", ir.lower())
        self.assertIn("external", ir.lower())

    def test_predeclared_movesl_works_with_extern_declaration(self):
        """MOVESL is the segmented sibling of MOVEL: it takes ADSMEM (not ADRMEM)."""
        src = ("PROGRAM P; "
               "PROCEDURE movesl (src, dst: ADSMEM; len: WORD); extern; "
               "VAR buf: ARRAY[1..4] OF CHAR; "
               "BEGIN MOVESL(ADS buf, ADS buf, WRD(4)) END.")
        ir = compile_to_ir(src)
        self.assertIn("movesl", ir.lower())
        self.assertIn("external", ir.lower())

    def test_predeclared_movesr_works_with_extern_declaration(self):
        """MOVESR is the segmented sibling of MOVER: it takes ADSMEM (not ADRMEM)."""
        src = ("PROGRAM P; "
               "PROCEDURE movesr (src, dst: ADSMEM; len: WORD); extern; "
               "VAR buf: ARRAY[1..4] OF CHAR; "
               "BEGIN MOVESR(ADS buf, ADS buf, WRD(4)) END.")
        ir = compile_to_ir(src)
        self.assertIn("movesr", ir.lower())
        self.assertIn("external", ir.lower())

    def test_predeclared_abort_generates_abort_call(self):
        """ABORT should lower to the runtime abort handler, carrying the message,
        error code, and status (manual: CONST STRING, WORD, WORD)."""
        ir = compile_to_ir("PROGRAM P; VAR s: STRING(4); BEGIN s := 'oops'; ABORT(s, WRD(3), WRD(7)) END.")
        self.assertIn("pabort", ir.lower())
        # void pabort(i8*, i32, i16, i16): message pointer + length + two words.
        self.assertIn("call void @\"pabort\"", ir)

    def test_predeclared_move_fill_callable_without_source_declaration(self):
        """The section-5 builtins must lower when called WITHOUT a source `extern`
        declaration (the whole point of predeclaring them). The flat variants
        (FILLC/MOVEL/MOVER) take ADRMEM addresses; the segmented siblings
        (FILLSC/MOVESL/MOVESR) take ADSMEM (ADS) addresses. Each should emit a
        call to its lowercase runtime extern, with the array address bitcast to
        i8* (directly for the flat variants, inside the segment pair for the
        segmented ones) and a WORD length."""
        segmented = {"FILLSC", "MOVESL", "MOVESR"}
        for proc, fn in (("FILLC", "fillc"), ("FILLSC", "fillsc"), ("MOVEL", "movel"), ("MOVER", "mover"), ("MOVESL", "movesl"), ("MOVESR", "movesr")):
            with self.subTest(proc=proc):
                addr = "ADS" if proc in segmented else "ADR"
                if proc.startswith("FILL"):
                    src = (f"PROGRAM P; VAR buf: ARRAY[1..4] OF CHAR; "
                           f"BEGIN {proc}({addr} buf, WRD(4), 'X') END.")
                else:
                    src = (f"PROGRAM P; VAR a, b: ARRAY[1..4] OF CHAR; "
                           f"BEGIN {proc}({addr} a, {addr} b, WRD(4)) END.")
                ir = compile_to_ir(src)
                self.assertIn(f"call i32 @\"{fn}\"", ir)
                self.assertIn("bitcast [4 x i8]*", ir)

    def test_null_lowers_as_empty_string_pointer(self):
        """NULL lowers to a pointer to the empty LSTRING constant."""
        src = "PROGRAM P; VAR s: LSTRING(10); BEGIN s := NULL END."
        ir = compile_to_ir(src)
        self.assertIn("nullstr", ir)
        self.assertIn("i8*", ir)

    @requires_exe
    def test_null_lstring_len_and_empty_write_runtime(self):
        """D-033: NULL assigns an empty LSTRING and LSTRING.LEN reads as zero."""
        src = """PROGRAM P;
VAR l: LSTRING(5);
BEGIN
  l := NULL;
  WRITELN(ORD(l.LEN));
  WRITELN('<', l, '>')
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "0\n<>\n")

    def test_pred_lowers_to_subtraction(self):
        """PRED lowers to integer subtraction by one."""
        src = "PROGRAM P; BEGIN WRITELN(PRED(3)) END."
        ir = compile_to_ir(src)
        self.assertIn("sub i16", ir)

    def test_sqr_lowers_to_multiply(self):
        """SQR lowers to multiplication of the operand by itself."""
        src = "PROGRAM P; BEGIN WRITELN(SQR(3)) END."
        ir = compile_to_ir(src)
        self.assertIn("mul i16", ir)

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

    def test_float_lowers_to_sitofp(self):
        """FLOAT lowers to a sitofp conversion."""
        src = "PROGRAM P; VAR x: REAL; BEGIN x := FLOAT(42) END."
        ir_text = compile_to_ir(src)
        self.assertIn("sitofp", ir_text)

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
        src = ("PROGRAM P; "
               "VAR x: INTEGER; "
               "BEGIN x := 0; WHILE x < 5 DO x := x + 1 END.")
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_for_loop(self):
        """FOR loop generates valid IR."""
        src = ("PROGRAM P; "
               "VAR i: INTEGER; "
               "BEGIN FOR i := 1 TO 5 DO WRITELN(i) END.")
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_for_static_loop_variable_uses_fixed_storage(self):
        """FOR STATIC uses fixed storage for the control variable."""
        src = ("PROGRAM P; "
               "VAR i: INTEGER; "
               "BEGIN FOR STATIC i := 1 TO 5 DO WRITELN(i) END.")
        ir = compile_to_ir(src)
        self.assertIn("__for_static", ir)
        self.assertIn("internal global", ir)

    def test_readonly_local_variable_is_emitted_as_immutable_storage(self):
        """READONLY variables should still codegen cleanly."""
        ir = compile_to_ir("PROGRAM P; VAR [READONLY] x: INTEGER; BEGIN WRITELN(x) END.")
        self.assertIn("x", ir)

    def test_procedure_call(self):
        """Procedure call generates valid IR."""
        src = ("PROGRAM P; "
               "PROCEDURE PrintOne; BEGIN WRITELN(1) END; "
               "BEGIN PrintOne END.")
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_function_call(self):
        """Function call generates valid IR."""
        src = ("PROGRAM P; "
               "FUNCTION Add(x: INTEGER; y: INTEGER): INTEGER; "
               "BEGIN Add := x + y END; "
               "BEGIN WRITELN(Add(2, 3)) END.")
        ir = compile_to_ir(src)
        self.assertIsInstance(ir, str)
        self.assertGreater(len(ir), 0)

    def test_real_function_parameter_and_return(self):
        """REAL function parameters and return values generate valid IR."""
        src = ("PROGRAM P; "
               "FUNCTION Twice(x: REAL): REAL; "
               "BEGIN Twice := x + x END; "
               "BEGIN WRITELN(Twice(1.5)) END.")
        ir = compile_to_ir(src)
        self.assertIn("double", ir)
        self.assertIn("Twice", ir)

    def test_abs_and_sqrt_generate_valid_ir(self):
        """ABS, SQRT, and other libm math functions generate valid IR."""
        src = ("PROGRAM P; VAR x: REAL; BEGIN "
               "WRITELN(ABS(-5)); "
               "x := SQRT(9) + SIN(1) + COS(1) + LN(2) + EXP(1) + ARCTAN(1); "
               "WRITELN(x) END.")
        ir = compile_to_ir(src)
        self.assertIn("sqrt", ir)
        self.assertIn("sin", ir)
        self.assertIn("cos", ir)
        self.assertIn("log", ir)
        self.assertIn("exp", ir)
        self.assertIn("atan", ir)
        self.assertIn("double", ir)

    def test_short_circuit_generates_branching_ir(self):
        """AND THEN / OR ELSE lower to branch + PHI, not eager bitwise ops."""
        src = ("PROGRAM P; VAR a, b: BOOLEAN; BEGIN "
               "a := TRUE; b := FALSE; "
               "IF a AND THEN b THEN WRITELN(1); "
               "IF a OR ELSE b THEN WRITELN(2) END.")
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

    def test_duplicate_else_runtime_matches_vintage_d003(self):
        """D-003: duplicate $ELSE in a true branch resumes at the second else."""
        src = """PROGRAM P;
BEGIN
  {$IF 1 $THEN}
  WRITELN('A')
  {$ELSE}
  ;WRITELN('B')
  {$ELSE}
  ;WRITELN('C')
  {$END}
END."""
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "A\nC\n")

    def test_trapped_reset_missing_file_records_errs_d012(self):
        """D-012: trapped RESET on a missing named file records vintage F.ERRS=10."""
        src = """PROGRAM P;
VAR f: TEXT;
BEGIN
  ASSIGN(f, 'NOFILE.XYZ');
  f.TRAP := TRUE;
  RESET(f);
  WRITELN(f.ERRS)
END."""
        ir = compile_to_ir(src)
        tmpdir = tempfile.mkdtemp()
        try:
            ll_path = os.path.join(tmpdir, "prog.ll")
            exe_path = os.path.join(tmpdir, "prog")
            with open(ll_path, "w") as f:
                f.write(ir)
            repo = os.path.dirname(os.path.dirname(__file__))
            runtime_sources = glob.glob(os.path.join(repo, "runtime", "*.c"))
            clang = subprocess.run(["clang", ll_path, *runtime_sources, "-o", exe_path, "-lm", "-w"], capture_output=True, text=True)
            self.assertEqual(clang.returncode, 0, msg=clang.stderr)
            run = subprocess.run([exe_path], cwd=tmpdir, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout, "10\n")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_trapped_malformed_file_read_records_errs_d013(self):
        """D-013: malformed formatted READ is trapped through F.TRAP/F.ERRS."""
        src = """PROGRAM P;
VAR f: TEXT; i: INTEGER;
BEGIN
  ASSIGN(f, 'T013.DAT'); REWRITE(f); WRITELN(f, 'XYZ'); CLOSE(f);
  RESET(f); f.TRAP := TRUE;
  READ(f, i);
  WRITELN('AFTER');
  WRITELN(f.ERRS)
END."""
        ir = compile_to_ir(src)
        tmpdir = tempfile.mkdtemp()
        try:
            ll_path = os.path.join(tmpdir, "prog.ll")
            exe_path = os.path.join(tmpdir, "prog")
            with open(ll_path, "w") as f:
                f.write(ir)
            repo = os.path.dirname(os.path.dirname(__file__))
            runtime_sources = glob.glob(os.path.join(repo, "runtime", "*.c"))
            clang = subprocess.run(["clang", ll_path, *runtime_sources, "-o", exe_path, "-lm", "-w"], capture_output=True, text=True)
            self.assertEqual(clang.returncode, 0, msg=clang.stderr)
            run = subprocess.run([exe_path], cwd=tmpdir, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout, "AFTER\n14\n")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_untrapped_malformed_file_read_still_aborts(self):
        """Malformed formatted READ remains fatal when F.TRAP is not set."""
        src = """PROGRAM P;
VAR f: TEXT; i: INTEGER;
BEGIN
  ASSIGN(f, 'T013B.DAT'); REWRITE(f); WRITELN(f, 'XYZ'); CLOSE(f);
  RESET(f);
  READ(f, i);
  WRITELN('AFTER')
END."""
        ir = compile_to_ir(src)
        tmpdir = tempfile.mkdtemp()
        try:
            ll_path = os.path.join(tmpdir, "prog.ll")
            exe_path = os.path.join(tmpdir, "prog")
            with open(ll_path, "w") as f:
                f.write(ir)
            repo = os.path.dirname(os.path.dirname(__file__))
            runtime_sources = glob.glob(os.path.join(repo, "runtime", "*.c"))
            clang = subprocess.run(["clang", ll_path, *runtime_sources, "-o", exe_path, "-lm", "-w"], capture_output=True, text=True)
            self.assertEqual(clang.returncode, 0, msg=clang.stderr)
            run = subprocess.run([exe_path], cwd=tmpdir, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(run.returncode, 0)
            self.assertNotIn("AFTER", run.stdout)
            self.assertIn("malformed integer input", run.stderr)
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_value_empty_set_and_set_arithmetic_runtime(self):
        """Lesson5-shaped VALUE [] plus set arithmetic compiles and runs."""
        src = """PROGRAM Lesson5;
TYPE
  StatusEffect = (Poisoned, Shielded, Stunned, Enraged, Hasted);
  StatusSet = SET OF StatusEffect;
VAR
  ActiveEffects: StatusSet;
  CombatFilter: StatusSet;
  ResultEffects: StatusSet;
VALUE
  ActiveEffects := [];
BEGIN
  WRITELN('--- Dojo Status Set Training ---');
  ActiveEffects := ActiveEffects + [Poisoned, Enraged];
  IF Poisoned IN ActiveEffects THEN WRITELN('Alert: Fighter is taking damage over time!');
  ActiveEffects := ActiveEffects - [Poisoned];
  IF NOT (Poisoned IN ActiveEffects) THEN WRITELN('Success: Poison cleared.');
  CombatFilter := [Poisoned, Stunned];
  ResultEffects := ActiveEffects * CombatFilter;
  IF ResultEffects = [] THEN WRITELN('Status Clear: No active impairments detected.')
END."""
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout,
                         "--- Dojo Status Set Training ---\nAlert: Fighter is taking damage over time!\nSuccess: Poison cleared.\nStatus Clear: No active impairments detected.\n")

    def test_value_record_field_initializers_runtime(self):
        """VALUE dotted record-field initializers compile and run."""
        src = """PROGRAM Lesson6;
TYPE
  FighterStance = (Natural, Crane, Tiger, Dragon);
  StatusEffect = (Poisoned, Shielded, Stunned, Enraged, Hasted);
  StatusSet = SET OF StatusEffect;
  CombatantRecord = RECORD
    Name: STRING(10);
    Stance: FighterStance;
    CurrentHP: INTEGER;
    Conditions: StatusSet;
  END;
VAR
  Player1: CombatantRecord;
VALUE
  Player1.Name := 'Mr. Karate';
  Player1.Stance := Natural;
  Player1.CurrentHP := 100;
  Player1.Conditions := [Shielded];
BEGIN
  WRITELN(Player1.Name);
  WRITELN(Player1.CurrentHP:1);
  IF Shielded IN Player1.Conditions THEN WRITELN('S');
  WRITELN(ORD(Player1.Stance):1)
END."""
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "Mr. Karate\n100\nS\n0\n")

    def test_case_no_match_default_rangeck_aborts(self):
        """A checked CASE no-match with no OTHERWISE aborts before fall-through."""
        src = """PROGRAM P;
VAR n: INTEGER;
BEGIN
  n := 5;
  WRITELN('BEFORE');
  CASE n OF
    1: WRITELN('ONE');
    2: WRITELN('TWO')
  END;
  WRITELN('AFTER')
END."""
        returncode, stdout = build_and_run(src)
        self.assertNotEqual(returncode, 0)
        self.assertIn("BEFORE\n", stdout)
        self.assertNotIn("AFTER", stdout)

    def test_case_no_match_rangeck_off_falls_through(self):
        """$RANGECK- preserves unchecked CASE no-match fall-through."""
        src = """PROGRAM P;
VAR n: INTEGER;
BEGIN
  {$RANGECK-}
  n := 5;
  WRITELN('BEFORE');
  CASE n OF
    1: WRITELN('ONE');
    2: WRITELN('TWO')
  END;
  WRITELN('AFTER')
END."""
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "BEFORE\nAFTER\n")

    def test_case_otherwise_still_runs(self):
        """An explicit OTHERWISE handles CASE no-match normally."""
        src = """PROGRAM P;
VAR n: INTEGER;
BEGIN
  n := 5;
  CASE n OF
    1: WRITELN('ONE');
    2: WRITELN('TWO')
    OTHERWISE WRITELN('OTHER')
  END;
  WRITELN('AFTER')
END."""
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "OTHER\nAFTER\n")

    def test_case_matching_arm_still_runs(self):
        """A matching CASE arm must not trigger the no-match trap."""
        src = """PROGRAM P;
VAR n: INTEGER;
BEGIN
  n := 2;
  CASE n OF
    1: WRITELN('ONE');
    2: WRITELN('TWO')
  END;
  WRITELN('AFTER')
END."""
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "TWO\nAFTER\n")

    def test_wide_integer_codegen_runtime(self):
        """INTEGER32/INTEGER64 codegen and WRITE work when wide-integers is enabled."""
        src = """PROGRAM P;
VAR x: INTEGER32; y: INTEGER64;
BEGIN
  x := 100000;
  y := x + 9000000000;
  WRITELN(x);
  WRITELN(y);
  WRITELN(MAXINT32);
  WRITELN(MAXINT64)
END."""
        returncode, stdout = build_and_run(src, features={'wide-integers': True})
        self.assertEqual(returncode, 0)
        self.assertEqual(
            [line.strip() for line in stdout.splitlines() if line.strip()],
            [
                "100000",
                "9000100000",
                "2147483647",
                "9223372036854775807",
            ],
        )

    def test_word_zero_extends_into_wide_integer_arithmetic(self):
        """WORD operands remain unsigned when mixed into wider integer arithmetic."""
        src = """PROGRAM P;
VAR w: WORD; r: INTEGER32;
BEGIN
  w := 40000;
  r := w + 0;
  WRITELN(r)
END."""
        returncode, stdout = build_and_run(src, features={'wide-integers': True})
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "40000")

    def test_real_assignment_and_output(self):
        """REAL assignment and output runs and produces correct output."""
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 1.5; WRITELN(x) END."
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("1.5", stdout)

    def test_real_function_parameter_and_return(self):
        """REAL function parameter and return values run and produce output."""
        src = ("PROGRAM P; "
               "FUNCTION Twice(x: REAL): REAL; "
               "BEGIN Twice := x + x END; "
               "BEGIN WRITELN(Twice(1.5)) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("3", stdout)

    def test_abs_and_sqrt_run(self):
        """ABS, SQRT, and other math functions run and produce correct output."""
        src = ("PROGRAM P; VAR x: REAL; BEGIN "
               "WRITELN(ABS(-5)); "
               "x := SQRT(9); "
               "WRITELN(x); "
               "WRITELN(SIN(0)); "
               "WRITELN(COS(0)); "
               "WRITELN(LN(1)); "
               "WRITELN(EXP(0)) "
               "END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("5", stdout)
        self.assertIn("3", stdout)
        self.assertIn("1", stdout)  # COS(0) = 1, EXP(0) = 1

    def test_trunc_run(self):
        """TRUNC truncates toward zero (not floor) for both positive and negative reals."""
        src = ("PROGRAM P; VAR i: INTEGER; BEGIN "
               "i := TRUNC(3.7); WRITELN(i); "
               "i := TRUNC(-3.7); WRITELN(i) "
               "END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        lines = stdout.strip().splitlines()
        self.assertEqual(lines[0].strip(), "3")  # truncate positive
        self.assertEqual(lines[1].strip(), "-3")  # truncate toward zero, NOT floor (-4)

    def test_round_run(self):
        """ROUND rounds away from zero (IBM Pascal manual 11-7)."""
        src = ("PROGRAM P; VAR i: INTEGER; BEGIN "
               "i := ROUND(2.4); WRITELN(i); "
               "i := ROUND(1.6); WRITELN(i); "
               "i := ROUND(-1.6); WRITELN(i); "
               "i := ROUND(3.5); WRITELN(i); "
               "i := ROUND(-3.5); WRITELN(i) "
               "END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        lines = stdout.strip().splitlines()
        self.assertEqual(lines[0].strip(), "2")  # round down
        self.assertEqual(lines[1].strip(), "2")  # round up
        self.assertEqual(lines[2].strip(), "-2")  # round toward zero (away from zero magnitude)
        self.assertEqual(lines[3].strip(), "4")  # tie: away from zero
        self.assertEqual(lines[4].strip(), "-4")  # tie: away from zero

    def test_float_run(self):
        """FLOAT converts integer to real."""
        src = ("PROGRAM P; VAR r: REAL; BEGIN "
               "r := FLOAT(42); WRITELN(r) "
               "END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertRegex(stdout, r"4\.2000000E\+01")

    def test_nil_codegen(self):
        """NIL lowers to a null pointer value."""
        src = "PROGRAM P; VAR p: ^INTEGER; BEGIN p := NIL END."
        ir = compile_to_ir(src)
        self.assertIn("null", ir)

    def test_ads_codegen_uses_pointer_plus_zero_segment(self):
        """ADS lowers as an address pair: R is the LLVM pointer, S is zero."""
        src = "PROGRAM P; VAR x: INTEGER; s: ADS OF INTEGER; BEGIN s := ADS x END."
        ir = compile_to_ir(src)
        self.assertIn("{i16*,i16}", ir.replace(" ", ""))
        self.assertIn("i16 0", ir)

    def test_new_dispose_codegen(self):
        """NEW allocates and DISPOSE frees a pointer variable."""
        src = "PROGRAM P; VAR p: ^INTEGER; BEGIN NEW(p); DISPOSE(p) END."
        ir = compile_to_ir(src)
        self.assertIn("malloc", ir)
        self.assertIn("free", ir)

    def test_string_edit_intrinsics_codegen(self):
        """INSERT/DELETE/POSITN/SCANEQ/SCANNE/ENCODE/DECODE should lower without crashing."""
        src = "PROGRAM P; VAR s: STRING(10); VAR t: STRING(10); VAR l: LSTRING(10); VAR n: INTEGER; BEGIN INSERT(s, t, 1); DELETE(t, 1, 1); WRITELN(POSITN(t, s)); WRITELN(SCANEQ(1, 'a', s, 1)); WRITELN(SCANNE(1, 'a', s, 1)); WRITELN(ENCODE(l, n)); WRITELN(DECODE(s, n)) END."
        ir = compile_to_ir(src)
        self.assertIn("memmove", ir)
        self.assertIn("positn", ir)
        self.assertIn("scaneq", ir)
        self.assertIn("scanne", ir)
        self.assertIn("encode_value", ir)
        self.assertIn("decode_value", ir)

    def test_short_circuit_skips_rhs_runtime(self):
        """Short-circuit operators must not evaluate an unnecessary RHS call."""
        src = ("PROGRAM P; "
               "FUNCTION Bad: BOOLEAN; BEGIN WRITELN(99); Bad := TRUE END; "
               "BEGIN "
               "IF FALSE AND THEN Bad() THEN WRITELN(1); "
               "IF TRUE OR ELSE Bad() THEN WRITELN(2) "
               "END.")
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

    def test_typed_set_constructor_comma_elements_runtime_d026(self):
        """D-026: COLORS[RED, BLUE] executes as a typed set constructor."""
        src = """
        PROGRAM P;
        TYPE COLOR = (RED, BLUE, GREEN);
             COLORS = SET OF COLOR;
        VAR s: COLORS;
        BEGIN
          s := COLORS [RED, BLUE];
          IF RED IN s THEN WRITELN('R');
          IF GREEN IN s THEN WRITELN('G')
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "R\n")

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
        src = ("PROGRAM P; "
               "VAR x: INTEGER; "
               "BEGIN x := 10; WRITELN(x) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("10", stdout)

    def test_for_loop_output(self):
        """FOR loop outputs 1 to 3."""
        src = ("PROGRAM P; "
               "VAR i: INTEGER; "
               "BEGIN FOR i := 1 TO 3 DO WRITELN(i) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        # Output should contain 1, 2, 3 (possibly with newlines)
        self.assertIn("1", stdout)
        self.assertIn("2", stdout)
        self.assertIn("3", stdout)

    def test_procedure_with_parameter(self):
        """Procedure with parameter compiles and runs."""
        src = ("PROGRAM P; "
               "PROCEDURE Print(x: INTEGER); BEGIN WRITELN(x) END; "
               "BEGIN Print(99) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("99", stdout)

    def test_integer_to_real_procedure_parameter(self):
        """INTEGER actual coerces into REAL procedure parameter at codegen."""
        src = ("PROGRAM P; "
               "PROCEDURE PrintReal(x: REAL); BEGIN WRITELN(x) END; "
               "BEGIN PrintReal(7) END.")
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
        self.assertRegex(stdout, r"2\.5000000E-01")

    def test_real_const_declaration_and_use(self):
        """REAL CONST can be declared and used in expressions without crashing."""
        src = ("PROGRAM P; "
               "CONST PI = 3.14159; "
               "VAR r: REAL; "
               "BEGIN r := PI; WRITELN(r) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("3.14", stdout)

    def test_negative_real_const(self):
        """Unary minus applied to a REAL constant generates valid IR and correct output."""
        src = ("PROGRAM P; "
               "CONST NEGPI = -3.14159; "
               "VAR r: REAL; "
               "BEGIN r := NEGPI; WRITELN(r) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("-3.14", stdout)

    def test_real_const_in_expression(self):
        """REAL constant participates in arithmetic expressions correctly."""
        src = ("PROGRAM P; "
               "CONST TWO = 2.0; NEGPI = -3.14159; "
               "FUNCTION Scale(x: REAL): REAL; "
               "BEGIN Scale := x * TWO + NEGPI END; "
               "BEGIN WRITELN(Scale(1.0) : 10 : 4) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        # 1.0 * 2.0 + (-3.14159) = -1.14159
        self.assertIn("-1.1416", stdout)

    def test_unary_minus_real_variable(self):
        """Unary minus on a REAL variable generates valid IR (not integer neg)."""
        src = ("PROGRAM P; VAR x: REAL; "
               "BEGIN x := 2.5; WRITELN(-x) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("-2.5", stdout)

    def test_real_comparison_produces_boolean(self):
        """REAL comparisons evaluate and branch correctly."""
        src = ("PROGRAM P; CONST NEGPI = -3.14159; "
               "BEGIN "
               "IF NEGPI < 0.0 THEN WRITELN(1) ELSE WRITELN(0); "
               "IF 0.5 = 0.5 THEN WRITELN(1) ELSE WRITELN(0) "
               "END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        lines = stdout.strip().split()
        self.assertEqual(lines, ['1', '1'])

    def test_mixed_int_real_arithmetic(self):
        """Mixed INTEGER and REAL operands widen to REAL correctly."""
        src = ("PROGRAM P; VAR x: REAL; i: INTEGER; "
               "BEGIN x := 3.0; i := 7; WRITELN(x + i : 8 : 2) END.")
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertIn("10.00", stdout)

    def test_nested_arithmetic(self):
        """Nested arithmetic expressions."""
        src = ("PROGRAM P; "
               "BEGIN WRITELN((2 + 3) * 4) END.")
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

    def test_packed_char_array_string_assignment_and_write_runtime(self):
        """PACKED ARRAY[..] OF CHAR accepts string literal assignment and WRITE."""
        src = """
        PROGRAM P;
        TYPE NAME = PACKED ARRAY[1..10] OF CHAR;
        VAR s: NAME;
        BEGIN
            s := 'Mr. Karate';
            WRITELN(s)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.strip(), "Mr. Karate")

    def test_lesson1b_packed_char_array_runtime(self):
        """Lesson1b-style packed char array string storage matches vintage output."""
        src = """
        PROGRAM Lesson1b;
        TYPE NAME = PACKED ARRAY[1..10] OF CHAR;
        VAR TrainerName : NAME; FighterSymbol : CHAR; TrainingRounds : INTEGER;
        BEGIN
            TrainerName := 'Mr. Karate';
            FighterSymbol := 'K';
            TrainingRounds := 10;
            WRITELN('Coach: Welcome to the dojo, ', TrainerName);
            WRITELN('Symbol: ', FighterSymbol);
            WRITELN('Rounds Scheduled: ', TrainingRounds:2)
        END.
        """
        returncode, stdout = build_and_run(src)
        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, "Coach: Welcome to the dojo, Mr. Karate\nSymbol: K\nRounds Scheduled: 10\n")

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
            src_str: STRING(6);
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
            src_str := 'abc  ';
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


class TestWrdBywordCodegen(unittest.TestCase):
    """IR-level and run-level tests for WRD and BYWORD (item 4.7)."""

    # --- IR-level: check instruction shape ---

    def test_wrd_integer_is_identity_at_i16(self):
        """WRD of an INTEGER value is already i16 after the width flip."""
        src = "PROGRAM P; VAR i: INTEGER; w: WORD; BEGIN i := -1; w := WRD(i) END."
        ir_text = compile_to_ir(src)
        self.assertIn("store i16", ir_text)

    def test_wrd_char_emits_zext(self):
        """WRD of a CHAR value lowers to a zext to i16."""
        src = "PROGRAM P; VAR c: CHAR; w: WORD; BEGIN c := 'A'; w := WRD(c) END."
        ir_text = compile_to_ir(src)
        self.assertIn("zext", ir_text)

    def test_byword_emits_shl_and_or(self):
        """BYWORD lowers to a shift-left-8 and bitwise-or."""
        src = "PROGRAM P; VAR w: WORD; BEGIN w := BYWORD(16#AB, 16#CD) END."
        ir_text = compile_to_ir(src)
        self.assertIn("shl", ir_text)
        self.assertIn("or", ir_text)

    # --- Run-level: check numeric results ---

    @requires_exe
    def test_wrd_negative_one_is_maxword(self):
        """WRD(-1) must equal MAXWORD (65535): same 16-bit pattern, unsigned."""
        src = """PROGRAM P;
VAR w: WORD;
BEGIN
  w := WRD(-1);
  WRITELN(w)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("65535", out)

    @requires_exe
    def test_word_probe_d032_edges(self):
        """D-032: MAXWORD, unsigned WORD assignment, and WRD(-1) match vintage."""
        src = """PROGRAM P;
VAR w: WORD;
BEGIN
  WRITELN(MAXWORD);
  w := 40000;
  WRITELN(w);
  w := WRD(-1);
  WRITELN(w)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().splitlines(), ["65535", "40000", "65535"])

    @requires_exe
    def test_wrd_char_gives_ascii_value(self):
        """WRD('A') equals 65 (ASCII code of A)."""
        src = """PROGRAM P;
VAR w: WORD;
BEGIN
  w := WRD('A');
  WRITELN(w)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("65", out)

    @requires_exe
    def test_wrd_word_identity(self):
        """WRD of a WORD value is identity."""
        # Populate via WRD(integer) to avoid the pre-existing INTEGER->WORD
        # literal assignment limitation.
        src = """PROGRAM P;
VAR w: WORD; i: INTEGER;
BEGIN
  i := 1000;
  w := WRD(i);
  w := WRD(w);
  WRITELN(w)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("1000", out)

    @requires_exe
    def test_byword_hi_lo_assembly(self):
        """BYWORD(0xAB, 0xCD) == 0xABCD == 43981."""
        src = """PROGRAM P;
VAR w: WORD;
BEGIN
  w := BYWORD(16#AB, 16#CD);
  WRITELN(w)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("43981", out)

    @requires_exe
    def test_byword_lobyte_hibyte_roundtrip(self):
        """LOBYTE(BYWORD(hi,lo)) == lo  and  HIBYTE(BYWORD(hi,lo)) == hi."""
        # HIBYTE/LOBYTE return CHAR; use CHAR variables to avoid the
        # pre-existing CHAR->INTEGER assignment limitation.
        src = """PROGRAM P;
VAR w: WORD; hi_out, lo_out: CHAR;
BEGIN
  w := BYWORD(16#12, 16#34);
  hi_out := HIBYTE(w);
  lo_out := LOBYTE(w);
  WRITELN(ORD(hi_out));
  WRITELN(ORD(lo_out))
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(lines[0].strip(), "18")  # 0x12 = 18
        self.assertEqual(lines[1].strip(), "52")  # 0x34 = 52


class TestRetypeCodegen(unittest.TestCase):
    """IR-level and run-level tests for the RETYPE intrinsic."""

    def test_retype_ir_shape(self):
        """Verify that RETYPE lowers to memory load/store and pointer bitcast."""
        src = "PROGRAM P; VAR i: INTEGER; c: CHAR; BEGIN i := 65; c := RETYPE(CHAR, i) END."
        ir_text = compile_to_ir(src)
        self.assertIn("bitcast", ir_text)

    @requires_exe
    def test_retype_char_to_boolean_runtime(self):
        src = """PROGRAM P;
VAR c: CHAR; b: BOOLEAN;
BEGIN
    c := CHR(1);
    b := RETYPE(BOOLEAN, c);
    IF b THEN
        WRITELN(1)
    ELSE
        WRITELN(0)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("1", out)

    @requires_exe
    def test_retype_constant_folding(self):
        src = """PROGRAM P;
VAR i: INTEGER;
BEGIN
    i := RETYPE(INTEGER, 'A');
    WRITELN(i)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("65", out)

    @requires_exe
    def test_retype_selectors(self):
        src = """PROGRAM P;
TYPE
    TArray = ARRAY[1..4] OF CHAR;
VAR
    i: INTEGER;
    c: CHAR;
BEGIN
    { 16#4100 is 'A\0' in little endian.
      Index 1 in memory will fetch the second byte, which is 16#41 = 65. }
    i := 16#4100;
    c := RETYPE(TArray, i)[1];
    WRITELN(ORD(c))
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        # On little-endian systems, 16#41 (65) is the first byte.
        self.assertIn("65", out)

    def test_retype_pointer_value_reinterprets_bits_not_pointee(self):
        """Checklist 9.9: RETYPE of a genuine ``^T`` pointer must reinterpret the
        pointer's address bits, NOT dereference it. The fixed lowering spills the
        loaded pointer to a slot and bitcasts the slot (pointer-to-pointer);
        the old buggy path bitcast the pointer value directly and loaded through
        it (a hidden dereference)."""
        src = ("PROGRAM P; TYPE PInt = ^INTEGER; VAR p: PInt; w: WORD; "
               "BEGIN w := RETYPE(WORD, p) END.")
        ir_text = compile_to_ir(src)
        # Pointer value is spilled to a slot, and the *slot* (i16**) is bitcast.
        self.assertIn("alloca i16*", ir_text)
        self.assertIn("bitcast i16**", ir_text)

    def test_retype_aggregate_address_still_loads_through(self):
        """Checklist 9.9 regression guard: retyping an aggregate (a STRING here,
        which lowers to an address-of-bytes pointer) must keep reinterpreting the
        pointee in place — i.e. bitcast the aggregate address and load through
        it, with no pointer spill."""
        src = ("PROGRAM P; VAR s: STRING(4); i: INTEGER; "
               "BEGIN i := RETYPE(INTEGER, s) END.")
        ir_text = compile_to_ir(src)
        self.assertIn("bitcast [4 x i8]*", ir_text)
        # No spill of an aggregate pointer to a slot on this path.
        self.assertNotIn("alloca [4 x i8]*", ir_text)

    @requires_exe
    def test_retype_nil_pointer_does_not_dereference(self):
        """A NIL pointer retyped to an ordinal must yield 0 (its address bits),
        not segfault. The buggy load-through path dereferenced NIL and crashed;
        the fixed path reinterprets the null bits and prints 0."""
        src = """PROGRAM P;
TYPE PInt = ^INTEGER;
VAR p: PInt; n: INTEGER;
BEGIN
    p := NIL;
    n := RETYPE(INTEGER, p);
    WRITELN(n)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("0", out)


class TestEnumCodegen(unittest.TestCase):
    """First-class enum support (checklist 9.8): SUCC/PRED, CASE, FOR, WRITE."""

    ENUM = "TYPE Color = (Red, Green, Blue);"

    @requires_exe
    def test_enum_succ_pred_runtime(self):
        src = f"""PROGRAM P;
{self.ENUM}
VAR c: Color;
BEGIN
    c := Green;
    c := SUCC(c);
    WRITELN(ORD(c));
    c := PRED(PRED(c));
    WRITELN(ORD(c))
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ["2", "0"])

    @requires_exe
    def test_enum_case_runtime(self):
        src = f"""PROGRAM P;
{self.ENUM}
VAR c: Color;
BEGIN
    c := Green;
    CASE c OF
        Red: WRITELN(1);
        Green: WRITELN(2);
        Blue: WRITELN(3)
    END
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("2", out)

    @requires_exe
    def test_enum_for_loop_runtime(self):
        src = f"""PROGRAM P;
{self.ENUM}
VAR c: Color;
BEGIN
    FOR c := Red TO Blue DO WRITELN(ORD(c))
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ["0", "1", "2"])

    @requires_exe
    def test_enum_write_name_runtime(self):
        """Default WRITE of an enum variable prints the faithful ordinal."""
        src = f"""PROGRAM P;
{self.ENUM}
VAR c: Color;
BEGIN
    c := Green;
    WRITELN(c)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "1\n")

    @requires_exe
    def test_enum_write_names_in_for_loop_runtime(self):
        src = f"""PROGRAM P;
{self.ENUM}
VAR c: Color;
BEGIN
    FOR c := Red TO Blue DO WRITELN(c)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ["0", "1", "2"])

    @requires_exe
    def test_enum_write_bare_member_literal_runtime(self):
        src = f"""PROGRAM P;
{self.ENUM}
BEGIN
    WRITELN(Blue)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "2\n")

    def test_symbolic_enum_write_emits_name_table(self):
        """With -f symbolic-enum-io, enum WRITE builds a name table."""
        src = f"PROGRAM P; {self.ENUM} VAR c: Color; BEGIN c := Red; WRITELN(c) END."
        ir_text = compile_to_ir(src, features={'symbolic-enum-io': True})
        self.assertIn("enumtab", ir_text)


class TestPackUnpackCodegen(unittest.TestCase):
    """Runtime execution tests for the PACK and UNPACK intrinsics."""

    @requires_exe
    def test_pack_and_unpack_runtime(self):
        src = """PROGRAM P;
VAR
    a: ARRAY[1..10] OF INTEGER;
    z: PACKED ARRAY[1..5] OF INTEGER;
    i: INTEGER;
BEGIN
    { Initialize unpacked array: 10, 20, 30, ... }
    FOR i := 1 TO 10 DO
        a[i] := i * 10;

    { Pack elements a[3..7] (30, 40, 50, 60, 70) into z }
    PACK(a, 3, z);

    { Write z[2] which should be 40 }
    WRITELN(z[2]);

    { Modify z }
    z[3] := 999;

    { Unpack z back to a starting at index 5 }
    UNPACK(z, a, 5);

    { Write a[7] which should be 999 (z[3] unpacked at a[5-1+3] = a[7]) }
    WRITELN(a[7]);
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        self.assertEqual(lines[0], "40")
        self.assertEqual(lines[1], "999")

    @requires_exe
    def test_pack_unpack_probe_d031_char_round_trip(self):
        """D-031: PACK/UNPACK index convention and packed-char-array WRITE."""
        src = """PROGRAM P;
VAR a: ARRAY [1..6] OF CHAR;
    z: PACKED ARRAY [1..3] OF CHAR;
    b: ARRAY [1..6] OF CHAR;
    i: INTEGER;
BEGIN
  FOR i := 1 TO 6 DO a[i] := CHR(ORD('A') + i - 1);
  PACK(a, 2, z);
  WRITELN(z);
  FOR i := 1 TO 6 DO b[i] := '.';
  UNPACK(z, b, 3);
  FOR i := 1 TO 6 DO WRITE(b[i]);
  WRITELN
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "BCD\n..BCD.\n")


class TestArrayLowerBoundIndexing(unittest.TestCase):
    """Regression tests: Pascal array indices are relative to the declared
    lower bound, so storage (allocated 0-based as [high-low+1 x elem]) must be
    addressed by index-minus-lower-bound. Indexing with the raw Pascal index
    reads/writes outside the allocation for any array whose lower bound != 0,
    silently corrupting adjacent memory."""

    @requires_exe
    def test_nonzero_lower_bound_round_trips(self):
        """ARRAY[5..7] written and read by Pascal index returns what was stored."""
        src = """PROGRAM P;
VAR a: ARRAY[5..7] OF INTEGER;
BEGIN
    a[5] := 100; a[6] := 200; a[7] := 300;
    WRITELN(a[5]); WRITELN(a[6]); WRITELN(a[7])
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["100", "200", "300"])

    @requires_exe
    def test_index_does_not_clobber_adjacent_variable(self):
        """Writing the top element of ARRAY[1..3] must not overflow its storage
        and overwrite a neighboring scalar (the original off-by-lower-bound bug
        wrote one slot past the end)."""
        src = """PROGRAM P;
VAR a: ARRAY[1..3] OF INTEGER;
    guard: INTEGER;
    i: INTEGER;
BEGIN
    guard := 777;
    FOR i := 1 TO 3 DO a[i] := i * 10;
    WRITELN(guard)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "777")

    @requires_exe
    def test_nested_array_aliased_type_with_nonzero_bounds(self):
        """A 2-D array via a named ARRAY OF ARRAY type, with non-1 lower bounds
        on both dimensions, indexes each dimension correctly."""
        src = """PROGRAM P;
TYPE Grid = ARRAY[2..3] OF ARRAY[10..12] OF INTEGER;
VAR m: Grid;
    r, c: INTEGER;
BEGIN
    FOR r := 2 TO 3 DO
        FOR c := 10 TO 12 DO
            m[r][c] := r * 100 + c;
    WRITELN(m[2][10]); WRITELN(m[3][12])
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["210", "312"])


class TestRecordFieldCodegen(unittest.TestCase):
    """Record field access (declaration, plain `.field`, RETYPE `.field`).

    Records were previously unimplemented: the type checker crashed on a
    record declaration and codegen had no struct layout, so a FIELD selector
    was a no-op that addressed offset 0 regardless of which field was named.
    """

    @requires_exe
    def test_field_access_reads_correct_offsets(self):
        """Each field round-trips at its own offset; a non-first field must not
        resolve to offset 0 (the original no-op behavior)."""
        src = """PROGRAM P;
TYPE Pt = RECORD x: INTEGER; y: INTEGER END;
VAR p: Pt;
BEGIN
    p.x := 10; p.y := 20;
    WRITELN(p.x); WRITELN(p.y)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["10", "20"])

    @requires_exe
    def test_record_field_does_not_clobber_neighbor(self):
        """Writing record fields must stay within the struct's storage."""
        src = """PROGRAM P;
TYPE R = RECORD a, b, c: INTEGER END;
VAR v: R;
    guard: INTEGER;
BEGIN
    guard := 1234;
    v.a := 1; v.b := 2; v.c := 3;
    WRITELN(v.c); WRITELN(guard)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["3", "1234"])

    @requires_exe
    def test_array_of_records(self):
        """Array-of-record indexing exercises lower-bound subtraction AND field
        offset together."""
        src = """PROGRAM P;
TYPE Pt = RECORD a, b: INTEGER END;
VAR arr: ARRAY[1..3] OF Pt;
    i: INTEGER;
BEGIN
    FOR i := 1 TO 3 DO BEGIN arr[i].a := i; arr[i].b := i * 100 END;
    WRITELN(arr[2].a); WRITELN(arr[3].b)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["2", "300"])

    @requires_exe
    def test_nested_records(self):
        src = """PROGRAM P;
TYPE Inner = RECORD m, n: INTEGER END;
     Outer = RECORD tag: INTEGER; nested: Inner END;
VAR o: Outer;
BEGIN
    o.tag := 9; o.nested.m := 100; o.nested.n := 200;
    WRITELN(o.tag); WRITELN(o.nested.m); WRITELN(o.nested.n)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["9", "100", "200"])

    @requires_exe
    def test_retype_reads_non_first_record_field(self):
        """RETYPE(record, x).field must address the field's real offset, not 0.
        0x000A0005 little-endian: lo WORD = 5, hi WORD = 10."""
        src = """PROGRAM P;
TYPE Pair = RECORD lo, hi: WORD END;
VAR i: INTEGER32;
    w: WORD;
BEGIN
    i := 16#000A0005;
    w := RETYPE(Pair, i).hi;
    WRITELN(w)
END."""
        rc, out = build_and_run(src, features={'wide-integers': True})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10")

    @requires_exe
    def test_field_access_case_insensitive(self):
        """Fields declared in one case are reachable in any case, end to end."""
        src = """PROGRAM P;
TYPE Rec = RECORD Count: INTEGER; Total: INTEGER END;
VAR r: Rec;
BEGIN
    r.count := 5; r.TOTAL := 50;
    WRITELN(r.Count); WRITELN(r.cOuNt); WRITELN(r.total)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["5", "5", "50"])

    @requires_exe
    def test_whole_record_copy_preserves_fields(self):
        """A whole-record assignment between equivalent (same-order) records
        copies each field to its counterpart, even when names differ in case."""
        src = """PROGRAM P;
TYPE A = RECORD Count: INTEGER; Total: INTEGER END;
     B = RECORD count: INTEGER; total: INTEGER END;
VAR a: A;
    b: B;
BEGIN
    b.count := 7; b.total := 8;
    a := b;
    WRITELN(a.Count); WRITELN(a.Total)
END."""
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual([l.strip() for l in out.splitlines() if l.strip()], ["7", "8"])


# Path to the runtime C library (sibling of the tests/ directory).
RUNTIME_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime")


def _compile_and_run_c(driver_src: str, runtime_files: list) -> tuple:
    """Compile a small C driver together with one or more runtime/*.c stubs,
    run it, and return (returncode, stdout, stderr). Used to exercise the
    runtime library directly (the Pascal harness does not link runtime stubs)."""
    tmpdir = tempfile.mkdtemp()
    try:
        driver_path = os.path.join(tmpdir, "driver.c")
        with open(driver_path, "w") as f:
            f.write(driver_src)
        exe_path = os.path.join(tmpdir, "prog")
        sources = [driver_path] + [os.path.join(RUNTIME_DIR, rf) for rf in runtime_files]
        compile_result = subprocess.run(["clang", *sources, "-o", exe_path], capture_output=True, text=True)
        if compile_result.returncode != 0:
            raise RuntimeError(f"clang failed: {compile_result.stderr}")
        run_result = subprocess.run([exe_path], capture_output=True, text=True)
        return run_result.returncode, run_result.stdout, run_result.stderr
    finally:
        import shutil
        shutil.rmtree(tmpdir)


def _build_pascal_with_runtime(src: str, runtime_files: list, stdin: str = "", features=None) -> tuple:
    """Compile Pascal source to IR, link it with the given runtime/*.c stubs,
    run it, and return (returncode, stdout). Unlike build_and_run this links the
    real runtime, so it exercises the Pascal -> IR -> native ABI end to end
    (notably the segmented {i8*, i16} address pair against the C `adsmem`
    struct)."""
    from pascal1981.codegen_llvm import compile_to_llvm

    ast = parse_source(src)
    result = typecheck_source(src, features=features)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    ir = compile_to_llvm(ast, features=features)

    tmpdir = tempfile.mkdtemp()
    try:
        ll_path = os.path.join(tmpdir, "prog.ll")
        with open(ll_path, "w") as f:
            f.write(ir)
        exe_path = os.path.join(tmpdir, "prog")
        sources = [ll_path] + [os.path.join(RUNTIME_DIR, rf) for rf in runtime_files]
        compile_result = subprocess.run(["clang", *sources, "-o", exe_path, "-lm"], capture_output=True, text=True)
        if compile_result.returncode != 0:
            raise RuntimeError(f"clang failed: {compile_result.stderr}")
        run_result = subprocess.run([exe_path], input=stdin, capture_output=True, text=True)
        return run_result.returncode, run_result.stdout
    finally:
        import shutil
        shutil.rmtree(tmpdir)


@requires_exe
class TestMoveRuntimeDirection(unittest.TestCase):
    """The move builtins must honor MOVEL/MOVER's left/right direction (manual),
    which is only observable on overlapping regions. With dst = src + 1 a forward
    (left-start) move propagates the first byte across the buffer, while a
    backward (right-start) move performs a plain shifted copy. A memmove would
    erase this distinction, so these tests fail if the stubs revert to memmove."""

    def _run(self, func_name: str) -> str:
        driver = ("#include <stdio.h>\n"
                  f"extern int {func_name}(char *src, char *dst, unsigned short len);\n"
                  "int main(void) {\n"
                  "    char b[6] = \"ABCDE\";\n"
                  f"    {func_name}(b, b + 1, 4);\n"
                  "    printf(\"%s\\n\", b);\n"
                  "    return 0;\n"
                  "}\n")
        rc, out, _ = _compile_and_run_c(driver, [f"{func_name}.c"])
        self.assertEqual(rc, 0)
        return out.strip()

    def _run_seg(self, func_name: str) -> str:
        """Driver for the SEGMENTED variants, which take ADSMEM addresses,
        lowered to a {char *ptr, unsigned short seg} pair passed by value."""
        driver = ("#include <stdio.h>\n"
                  "typedef struct { char *ptr; unsigned short seg; } adsmem;\n"
                  f"extern int {func_name}(adsmem src, adsmem dst, unsigned short len);\n"
                  "int main(void) {\n"
                  "    char b[6] = \"ABCDE\";\n"
                  "    adsmem s = { b, 0 };\n"
                  "    adsmem d = { b + 1, 0 };\n"
                  f"    {func_name}(s, d, 4);\n"
                  "    printf(\"%s\\n\", b);\n"
                  "    return 0;\n"
                  "}\n")
        rc, out, _ = _compile_and_run_c(driver, [f"{func_name}.c"])
        self.assertEqual(rc, 0)
        return out.strip()

    def test_movel_propagates_forward(self):
        # Forward copy: b[1]=b[0]='A', then each new 'A' feeds the next -> "AAAAA".
        self.assertEqual(self._run("movel"), "AAAAA")

    def test_mover_copies_backward(self):
        # Backward copy (dst>src): bytes shift right by one -> "AABCD".
        self.assertEqual(self._run("mover"), "AABCD")

    def test_movel_and_mover_differ(self):
        self.assertNotEqual(self._run("movel"), self._run("mover"))

    def test_movesl_propagates_forward(self):
        # Segmented forward move mirrors MOVEL's left-start propagation.
        self.assertEqual(self._run_seg("movesl"), "AAAAA")

    def test_movesr_copies_backward(self):
        # Segmented backward move mirrors MOVER's right-start shifted copy.
        self.assertEqual(self._run_seg("movesr"), "AABCD")

    def test_movesl_and_movesr_differ(self):
        self.assertNotEqual(self._run_seg("movesl"), self._run_seg("movesr"))

    def test_segmented_move_through_full_pipeline(self):
        """End-to-end: a Pascal MOVESL(ADS .., ADS .., WRD(..)) compiled to IR and
        linked against the real movesl.c must copy correctly, proving the
        segmented address pair matches the C `adsmem` struct ABI."""
        src = ("PROGRAM P; "
               "VAR a, b: ARRAY[1..4] OF CHAR; "
               "BEGIN "
               "a[1] := 'W'; a[2] := 'X'; a[3] := 'Y'; a[4] := 'Z'; "
               "b[1] := '.'; b[2] := '.'; b[3] := '.'; b[4] := '.'; "
               "MOVESL(ADS a, ADS b, WRD(4)); "
               "WRITELN(b[1], b[2], b[3], b[4]) "
               "END.")
        rc, out = _build_pascal_with_runtime(src, ["movesl.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "WXYZ")


@requires_exe
class TestWriteRealFormatting(unittest.TestCase):

    def test_real_default_format_emits_exponential(self):
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 1.5; WRITELN(x) END."
        ir = compile_to_ir(src)
        self.assertIn('c"%14.7E', ir)

    def test_real_width_only_uses_exponential(self):
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 1.5; WRITELN(x:10) END."
        ir = compile_to_ir(src)
        self.assertIn("%*E", ir)

    def test_real_width_and_precision_use_fixed_point(self):
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 1.5; WRITELN(x:8:3) END."
        ir = compile_to_ir(src)
        self.assertIn("%*.*f", ir)


class TestRuntimeAbortFlush(unittest.TestCase):
    """Generated runtime-check aborts flush stdout before aborting."""

    @requires_exe
    def test_string_capacity_abort_preserves_prior_stdout(self):
        src = """PROGRAM P;
VAR s: LSTRING(2);
BEGIN
  WRITELN('BEFORE');
  CONCAT(s, 'ABC');
  WRITELN('AFTER')
END."""
        rc, out = build_and_run(src)
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "BEFORE\n")


class TestAbortRuntime(unittest.TestCase):
    """ABORT's runtime must surface the message, error code, and status, then
    abort (manual: stops like an internal runtime error)."""

    @requires_exe
    def test_pabort_reports_message_and_aborts(self):
        driver = ("extern void pabort(const char *msg, int msglen, "
                  "unsigned short code, unsigned short status);\n"
                  "int main(void) {\n"
                  "    pabort(\"boom\", 4, 5, 7);\n"
                  "    return 0;\n"
                  "}\n")
        rc, out, err = _compile_and_run_c(driver, ["pabort.c"])
        self.assertNotEqual(rc, 0)  # abort() does not return 0
        self.assertIn("boom", err)
        self.assertIn("5", err)
        self.assertIn("7", err)


class TestWriteDoubleColonCodegen(unittest.TestCase):
    """P::N lowering (discrepancy D-002): default 14-char field, fixed point."""

    def test_double_colon_real_uses_fixed_point_with_default_width(self):
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 123.456; WRITELN(x::2) END."
        ir = compile_to_ir(src)
        self.assertIn("%*.*f", ir)

    @requires_exe
    def test_double_colon_real_matches_vintage_output(self):
        """Vintage 1981 output for WRITELN(123.456::2): '        123.46'
        (14-char field, 2 decimals) — observed in differential probe t002."""
        src = "PROGRAM P; VAR x: REAL; BEGIN x := 123.456; WRITELN(x::2) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "        123.46\n")


class TestStringCapacityGatesRespectRangeck(unittest.TestCase):
    """7.7 follow-on: the string-intrinsic capacity gates must honor the
    per-statement $RANGECK state and the CLI force override.

    (The §9.5 checklist note claiming these were 'unconditional' was stale:
    statement-level wiring via effective_rangeck already exists. These tests
    pin the behavior so it cannot silently regress.)

    Note: each guard contributes exactly one `_overflow` basic block, whose
    label appears twice in the IR text (block definition + cbranch operand),
    so we count distinct guards as occurrences // 2.
    """

    def _guards(self, src: str, **kw) -> int:
        from pascal1981.codegen_llvm import compile_to_llvm
        from tests.support import parse_source
        ir = compile_to_llvm(parse_source(src), **kw)
        return ir.count('_overflow') // 2

    MIX = ("PROGRAM P; VAR a, b: LSTRING(5); BEGIN "
           "{$RANGECK-} CONCAT(a, 'XY'); "
           "{$RANGECK+} CONCAT(b, 'XY') END.")

    def test_rangeck_off_removes_concat_guard(self):
        src = "PROGRAM P; VAR a: LSTRING(5); BEGIN {$RANGECK-} CONCAT(a, 'XY') END."
        self.assertEqual(self._guards(src), 0)

    def test_rangeck_default_emits_concat_guard(self):
        src = "PROGRAM P; VAR a: LSTRING(5); BEGIN CONCAT(a, 'XY') END."
        self.assertEqual(self._guards(src), 1)

    def test_per_statement_granularity(self):
        """$RANGECK- then $RANGECK+ in one program: only the second
        statement gets a guard."""
        self.assertEqual(self._guards(self.MIX), 1)

    def test_cli_force_off_overrides_source(self):
        self.assertEqual(self._guards(self.MIX, force_flags={'RANGECK': False}), 0)

    def test_cli_force_on_overrides_source(self):
        self.assertEqual(self._guards(self.MIX, force_flags={'RANGECK': True}), 2)

    def test_string_assignment_gate_respects_flag(self):
        on = "PROGRAM P; VAR a: LSTRING(5); b: LSTRING(9); BEGIN a := b END."
        off = "PROGRAM P; VAR a: LSTRING(5); b: LSTRING(9); BEGIN {$RANGECK-} a := b END."
        self.assertEqual(self._guards(on), 1)
        self.assertEqual(self._guards(off), 0)

    def test_copystr_copylst_insert_respect_flag(self):
        for call in ("COPYSTR('AB', s)", "COPYLST('AB', a)", "INSERT('AB', a, 1)"):
            src_on = f"PROGRAM P; VAR a: LSTRING(5); s: STRING(5); BEGIN {call} END."
            src_off = f"PROGRAM P; VAR a: LSTRING(5); s: STRING(5); BEGIN {{$RANGECK-}} {call} END."
            self.assertGreaterEqual(self._guards(src_on), 1, call)
            self.assertEqual(self._guards(src_off), 0, call)


class TestRuntimeCheckFlags(unittest.TestCase):
    """$INDEXCK / $MATHCK / $NILCK / $INITCK codegen (manual metacommand
    pages: INDEXCK/MATHCK/NILCK/STACKCK default +, INITCK default -).
    The -32768 INITCK sentinel is widened to its 32-bit analogue
    -32768 (INT16_MIN) for this implementation's INTEGER."""

    def _ir(self, src: str, **kw) -> str:
        from pascal1981.codegen_llvm import compile_to_llvm
        from tests.support import parse_source
        return compile_to_llvm(parse_source(src), **kw)

    # ---------------- INDEXCK ----------------

    IDX = ("PROGRAM P; VAR a: ARRAY[1..3] OF INTEGER; i: INTEGER; "
           "BEGIN i := 2; a[i] := 1 END.")

    def test_indexck_default_emits_guard(self):
        self.assertIn('indexck_fail', self._ir(self.IDX))

    def test_indexck_off_removes_guard(self):
        src = self.IDX.replace('BEGIN', 'BEGIN {$INDEXCK-}')
        self.assertNotIn('indexck_fail', self._ir(src))

    def test_indexck_constant_in_range_skips_guard(self):
        src = ("PROGRAM P; VAR a: ARRAY[1..3] OF INTEGER; "
               "BEGIN a[2] := 1 END.")
        self.assertNotIn('indexck_fail', self._ir(src))

    @requires_exe
    def test_indexck_aborts_out_of_bounds(self):
        src = ("PROGRAM P; VAR a: ARRAY[1..3] OF INTEGER; i: INTEGER; "
               "BEGIN i := 5; a[i] := 1 END.")
        rc, _ = build_and_run(src)
        self.assertNotEqual(rc, 0)

    @requires_exe
    def test_indexck_in_bounds_runs_clean(self):
        rc, _ = build_and_run(self.IDX)
        self.assertEqual(rc, 0)

    # ---------------- MATHCK ----------------

    ADD = "PROGRAM P; VAR x, y: INTEGER; BEGIN x := 1; y := x + 1 END."

    def test_mathck_default_uses_overflow_intrinsic(self):
        self.assertIn('sadd.with.overflow', self._ir(self.ADD))

    def test_mathck_off_uses_plain_add(self):
        src = self.ADD.replace('BEGIN', 'BEGIN {$MATHCK-}')
        ir = self._ir(src)
        self.assertNotIn('with.overflow', ir)

    def test_mathck_word_uses_unsigned_intrinsic(self):
        src = "PROGRAM P; VAR x, y: WORD; BEGIN x := 1; y := x + x END."
        self.assertIn('uadd.with.overflow', self._ir(src))

    def test_mathck_div_emits_zero_guard(self):
        src = "PROGRAM P; VAR x, y: INTEGER; BEGIN y := 2; x := 4 DIV y END."
        self.assertIn('mathck_div_fail', self._ir(src))

    @requires_exe
    def test_mathck_overflow_aborts(self):
        src = ("PROGRAM P; VAR x: INTEGER; "
               "BEGIN x := 32767; x := x + 1 END.")
        rc, _ = build_and_run(src)
        self.assertNotEqual(rc, 0)

    @requires_exe
    def test_mathck_off_overflow_wraps(self):
        src = ("PROGRAM P; VAR x: INTEGER; BEGIN {$MATHCK-} "
               "x := 32767; x := x + 1; WRITELN(x) END.")
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), '-32768')

    @requires_exe
    def test_mathck_div_by_zero_aborts(self):
        src = ("PROGRAM P; VAR x, y: INTEGER; "
               "BEGIN y := 0; x := 1 DIV y END.")
        rc, _ = build_and_run(src)
        self.assertNotEqual(rc, 0)

    # ---------------- NILCK ----------------

    DEREF = ("PROGRAM P; TYPE pi = ^INTEGER; VAR p: pi; x: INTEGER; "
             "BEGIN p := NIL; x := p^ END.")

    def test_nilck_default_emits_guard(self):
        self.assertIn('nilck_fail', self._ir(self.DEREF))

    def test_nilck_off_removes_guard(self):
        src = self.DEREF.replace('BEGIN p', 'BEGIN {$NILCK-} p')
        self.assertNotIn('nilck_fail', self._ir(src))

    @requires_exe
    def test_nilck_nil_deref_aborts(self):
        rc, _ = build_and_run(self.DEREF)
        self.assertNotEqual(rc, 0)

    @requires_exe
    def test_nilck_initck_sentinel_deref_aborts(self):
        """With $INITCK+, an uninitialized pointer holds sentinel 1 and
        dereferencing it must be caught by NILCK (manual: 'Uninitialized
        (value of 1; only with $INITCK)')."""
        src = ("PROGRAM P; {$INITCK+} TYPE pi = ^INTEGER; "
               "VAR p: pi; x: INTEGER; BEGIN x := p^ END.")
        rc, _ = build_and_run(src)
        self.assertNotEqual(rc, 0)

    # ---------------- INITCK ----------------

    @requires_exe
    def test_initck_integer_sentinel_is_int16_min(self):
        src = "PROGRAM P; {$INITCK+} VAR x: INTEGER; BEGIN WRITELN(x) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), '-32768')

    @requires_exe
    def test_initck_default_off_zero_init(self):
        src = "PROGRAM P; VAR x: INTEGER; BEGIN WRITELN(x) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), '0')

    def test_initck_pointer_sentinel_requires_nilck(self):
        """Pointer sentinel 1 is emitted only when $NILCK is also on."""
        on = ("PROGRAM P; {$INITCK+} TYPE pi = ^INTEGER; VAR p: pi; "
              "BEGIN END.")
        off = ("PROGRAM P; {$INITCK+, $NILCK-} TYPE pi = ^INTEGER; VAR p: pi; "
               "BEGIN END.")
        self.assertIn('inttoptr', self._ir(on))
        self.assertNotIn('inttoptr (i64 1', self._ir(off))

    # ---------------- CLI overrides ----------------

    def test_force_flags_override_for_new_checks(self):
        ir_off = self._ir(self.IDX, force_flags={'INDEXCK': False})
        self.assertNotIn('indexck_fail', ir_off)
        ir_on = self._ir(self.ADD.replace('BEGIN', 'BEGIN {$MATHCK-}'), force_flags={'MATHCK': True})
        self.assertIn('with.overflow', ir_on)


@requires_llvm
class TestValueInitializerCodegen(unittest.TestCase):
    """VALUE-section runtime initialization."""

    @requires_exe
    def test_value_initializes_scalars_runtime(self):
        src = ("PROGRAM P; VAR i: INTEGER; r: REAL; c: CHAR; "
               "VALUE i := 123; r := 4.5; c := 'K'; "
               "BEGIN WRITELN(i:1); WRITELN(r:4:1); WRITELN(c) END.")
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "123\n 4.5\nK\n")

    @requires_exe
    def test_value_initializes_string_runtime(self):
        src = "PROGRAM P; VAR s: STRING(10); VALUE s := 'Mr. Karate'; BEGIN WRITELN(s) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "Mr. Karate\n")

    @requires_exe
    def test_value_initializes_lstring_runtime(self):
        src = "PROGRAM P; VAR s: LSTRING(14); VALUE s := 'Mr. Karate'; BEGIN WRITELN(s) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "Mr. Karate\n")

    @requires_exe
    def test_value_initializes_packed_char_array_runtime(self):
        src = ("PROGRAM P; TYPE NAME = PACKED ARRAY[1..10] OF CHAR; "
               "VAR s: NAME; VALUE s := 'Mr. Karate'; BEGIN WRITELN(s) END.")
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "Mr. Karate\n")

    # ---- D-011: STRING ::N precision ----
    # Faithful 1981 default ignores ::N on strings (prints the whole value);
    # the truncating behavior is the opt-in -f string-precision extension.

    @requires_exe
    def test_string_precision_ignored_by_default_d011(self):
        src = "PROGRAM P; VAR s: STRING(5); BEGIN s := 'ABCDE'; WRITELN(s::3) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ABCDE\n")

    @requires_exe
    def test_string_precision_honored_with_feature_d011(self):
        src = "PROGRAM P; VAR s: STRING(5); BEGIN s := 'ABCDE'; WRITELN(s::3) END."
        rc, out = build_and_run(src, features={"string-precision": True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ABC\n")

    @requires_exe
    def test_string_width_still_pads_and_ignores_precision_by_default_d011(self):
        # P:M:N — width M still pads; N is ignored by default.
        src = "PROGRAM P; VAR s: STRING(5); BEGIN s := 'ABCDE'; WRITELN(s:7:3) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "  ABCDE\n")


@requires_exe
class TestGotoCodegen(unittest.TestCase):
    """GOTO / labeled-statement lowering.

    Labels are pre-created as LLVM blocks for the whole routine body before
    its statements are lowered, so a GOTO resolves to a block whether the
    target label appears earlier (backward) or later (forward) in the source.
    """

    def test_backward_goto_forms_a_loop(self):
        src = (
            "PROGRAM P; LABEL 1; VAR i: INTEGER; BEGIN "
            "i := 0; 1: i := i + 1; IF i < 5 THEN GOTO 1; WRITELN('i=', i) END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "i=5\n")

    def test_forward_goto_skips_dead_code(self):
        # The WRITELN between the GOTO and its forward target must not run.
        src = (
            "PROGRAM P; LABEL skip; VAR i: INTEGER; BEGIN "
            "i := 1; IF i = 1 THEN GOTO skip; "
            "WRITELN('NOPE'); skip: WRITELN('ok') END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ok\n")

    def test_goto_escapes_nested_loops(self):
        # The canonical use: jump clear out of doubly-nested FOR loops.
        src = (
            "PROGRAM P; LABEL 99; VAR i, j: INTEGER; BEGIN "
            "FOR i := 1 TO 5 DO FOR j := 1 TO 5 DO "
            "IF (i * j) = 6 THEN BEGIN WRITELN(i, ' ', j); GOTO 99 END; "
            "99: WRITELN('done') END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "2 3\ndone\n")

    def test_goto_is_routine_local(self):
        # A label inside a procedure is reachable by a GOTO within that
        # procedure; the program body has its own independent label scope.
        src = (
            "PROGRAM P; "
            "PROCEDURE Count(n: INTEGER); LABEL 10; VAR k: INTEGER; BEGIN "
            "k := 0; 10: k := k + 1; IF k < n THEN GOTO 10; WRITELN('k=', k) END; "
            "BEGIN Count(3) END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "k=3\n")

    def test_labeled_loop_serves_as_both_goto_and_cycle_target(self):
        # A numeric label on a WHILE is simultaneously a GOTO target and the
        # loop label used by CYCLE; both must resolve correctly.
        src = (
            "PROGRAM P; LABEL 1, 2; VAR i: INTEGER; BEGIN i := 0; "
            "1: WHILE i < 10 DO BEGIN i := i + 1; "
            "IF i = 3 THEN CYCLE 1; IF i = 7 THEN GOTO 2; WRITELN(i) END; "
            "2: WRITELN('end=', i) END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "1\n2\n4\n5\n6\nend=7\n")


@requires_llvm
class TestGotoIR(unittest.TestCase):
    """IR-shape checks for GOTO that don't need a native toolchain."""

    def test_goto_emits_branch_to_label_block(self):
        src = (
            "PROGRAM P; LABEL 1; VAR i: INTEGER; BEGIN "
            "i := 0; 1: i := i + 1; IF i < 5 THEN GOTO 1 END."
        )
        ir = compile_to_ir(src)
        # A dedicated block exists for label 1 and is branched to by the GOTO.
        self.assertIn('label_1', ir)
        self.assertIn('br label %"label_1', ir)

    def test_goto_to_undefined_label_is_rejected(self):
        src = "PROGRAM P; BEGIN GOTO 7 END."
        with self.assertRaises(Exception) as ctx:
            compile_to_ir(src)
        self.assertIn('label', str(ctx.exception).lower())


@requires_exe
class TestGotoCodegen(unittest.TestCase):
    """GOTO / labeled-statement lowering.

    Labels are pre-created as LLVM blocks for the whole routine before its body
    is lowered, so both backward and forward GOTOs resolve, including jumps out
    of nested loops.  Labels may be integers or identifiers, are routine-local,
    and a labeled loop remains a valid BREAK/CYCLE target as well.
    """

    def test_backward_goto_loop(self):
        src = (
            "PROGRAM P; LABEL 1; VAR i: INTEGER; "
            "BEGIN i := 0; 1: i := i + 1; IF i < 5 THEN GOTO 1; "
            "WRITELN('i=', i) END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "i=5\n")

    def test_forward_goto_skips_dead_code(self):
        src = (
            "PROGRAM P; LABEL skip; VAR i: INTEGER; "
            "BEGIN i := 1; IF i = 1 THEN GOTO skip; "
            "WRITELN('NOPE'); skip: WRITELN('ok') END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ok\n")

    def test_goto_escapes_nested_loops(self):
        src = (
            "PROGRAM P; LABEL 99; VAR i, j: INTEGER; "
            "BEGIN "
            "  FOR i := 1 TO 5 DO "
            "    FOR j := 1 TO 5 DO "
            "      IF (i * j) = 6 THEN BEGIN WRITELN(i, ' ', j); GOTO 99 END; "
            "  99: WRITELN('out') "
            "END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "2 3\nout\n")

    def test_goto_is_routine_local(self):
        # A procedure-local label and a program-level label of the same numeric
        # id are independent: each GOTO targets its own routine's label.
        src = (
            "PROGRAM P; LABEL 10; VAR i: INTEGER; "
            "PROCEDURE Q; LABEL 10; VAR k: INTEGER; "
            "BEGIN k := 0; 10: k := k + 1; IF k < 3 THEN GOTO 10; "
            "WRITELN('q=', k) END; "
            "BEGIN i := 0; 10: i := i + 1; IF i < 2 THEN GOTO 10; "
            "Q; WRITELN('p=', i) END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "q=3\np=2\n")

    def test_labeled_loop_is_still_break_cycle_target(self):
        # The same label drives a CYCLE (continue) and coexists with a GOTO that
        # exits to a second label.
        src = (
            "PROGRAM P; LABEL 1, 2; VAR i: INTEGER; "
            "BEGIN i := 0; "
            "  1: WHILE i < 10 DO BEGIN "
            "       i := i + 1; "
            "       IF i = 3 THEN CYCLE 1; "
            "       IF i = 7 THEN GOTO 2; "
            "       WRITE(i, ' ') END; "
            "  2: WRITELN('end=', i) "
            "END."
        )
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "1 2 4 5 6 end=7\n")

    def test_undefined_goto_label_is_rejected(self):
        from pascal1981.codegen.base import CodegenError
        src = "PROGRAM P; BEGIN GOTO 7 END."
        with self.assertRaises(CodegenError):
            compile_to_ir(src)

    @requires_llvm
    def test_goto_lowers_to_branch_into_label_block(self):
        # IR sanity: a label block is materialized and the GOTO branches to it.
        src = (
            "PROGRAM P; LABEL 1; VAR i: INTEGER; "
            "BEGIN i := 0; 1: i := i + 1; IF i < 5 THEN GOTO 1 END."
        )
        ir = compile_to_ir(src)
        self.assertIn("label_1", ir)
        self.assertIn("br label %\"label_1", ir)
