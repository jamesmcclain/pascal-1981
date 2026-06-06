"""
Parser accept/reject test suite.

Tests the lexer and parser over the fixture corpus (should_pass, should_fail),
without involving the type checker or any codegen.

Runs in-process; no subprocess or stdout grepping.
No llvmlite dependency.
"""

import os
import unittest
from pathlib import Path

from tests.support import parse_source, LexerError, ParserError


class TestParserAccept(unittest.TestCase):
    """Test that should_pass/ programs are accepted."""

    def setUp(self):
        """Load all should_pass fixtures."""
        fixtures_dir = Path(__file__).parent / "fixtures" / "parser" / "should_pass"
        self.files = sorted(fixtures_dir.glob("*.pas"))
        self.assertEqual(len(self.files), 19, f"Expected 19 should_pass fixtures, found {len(self.files)}")

    def test_parser_accepts_all_should_pass(self):
        """Each should_pass/ file must parse without raising."""
        for fixture in self.files:
            src = fixture.read_text()
            with self.subTest(file=fixture.name):
                try:
                    parse_source(src)
                except (LexerError, ParserError) as e:
                    self.fail(f"{fixture.name} should pass but raised {type(e).__name__}: {e}")


class TestParserReject(unittest.TestCase):
    """Test that should_fail/ programs are rejected."""

    def setUp(self):
        """Load all should_fail fixtures."""
        fixtures_dir = Path(__file__).parent / "fixtures" / "parser" / "should_fail"
        self.files = sorted(fixtures_dir.glob("*.pas"))
        self.assertEqual(len(self.files), 15, f"Expected 15 should_fail fixtures, found {len(self.files)}")

    def test_parser_rejects_all_should_fail(self):
        """Each should_fail/ file must raise LexerError or ParserError (not any other exception)."""
        for fixture in self.files:
            src = fixture.read_text()
            with self.subTest(file=fixture.name):
                with self.assertRaises((LexerError, ParserError),
                                       msg=f"{fixture.name} should fail but was accepted"):
                    parse_source(src)


class TestParserJudgmentCalls(unittest.TestCase):
    """Dialect decisions promoted from informational fixtures to assertions."""

    def test_write_field_width_passes(self):
        """WRITE/WRITELN field-width syntax is valid only for the write family."""
        fixture = Path(__file__).parent / "fixtures" / "parser" / "judgment_calls" / "A_write_field_width.pas"
        try:
            parse_source(fixture.read_text())
        except (LexerError, ParserError) as e:
            self.fail(f"{fixture.name} should pass but raised {type(e).__name__}: {e}")

    def test_multi_write_field_widths_pass(self):
        """Multiple WRITE arguments may each carry width/precision formatting."""
        parse_source(
            "PROGRAM P; VAR x, y : REAL; z : INTEGER; "
            "BEGIN WRITELN(x:5:2, y:5:2, z:4, 'Hello World') END."
        )

    def test_colon_args_on_ordinary_call_fail(self):
        """Ordinary procedure calls do not accept WRITE-style :width/:precision suffixes."""
        fixture = Path(__file__).parent / "fixtures" / "parser" / "judgment_calls" / "B_colon_args_any_call.pas"
        with self.assertRaises((LexerError, ParserError),
                               msg=f"{fixture.name} should fail but was accepted"):
            parse_source(fixture.read_text())

    def test_set_base_type_is_preserved(self):
        """SET OF should preserve its declared base type instead of collapsing to INTEGER."""
        ast = parse_source("PROGRAM P; VAR s: SET OF CHAR; BEGIN END.")
        decl = ast.block.decls[0]
        self.assertEqual(type(decl.type_expr).__name__, "SetType")
        self.assertEqual(type(decl.type_expr.base).__name__, "NamedType")
        self.assertEqual(decl.type_expr.base.name, "CHAR")

    def test_set_base_subrange_preserves_bounds(self):
        """A subrange set base (SET OF lo..hi) must keep both bounds rather than
        collapsing to the bare host type and discarding the range."""
        ast = parse_source("PROGRAM P; VAR s: SET OF 1..10; BEGIN END.")
        base = ast.block.decls[0].type_expr.base
        self.assertEqual(type(base).__name__, "SubrangeType")
        self.assertEqual(base.low.value, 1)
        self.assertEqual(base.high.value, 10)
        self.assertEqual(base.host, "INTEGER")

    def test_set_base_char_subrange_preserves_bounds(self):
        """A character subrange set base keeps its bounds and infers CHAR host."""
        ast = parse_source("PROGRAM P; VAR s: SET OF 'A'..'Z'; BEGIN END.")
        base = ast.block.decls[0].type_expr.base
        self.assertEqual(type(base).__name__, "SubrangeType")
        self.assertEqual(base.low.value, "'A'")
        self.assertEqual(base.high.value, "'Z'")
        self.assertEqual(base.host, "CHAR")

    def test_set_base_named_const_subrange_preserves_bounds(self):
        """A subrange with named-constant bounds keeps the identifiers; the host
        type is left unresolved (None) for the type checker to determine."""
        ast = parse_source("PROGRAM P; CONST lo = 1; hi = 9; VAR s: SET OF lo..hi; BEGIN END.")
        base = ast.block.decls[-1].type_expr.base
        self.assertEqual(type(base).__name__, "SubrangeType")
        self.assertEqual(base.low.name, "lo")
        self.assertEqual(base.high.name, "hi")
        self.assertIsNone(base.host)

    def test_typed_set_constructor_with_range_parses(self):
        """Type-prefixed set constructors preserve the type identifier."""
        ast = parse_source("PROGRAM P; TYPE S = SET OF 1..10; VAR x: S; BEGIN x := S[1..3] END.")
        expr = ast.block.body[0].expr
        self.assertEqual(type(expr).__name__, "SetConstructor")
        self.assertEqual(expr.type_name, "S")
        self.assertEqual(type(expr.elements[0]).__name__, "RangeExpr")

    def test_array_indexing_still_parses_as_designator(self):
        """Plain IDENTIFIER[...] without '..' remains array indexing."""
        ast = parse_source("PROGRAM P; VAR a: ARRAY[1..3] OF INTEGER; x: INTEGER; BEGIN x := a[1] END.")
        expr = ast.block.body[0].expr
        self.assertEqual(type(expr).__name__, "Designator")

    def test_identifier_labels_parse_as_labels(self):
        """Identifier labels should be legal in both LABEL declarations and label statements."""
        ast = parse_source("PROGRAM P; LABEL start; BEGIN start: END.")
        self.assertEqual(ast.block.decls[0].labels, ['start'])
        self.assertEqual(type(ast.block.body[0]).__name__, 'LabelStmt')
        self.assertEqual(ast.block.body[0].label, 'start')

    def test_labeled_break_and_cycle_parse_identifier_labels(self):
        """BREAK/CYCLE should accept an optional identifier label target."""
        ast = parse_source(
            "PROGRAM P; LABEL done, again; BEGIN WHILE TRUE DO BEGIN BREAK done; CYCLE again END; done: END."
        )
        loop_body = ast.block.body[0].body
        self.assertEqual(type(loop_body.stmts[0]).__name__, 'BreakStmt')
        self.assertEqual(loop_body.stmts[0].label, 'done')
        self.assertEqual(type(loop_body.stmts[1]).__name__, 'CycleStmt')
        self.assertEqual(loop_body.stmts[1].label, 'again')

    def test_labeled_break_and_cycle_parse_numeric_labels(self):
        """BREAK/CYCLE should also accept numeric labels."""
        ast = parse_source(
            "PROGRAM P; LABEL 10, 20; BEGIN WHILE TRUE DO BEGIN BREAK 10; CYCLE 20 END; 10: END."
        )
        loop_body = ast.block.body[0].body
        self.assertEqual(loop_body.stmts[0].label, 10)
        self.assertEqual(loop_body.stmts[1].label, 20)

    def test_bare_break_and_cycle_still_parse(self):
        """Bare BREAK/CYCLE remain valid and carry no label target."""
        ast = parse_source("PROGRAM P; BEGIN WHILE TRUE DO BEGIN BREAK; CYCLE END END.")
        loop_body = ast.block.body[0].body
        self.assertIsNone(loop_body.stmts[0].label)
        self.assertIsNone(loop_body.stmts[1].label)

    def test_short_circuit_and_then_or_else_parse(self):
        """Boolean conditions accept IBM Pascal short-circuit operators."""
        ast = parse_source(
            "PROGRAM P; VAR a, b: BOOLEAN; BEGIN IF a AND THEN b THEN WRITELN(1); WHILE a OR ELSE b DO a := FALSE END."
        )
        self.assertEqual(ast.block.body[0].cond.op, 'AND_THEN')
        self.assertEqual(ast.block.body[1].cond.op, 'OR_ELSE')

    def test_ads_factor_and_address_pointer_types_parse(self):
        """ADS expression and ADR OF / ADS OF type prefixes are manual address forms."""
        ast = parse_source(
            "PROGRAM P; VAR x: INTEGER; a: ADR OF INTEGER; s: ADS OF INTEGER; BEGIN a := ADR x; s := ADS x END."
        )
        adr_decl = ast.block.decls[1]
        ads_decl = ast.block.decls[2]
        self.assertEqual(adr_decl.type_expr.flavor, 'ADR')
        self.assertEqual(ads_decl.type_expr.flavor, 'ADS')
        self.assertEqual(type(ast.block.body[0].expr).__name__, 'AdrExpr')
        self.assertEqual(type(ast.block.body[1].expr).__name__, 'AdsExpr')

    def test_parameter_modes_parse_var_const_and_far_forms(self):
        """Parameter modes should preserve VAR/CONST and VARS/CONSTS spelling."""
        ast = parse_source("PROGRAM P; PROCEDURE Q(VAR a: INTEGER; VARS b: INTEGER; CONST c: INTEGER; CONSTS d: INTEGER); BEGIN END; BEGIN END.")
        modes = [p.mode for p in ast.block.decls[0].params]
        self.assertEqual(modes, ['VAR', 'VARS', 'CONST', 'CONSTS'])

    def test_confirmed_attributes_parse(self):
        """The six confirmed attributes should parse in bracketed lists."""
        ast = parse_source(
            "PROGRAM P; VAR [STATIC, READONLY] x: INTEGER; PROCEDURE Q [PUBLIC, EXTERN, PURE]; BEGIN END; BEGIN END."
        )
        var_decl = ast.block.decls[0]
        proc_decl = ast.block.decls[1]
        self.assertEqual(var_decl.attributes, ['STATIC', 'READONLY'])
        self.assertEqual(proc_decl.attributes, ['PUBLIC', 'EXTERN', 'PURE'])

    def test_manual_radix_integer_constant(self):
        """The manual radix form n#digits should lex and parse as an integer constant."""
        ast = parse_source("PROGRAM P; CONST MASK = 16#FF; BEGIN END.")
        const = ast.block.decls[0]
        self.assertEqual(const.value.value, 255)

    def test_manual_radix_integer_in_expression(self):
        """Radix literals must also work in expression/factor position, not just
        in constant declarations (regression: the factor path used to re-parse the
        lexeme and crash on '16#FF')."""
        for src, expected in [
            ("PROGRAM P; VAR x: INTEGER; BEGIN x := 16#FF END.", 255),
            ("PROGRAM P; VAR x: INTEGER; BEGIN x := 2#1010 END.", 10),
            ("PROGRAM P; VAR x: INTEGER; BEGIN x := 8#17 END.", 15),
        ]:
            ast = parse_source(src)
            assign = ast.block.body[0]
            self.assertEqual(assign.expr.value, expected)

    def test_multi_dimensional_subscript_desugars(self):
        """Comma-separated subscripts should parse as chained INDEX selectors."""
        ast = parse_source("PROGRAM P; VAR a: ARRAY[1..3] OF ARRAY[1..4] OF INTEGER; i, j: INTEGER; BEGIN a[i,j] := 1 END.")
        assign = ast.block.body[0]
        self.assertEqual(type(assign.target).__name__, "Designator")
        self.assertEqual(len(assign.target.selectors), 2)
        self.assertEqual([s.kind for s in assign.target.selectors], ["INDEX", "INDEX"])

    def test_for_static_passes(self):
        """The manual permits an optional STATIC after FOR."""
        fixture = Path(__file__).parent / "fixtures" / "parser" / "should_pass" / "16_for_static.pas"
        try:
            ast = parse_source(fixture.read_text())
        except (LexerError, ParserError) as e:
            self.fail(f"{fixture.name} should pass but raised {type(e).__name__}: {e}")
        self.assertTrue(ast.block.body[0].static)

    def test_dollar_hex_is_rejected(self):
        """The '$FF' hex form is not part of the IBM Pascal 2.0 dialect (the
        manual's only hex notation is the n#digits radix form). It must be
        rejected in both constant and expression position."""
        for src in [
            "PROGRAM P; CONST MASK = $FF; BEGIN END.",
            "PROGRAM P; VAR x: INTEGER; BEGIN x := $FF END.",
        ]:
            with self.assertRaises((LexerError, ParserError)):
                parse_source(src)


if __name__ == '__main__':
    unittest.main()
