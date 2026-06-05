"""
Type checker test suite.

Combines all semantic validation, type rules, and module-level semantics tests.
Organized by topic into TestCase classes.

Runs in-process (no subprocess); no llvmlite dependency.
"""

import unittest

from tests.support import typecheck_source, typecheck_module


class TestVariableScope(unittest.TestCase):
    """Variable scope and name resolution."""

    def test_undefined_variable(self):
        """Undefined variable is an error."""
        result = typecheck_source("PROGRAM P; BEGIN WRITELN(x) END.")
        self.assertFalse(result.success)
        self.assertIn("Undefined", " ".join(str(e) for e in result.errors))

    def test_defined_variable(self):
        """Defined variable passes type check."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN WRITELN(x) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_scope_isolation_procedure(self):
        """Procedure scope does not leak to outer scope."""
        result = typecheck_source(
            "PROGRAM P; "
            "PROCEDURE P1; VAR x: INTEGER; BEGIN END; "
            "BEGIN WRITELN(x) END."
        )
        self.assertFalse(result.success)
        self.assertIn("Undefined", " ".join(str(e) for e in result.errors))

    def test_const_declaration(self):
        """Const declaration is valid."""
        result = typecheck_source("PROGRAM P; CONST x = 42; BEGIN END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_lstring_and_string_types_resolve(self):
        """STRING(n) and LSTRING(n) type annotations resolve."""
        result = typecheck_source("PROGRAM P; VAR a: STRING(10); VAR b: LSTRING(10); BEGIN END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_null_is_the_empty_lstring_constant(self):
        """NULL is the predeclared empty LSTRING constant."""
        result = typecheck_source("PROGRAM P; VAR s: LSTRING(10); BEGIN s := NULL END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_null_is_not_a_pointer_constant(self):
        """NULL is a string constant, not a pointer constant."""
        result = typecheck_source("PROGRAM P; VAR p: ^INTEGER; BEGIN p := NULL END.")
        self.assertFalse(result.success)
        self.assertIn("Cannot assign", " ".join(str(e) for e in result.errors))

    def test_string_literal_assigns_when_capacity_fits(self):
        """String literals can initialize compatible STRING/LSTRING storage."""
        result = typecheck_source("PROGRAM P; VAR a: STRING(10); VAR b: LSTRING(10); BEGIN a := 'abc'; b := 'abc' END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_string_literal_assignment_rejects_overflow(self):
        """String literal assignment rejects destinations that are too small."""
        result = typecheck_source("PROGRAM P; VAR a: STRING(2); BEGIN a := 'abc' END.")
        self.assertFalse(result.success)
        self.assertIn("Cannot assign", " ".join(str(e) for e in result.errors))

    def test_predeclared_maxint_maxword_constants(self):
        """MAXINT and MAXWORD are predeclared constants."""
        result = typecheck_source("PROGRAM P; CONST hi = MAXINT; BEGIN WRITELN(hi); WRITELN(MAXWORD) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_predeclared_text_input_output_string_names(self):
        """TEXT, INPUT, OUTPUT, and STRING are predeclared names."""
        result = typecheck_source("PROGRAM P; VAR f: TEXT; BEGIN WRITELN(INPUT); WRITELN(OUTPUT); WRITELN(f) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_shadowing_nested_scope(self):
        """Variable shadowing in nested scope is allowed."""
        result = typecheck_source(
            "PROGRAM P; "
            "VAR x: INTEGER; "
            "PROCEDURE P1; VAR x: INTEGER; BEGIN x := 1 END; "
            "BEGIN x := 2 END."
        )
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))


class TestTypeCompatibility(unittest.TestCase):
    """Type compatibility and assignment rules."""

    def test_integer_to_integer_assignment(self):
        """INTEGER to INTEGER assignment is valid."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := 42 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_real_to_integer_assignment_error(self):
        """REAL to INTEGER assignment is an error."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := 3.14 END.")
        self.assertFalse(result.success)
        self.assertIn("Cannot assign", " ".join(str(e) for e in result.errors))

    def test_integer_to_boolean_assignment_error(self):
        """INTEGER to BOOLEAN assignment is an error."""
        result = typecheck_source("PROGRAM P; VAR b: BOOLEAN; BEGIN b := 1 END.")
        self.assertFalse(result.success)
        self.assertIn("Cannot assign", " ".join(str(e) for e in result.errors))

    def test_boolean_in_condition(self):
        """BOOLEAN in IF condition is valid."""
        result = typecheck_source("PROGRAM P; VAR b: BOOLEAN; VAR x: INTEGER; BEGIN IF b THEN x := 1 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_multiple_assignments(self):
        """Multiple assignments to same variable are valid."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := 1; x := 2; x := 3 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_set_type_declaration_and_assignment(self):
        """SET OF declarations resolve and accept compatible set constructors."""
        result = typecheck_source("PROGRAM P; TYPE S = SET OF 1..10; VAR x: S; BEGIN x := [1, 2..4] END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_set_membership_and_comparison(self):
        """Set operators typecheck to set/BOOLEAN as appropriate."""
        result = typecheck_source("PROGRAM P; VAR a, b: SET OF 1..10; VAR x: INTEGER; VAR ok: BOOLEAN; BEGIN ok := (x IN a) AND (a = b) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_typed_set_constructor_constant_range(self):
        """Type-prefixed set constructors are valid when all elements are constant."""
        result = typecheck_source("PROGRAM P; TYPE S = SET OF 1..10; VAR x: S; BEGIN x := S[1..3] END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_typed_set_constructor_rejects_variable_range(self):
        """Type-prefixed set constructors reject variable elements per the manual."""
        result = typecheck_source("PROGRAM P; TYPE S = SET OF 1..10; VAR x: S; VAR i, j: INTEGER; BEGIN x := S[i..j] END.")
        self.assertFalse(result.success)
        self.assertTrue(any("constant elements" in e.message for e in result.errors))

    def test_typed_set_constructor_prefix_must_be_set_type(self):
        """Typed set constructor prefixes must resolve to set types."""
        result = typecheck_source("PROGRAM P; TYPE N = INTEGER; VAR x: SET OF 1..10; BEGIN x := N[1..3] END.")
        self.assertFalse(result.success)
        self.assertTrue(any("must name a set type" in e.message for e in result.errors))

    def test_pred_typecheck(self):
        """PRED accepts one INTEGER argument and returns INTEGER."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := PRED(3) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_sqr_typecheck(self):
        """SQR accepts INTEGER/REAL and returns the same type."""
        int_result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := SQR(3) END.")
        self.assertTrue(int_result.success, msg=" ".join(str(e) for e in int_result.errors))
        real_result = typecheck_source("PROGRAM P; VAR x: REAL; BEGIN x := SQR(1.5) END.")
        self.assertTrue(real_result.success, msg=" ".join(str(e) for e in real_result.errors))

    def test_upper_lower_typecheck(self):
        """UPPER and LOWER accept array variables and return INTEGER bounds."""
        result = typecheck_source("PROGRAM P; VAR a: ARRAY[1..10] OF INTEGER; BEGIN WRITELN(UPPER(a)); WRITELN(LOWER(a)) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_hibyte_lobyte_typecheck(self):
        """HIBYTE and LOBYTE accept INTEGER arguments and return CHAR."""
        result = typecheck_source("PROGRAM P; VAR x: CHAR; BEGIN x := HIBYTE(4660); x := LOBYTE(4660) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_abs_and_sqrt_typecheck(self):
        """ABS accepts INTEGER/REAL and SQRT returns REAL."""
        result = typecheck_source("PROGRAM P; VAR x: REAL; BEGIN x := SQRT(ABS(-5)) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_nil_typecheck_for_pointer_assignment(self):
        """NIL is a typed null pointer constant."""
        result = typecheck_source("PROGRAM P; VAR p: ^INTEGER; BEGIN p := NIL END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_adr_ads_address_types_typecheck(self):
        """ADR OF and ADS OF variables accept matching address-of expressions."""
        result = typecheck_source(
            "PROGRAM P; VAR x: INTEGER; a: ADR OF INTEGER; s: ADS OF INTEGER; BEGIN a := ADR x; s := ADS x END."
        )
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_parameter_modes_typecheck(self):
        """VAR/VARS are writable references; CONST/CONSTS are read-only references."""
        ok = typecheck_source(
            "PROGRAM P; PROCEDURE Q(VAR a: INTEGER; VARS b: INTEGER; CONST c: INTEGER; CONSTS d: INTEGER); BEGIN a := 1; b := 2; WRITELN(c); WRITELN(d) END; BEGIN END."
        )
        self.assertTrue(ok.success, msg=" ".join(str(e) for e in ok.errors))
        bad = typecheck_source(
            "PROGRAM P; PROCEDURE Q(CONST c: INTEGER; CONSTS d: INTEGER); BEGIN c := 1; d := 2 END; BEGIN END."
        )
        self.assertFalse(bad.success)
        self.assertTrue(any("Cannot assign" in e.message for e in bad.errors))

    def test_readonly_variable_is_immutable(self):
        """READONLY variables must reject assignment."""
        result = typecheck_source("PROGRAM P; VAR [READONLY] x: INTEGER; BEGIN x := 1 END.")
        self.assertFalse(result.success)
        self.assertTrue(any("immutable" in e.message.lower() for e in result.errors))

    def test_pure_function_rejects_var_parameters(self):
        """PURE functions cannot take VAR/VARS parameters."""
        result = typecheck_source("PROGRAM P; FUNCTION F(VAR x: INTEGER): INTEGER [PURE]; BEGIN F := x END; BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any("PURE function" in e.message for e in result.errors))

    def test_pure_procedure_is_rejected(self):
        """PURE is only valid on functions."""
        result = typecheck_source("PROGRAM P; PROCEDURE P1 [PURE]; BEGIN END; BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any("PURE is only valid on functions" in e.message for e in result.errors))


class TestControlFlow(unittest.TestCase):
    """Control flow type validation (IF, WHILE, FOR, REPEAT, CASE)."""

    def test_if_boolean_condition(self):
        """IF with BOOLEAN condition is valid."""
        result = typecheck_source("PROGRAM P; BEGIN IF TRUE THEN WRITELN(1) END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_if_integer_condition_error(self):
        """IF with INTEGER condition is an error."""
        result = typecheck_source("PROGRAM P; BEGIN IF 42 THEN WRITELN(1) END.")
        self.assertFalse(result.success)
        self.assertIn("must be BOOLEAN", " ".join(str(e) for e in result.errors))

    def test_short_circuit_boolean_operands(self):
        """AND THEN / OR ELSE are valid for BOOLEAN operands."""
        result = typecheck_source(
            "PROGRAM P; VAR a, b: BOOLEAN; BEGIN IF a AND THEN b THEN WRITELN(1); IF a OR ELSE b THEN WRITELN(2) END."
        )
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_short_circuit_rejects_integer_operands(self):
        """AND THEN / OR ELSE are boolean-only, unlike ordinary bitwise AND/OR."""
        result = typecheck_source("PROGRAM P; VAR x, y: INTEGER; BEGIN IF x AND THEN y THEN WRITELN(1) END.")
        self.assertFalse(result.success)
        self.assertIn("AND_THEN", " ".join(str(e) for e in result.errors))

    def test_while_boolean_condition(self):
        """WHILE with BOOLEAN condition is valid."""
        result = typecheck_source(
            "PROGRAM P; VAR x: INTEGER; BEGIN x := 0; WHILE x < 10 DO x := x + 1 END."
        )
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_while_integer_condition_error(self):
        """WHILE with INTEGER condition is an error."""
        result = typecheck_source(
            "PROGRAM P; VAR x: INTEGER; BEGIN WHILE x DO x := x + 1 END."
        )
        self.assertFalse(result.success)
        self.assertIn("must be BOOLEAN", " ".join(str(e) for e in result.errors))

    def test_for_loop_integer_variable(self):
        """FOR with INTEGER loop variable is valid."""
        result = typecheck_source(
            "PROGRAM P; VAR i: INTEGER; BEGIN FOR i := 1 TO 10 DO WRITELN(i) END."
        )
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_for_loop_real_variable_error(self):
        """FOR with REAL loop variable is an error."""
        result = typecheck_source(
            "PROGRAM P; VAR r: REAL; BEGIN FOR r := 1.0 TO 10.0 DO WRITELN(r) END."
        )
        self.assertFalse(result.success)
        self.assertIn("must be INTEGER", " ".join(str(e) for e in result.errors))


class TestCallValidation(unittest.TestCase):
    """Function and procedure call validation."""

    def test_undefined_procedure_call_error(self):
        """Call to undefined procedure is an error."""
        result = typecheck_source("PROGRAM P; BEGIN UNDEFINED_PROC() END.")
        self.assertFalse(result.success)
        self.assertIn("Undefined", " ".join(str(e) for e in result.errors))

    def test_valid_procedure_call(self):
        """Call to defined procedure is valid."""
        result = typecheck_source("PROGRAM P; PROCEDURE P1; BEGIN END; BEGIN P1 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_procedure_with_parameters(self):
        """Procedure call with correct parameters is valid."""
        result = typecheck_source(
            "PROGRAM P; PROCEDURE P1(x: INTEGER); BEGIN END; BEGIN P1(42) END."
        )
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_integer_to_real_procedure_parameter(self):
        """INTEGER actual may flow to REAL formal parameter."""
        result = typecheck_source(
            "PROGRAM P; PROCEDURE P1(x: REAL); BEGIN END; BEGIN P1(42) END."
        )
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_undefined_function_error(self):
        """Call to undefined function is an error."""
        result = typecheck_source("PROGRAM P; BEGIN WRITELN(UNDEFINED_FUNC()) END.")
        self.assertFalse(result.success)
        self.assertIn("Undefined", " ".join(str(e) for e in result.errors))


class TestFunctionReturnTypes(unittest.TestCase):
    """Function return type validation."""

    def test_mismatched_return_type_error(self):
        """Function return with wrong type is an error."""
        result = typecheck_source(
            "PROGRAM P; "
            "FUNCTION F: INTEGER; BEGIN F := 3.14 END; "
            "BEGIN END."
        )
        self.assertFalse(result.success)
        self.assertIn("Cannot assign", " ".join(str(e) for e in result.errors))


class TestArrayTypeChecking(unittest.TestCase):
    """Array indexing and type validation."""

    def test_array_real_index_error(self):
        """Array index with REAL is an error."""
        result = typecheck_source(
            "PROGRAM P; VAR a: ARRAY[1..10] OF INTEGER; BEGIN a[1.5] := 42 END."
        )
        self.assertFalse(result.success)
        self.assertIn("Array index must be INTEGER", " ".join(str(e) for e in result.errors))


class TestArithmetic(unittest.TestCase):
    """Arithmetic and logic type checking."""

    def test_integer_arithmetic(self):
        """Integer arithmetic is valid."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := 1 + 2 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_mixed_type_arithmetic_error(self):
        """Mixing incompatible types in arithmetic is an error."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := 1 + TRUE END.")
        self.assertFalse(result.success)


class TestIntegration(unittest.TestCase):
    """Integration and edge cases."""

    def test_simple_program(self):
        """Simple valid program passes."""
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := 1 END.")
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_const_undefined_use_error(self):
        """Using undefined variable in const scope is an error."""
        result = typecheck_source("PROGRAM P; CONST z = 10; BEGIN WRITELN(y) END.")
        self.assertFalse(result.success)
        self.assertIn("Undefined", " ".join(str(e) for e in result.errors))

    def test_parameter_type_mismatch_error(self):
        """Parameter type mismatch is an error."""
        result = typecheck_source(
            "PROGRAM P; PROCEDURE P1(x: INTEGER); BEGIN END; BEGIN P1(1.5) END."
        )
        self.assertFalse(result.success)
        # Error message may say "type mismatch" or similar
        self.assertTrue(
            not result.success,
            msg="Expected type error on parameter mismatch"
        )


class TestModuleSemantics(unittest.TestCase):
    """Interface/implementation/module-level semantics (multi-file module tests)."""

    def test_implementation_matches_interface_procedure(self):
        """Implementation procedure signature must match interface."""
        iface = """INTERFACE;
   UNIT TEST (Proc1);
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;

"""
        impl = """IMPLEMENTATION OF TEST;
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_implementation_matches_interface_function(self):
        """Implementation function signature must match interface."""
        iface = """INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
END;

"""
        impl = """IMPLEMENTATION OF TEST;
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
  Func1 := X + 1
END;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_implementation_with_private_procedures(self):
        """Implementation can include procedures not in interface."""
        iface = """INTERFACE;
   UNIT TEST (DoWork);
PROCEDURE DoWork;
BEGIN
END;

"""
        impl = """IMPLEMENTATION OF TEST;
PROCEDURE DoWork;
BEGIN
END;
PROCEDURE Helper;
BEGIN
END;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_missing_implementation_error(self):
        """Missing procedure in implementation is an error."""
        iface = """INTERFACE;
   UNIT TEST (Proc1, Proc2);
PROCEDURE Proc1;
PROCEDURE Proc2;
BEGIN
END;

"""
        impl = """IMPLEMENTATION OF TEST;
PROCEDURE Proc1;
BEGIN
END;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertFalse(result.success)

    def test_parameter_count_mismatch_error(self):
        """Parameter count mismatch between interface and implementation is an error."""
        iface = """INTERFACE;
   UNIT TEST (Proc1);
PROCEDURE Proc1(X: INTEGER; Y: INTEGER);
BEGIN
END;

"""
        impl = """IMPLEMENTATION OF TEST;
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertFalse(result.success)

    def test_parameter_type_mismatch_error(self):
        """Parameter type mismatch between interface and implementation is an error."""
        iface = """INTERFACE;
   UNIT TEST (Proc1);
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;

"""
        impl = """IMPLEMENTATION OF TEST;
PROCEDURE Proc1(X: REAL);
BEGIN
END;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertFalse(result.success)

    def test_function_return_type_mismatch_error(self):
        """Function return type mismatch is an error."""
        iface = """INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1: INTEGER;
BEGIN
END;

"""
        impl = """IMPLEMENTATION OF TEST;
FUNCTION Func1: REAL;
BEGIN
  Func1 := 0.0
END;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, impl_code=impl)
        self.assertFalse(result.success)

    def test_program_selective_module_import(self):
        """Program can selectively import named symbols from a module."""
        iface = """INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
END;

"""
        prog = """PROGRAM Prog (OUTPUT);
USES TEST (Func1);
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, prog_code=prog, module_name='TEST')
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_program_all_module_import(self):
        """Program can import all exported symbols from a module."""
        iface = """INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
END;

"""
        prog = """PROGRAM Prog (OUTPUT);
USES TEST;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, prog_code=prog, module_name='TEST')
        self.assertTrue(result.success, msg=" ".join(str(e) for e in result.errors))

    def test_missing_module_error(self):
        """Missing module in USES clause is an error."""
        prog = """PROGRAM Prog (OUTPUT);
USES NONEXISTENT;
BEGIN
END.
"""
        result = typecheck_module(prog_code=prog)
        self.assertFalse(result.success)

    def test_non_exported_symbol_import_error(self):
        """Importing non-exported symbol is an error."""
        iface = """INTERFACE;
   UNIT TEST (PublicFunc);
FUNCTION PublicFunc: INTEGER;
FUNCTION PrivateFunc: INTEGER;
BEGIN
END;

"""
        prog = """PROGRAM Prog (OUTPUT);
USES TEST (PrivateFunc);
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, prog_code=prog, module_name='TEST')
        self.assertFalse(result.success)


if __name__ == '__main__':
    unittest.main()
