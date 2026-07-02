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

from tests.support import LexerError, ParserError, parse_source


class TestParserAccept(unittest.TestCase):
    """Test that should_pass/ programs are accepted."""

    def setUp(self):
        """Load all should_pass fixtures."""
        fixtures_dir = Path(__file__).parent / "fixtures" / "parser" / "should_pass"
        self.files = sorted(fixtures_dir.glob("*.pas"))
        self.assertGreaterEqual(len(self.files), 34, f"Expected at least 34 should_pass fixtures, found {len(self.files)}")

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
        self.assertGreaterEqual(len(self.files), 15, f"Expected at least 15 should_fail fixtures, found {len(self.files)}")

    def test_parser_rejects_all_should_fail(self):
        """Each should_fail/ file must raise LexerError or ParserError (not any other exception)."""
        for fixture in self.files:
            src = fixture.read_text()
            with self.subTest(file=fixture.name):
                with self.assertRaises((LexerError, ParserError), msg=f"{fixture.name} should fail but was accepted"):
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
        parse_source("PROGRAM P; VAR x, y : REAL; z : INTEGER; "
                     "BEGIN WRITELN(x:5:2, y:5:2, z:4, 'Hello World') END.")

    def test_colon_args_on_ordinary_call_fail(self):
        """Ordinary procedure calls do not accept WRITE-style :width/:precision suffixes."""
        fixture = Path(__file__).parent / "fixtures" / "parser" / "judgment_calls" / "B_colon_args_any_call.pas"
        with self.assertRaises((LexerError, ParserError), msg=f"{fixture.name} should fail but was accepted"):
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
        self.assertEqual(base.low.value, "A")
        self.assertEqual(base.high.value, "Z")
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

    def test_value_empty_set_constructor_parses(self):
        """VALUE declarations accept set constants such as the empty set []."""
        ast = parse_source("PROGRAM P; TYPE S = SET OF 1..10; VAR x: S; VALUE x := []; BEGIN END.")
        value = ast.block.decls[-1].value
        self.assertEqual(type(value).__name__, "SetConstructor")
        self.assertEqual(value.elements, [])

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
        ast = parse_source("PROGRAM P; LABEL done, again; BEGIN WHILE TRUE DO BEGIN BREAK done; CYCLE again END; done: END.")
        loop_body = ast.block.body[0].body
        self.assertEqual(type(loop_body.stmts[0]).__name__, 'BreakStmt')
        self.assertEqual(loop_body.stmts[0].label, 'done')
        self.assertEqual(type(loop_body.stmts[1]).__name__, 'CycleStmt')
        self.assertEqual(loop_body.stmts[1].label, 'again')

    def test_labeled_break_and_cycle_parse_numeric_labels(self):
        """BREAK/CYCLE should also accept numeric labels."""
        ast = parse_source("PROGRAM P; LABEL 10, 20; BEGIN WHILE TRUE DO BEGIN BREAK 10; CYCLE 20 END; 10: END.")
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
        ast = parse_source("PROGRAM P; VAR a, b: BOOLEAN; BEGIN IF a AND THEN b THEN WRITELN(1); WHILE a OR ELSE b DO a := FALSE END.")
        self.assertEqual(ast.block.body[0].cond.op, 'AND_THEN')
        self.assertEqual(ast.block.body[1].cond.op, 'OR_ELSE')

    def test_ads_factor_and_address_pointer_types_parse(self):
        """ADS expression and ADR OF / ADS OF type prefixes are manual address forms."""
        ast = parse_source("PROGRAM P; VAR x: INTEGER; a: ADR OF INTEGER; s: ADS OF INTEGER; BEGIN a := ADR x; s := ADS x END.")
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
        ast = parse_source("PROGRAM P; VAR [STATIC, READONLY] x: INTEGER; PROCEDURE Q [PUBLIC, EXTERN, PURE]; BEGIN END; BEGIN END.")
        var_decl = ast.block.decls[0]
        proc_decl = ast.block.decls[1]
        self.assertEqual([a.name for a in var_decl.attributes], ['STATIC', 'READONLY'])
        self.assertEqual([a.name for a in proc_decl.attributes], ['PUBLIC', 'EXTERN', 'PURE'])

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
        fixture = Path(__file__).parent / "fixtures" / "parser" / "should_pass" / "19_for_static.pas"
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


class TestMetacommands(unittest.TestCase):
    """Unit tests for §9.5 metacommand infrastructure."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _lex(self, src: str):
        from pascal1981.lexer import Lexer
        return Lexer(src)

    def _flags_after(self, src: str) -> dict:
        """Return the meta_flags dict on the lexer after tokenizing `src`."""
        from pascal1981.lexer import Lexer
        lex = Lexer(src)
        lex.tokenize()
        return dict(lex.meta_flags)

    def _tokens(self, src: str):
        from pascal1981.lexer import Lexer
        return Lexer(src).tokenize()

    # ------------------------------------------------------------------
    # Tier-2 ON/OFF flags
    # ------------------------------------------------------------------

    def test_tier2_flag_on(self):
        flags = self._flags_after('{$RANGECK+}')
        self.assertTrue(flags['RANGECK'])

    def test_tier2_flag_off(self):
        flags = self._flags_after('{$RANGECK-}')
        self.assertFalse(flags['RANGECK'])

    def test_debug_master_off_sets_subflags(self):
        """$DEBUG- must disable all documented sub-flags."""
        flags = self._flags_after('{$DEBUG-}')
        self.assertFalse(flags['DEBUG'])
        for sub in ('ENTRY', 'INDEXCK', 'INITCK', 'MATHCK', 'NILCK', 'RANGECK', 'STACKCK'):
            self.assertFalse(flags[sub], f'{sub} should be off after $DEBUG-')

    def test_debug_master_on_sets_subflags(self):
        flags = self._flags_after('{$DEBUG-} {$DEBUG+}')
        self.assertTrue(flags['DEBUG'])
        for sub in ('ENTRY', 'INDEXCK', 'INITCK', 'MATHCK', 'NILCK', 'RANGECK', 'STACKCK'):
            self.assertTrue(flags[sub], f'{sub} should be on after $DEBUG+')

    def test_line_plus_implies_entry_plus(self):
        """$LINE+ must automatically set $ENTRY+ (manual §4-20)."""
        flags = self._flags_after('{$ENTRY-} {$LINE+}')
        self.assertTrue(flags['LINE'])
        self.assertTrue(flags['ENTRY'])

    def test_comma_separated_flags(self):
        flags = self._flags_after('{$RANGECK-, $INDEXCK-}')
        self.assertFalse(flags['RANGECK'])
        self.assertFalse(flags['INDEXCK'])

    def test_flag_stamped_on_tokens(self):
        """RANGECK flag in effect at a token must appear on that token's flags."""
        toks = self._tokens('{$RANGECK-} PROGRAM P; BEGIN END.')
        # First real token after the metacommand is PROGRAM
        prog_tok = next(t for t in toks if t.kind == 'PROGRAM')
        self.assertFalse(prog_tok.flags.get('RANGECK', True))

    # ------------------------------------------------------------------
    # Tier-1 listing metacommands (must not raise)
    # ------------------------------------------------------------------

    def test_tier1_all_absorbed_silently(self):
        tier1 = ("{$LIST-} {$LIST+} {$OCODE-} {$SYMTAB-} "
                 "{$TITLE:'Test'} {$SUBTITLE:'Sub'} "
                 "{$PAGE:1} {$PAGE} {$PAGEIF:5} {$PAGESIZE:60} "
                 "{$LINESIZE:132} {$ERRORS:10} {$SKIP:3}")
        try:
            self._flags_after(tier1)
        except Exception as e:
            self.fail(f"Tier-1 metacommands raised unexpectedly: {e}")

    # ------------------------------------------------------------------
    # $PUSH / $POP
    # ------------------------------------------------------------------

    def test_push_pop_round_trip(self):
        """{$PUSH} {$RANGECK-} {$POP} must restore RANGECK to True."""
        flags = self._flags_after('{$PUSH} {$RANGECK-} {$POP}')
        self.assertTrue(flags['RANGECK'])

    def test_push_pop_multiple_flags(self):
        flags = self._flags_after('{$PUSH} {$RANGECK-} {$INDEXCK-} {$POP}')
        self.assertTrue(flags['RANGECK'])
        self.assertTrue(flags['INDEXCK'])

    def test_pop_on_empty_stack_is_silent(self):
        try:
            self._flags_after('{$POP}')
        except Exception as e:
            self.fail(f"$POP on empty stack raised: {e}")

    # ------------------------------------------------------------------
    # $IF / $THEN / $ELSE / $END
    # ------------------------------------------------------------------

    def _token_kinds(self, src: str) -> list:
        return [t.kind for t in self._tokens(src) if t.kind != 'EOF']

    def test_if_true_includes_then_branch(self):
        """$IF 1 $THEN: body inside should be tokenized."""
        kinds = self._token_kinds('PROGRAM P; BEGIN {$IF 1 $THEN} WRITELN {$END} END.')
        self.assertIn('IDENTIFIER', kinds)  # WRITELN is an identifier here

    def test_if_false_skips_then_branch(self):
        """$IF 0 $THEN: identifiers inside skipped block must not appear."""
        identifiers = [t for t in self._tokens('PROGRAM P; BEGIN {$IF 0 $THEN} GARBAGE {$END} END.') if t.kind == 'IDENTIFIER']
        names = [t.value for t in identifiers]
        self.assertNotIn('GARBAGE', names)

    def test_if_true_skips_else_branch(self):
        """True condition: else-branch garbage must be skipped."""
        kinds = self._token_kinds('PROGRAM P; BEGIN {$IF 1 $THEN} WRITELN {$ELSE} @@@ BAD @@@ {$END} END.')
        # WRITELN identifier present, no other stray tokens
        identifiers = [t for t in self._tokens('PROGRAM P; BEGIN {$IF 1 $THEN} WRITELN {$ELSE} @@@ BAD @@@ {$END} END.') if t.kind == 'IDENTIFIER']
        names = [t.value for t in identifiers]
        self.assertIn('WRITELN', names)
        self.assertNotIn('BAD', names)

    def test_if_false_uses_else_branch(self):
        """False condition: else-branch must be tokenized."""
        identifiers = [t for t in self._tokens('PROGRAM P; BEGIN {$IF 0 $THEN} BAD {$ELSE} WRITELN {$END} END.') if t.kind == 'IDENTIFIER']
        names = [t.value for t in identifiers]
        self.assertNotIn('BAD', names)
        self.assertIn('WRITELN', names)

    def test_nested_if_outer_true_inner_false(self):
        """Nested $IF: inner false block must not leak tokens."""
        identifiers = [t for t in self._tokens('PROGRAM P; BEGIN '
                                               '{$IF 1 $THEN} '
                                               '  {$IF 0 $THEN} BAD {$ELSE} GOOD {$END} '
                                               '{$END} '
                                               'END.') if t.kind == 'IDENTIFIER']
        names = [t.value for t in identifiers]
        self.assertNotIn('BAD', names)
        self.assertIn('GOOD', names)

    def test_nested_if_outer_false_skips_entire_block(self):
        """When outer $IF is false, inner $IF/$END must not confuse depth tracking."""
        identifiers = [t for t in self._tokens('PROGRAM P; BEGIN '
                                               '{$IF 0 $THEN} '
                                               '  {$IF 1 $THEN} BAD {$END} '
                                               '  ALSO_BAD '
                                               '{$END} '
                                               'END.') if t.kind == 'IDENTIFIER']
        names = [t.value for t in identifiers]
        self.assertNotIn('BAD', names)
        self.assertNotIn('ALSO_BAD', names)

    # ------------------------------------------------------------------
    # $MESSAGE
    # ------------------------------------------------------------------

    def test_message_does_not_raise(self):
        import io
        import sys as _sys
        buf = io.StringIO()
        old_err = _sys.stderr
        _sys.stderr = buf
        try:
            self._flags_after("{$MESSAGE: 'hello from test'}")
        finally:
            _sys.stderr = old_err
        self.assertIn('hello from test', buf.getvalue())

    # ------------------------------------------------------------------
    # $INCONST
    # ------------------------------------------------------------------

    def test_inconst_sets_meta_const_to_zero(self):
        """$INCONST defines an identifier with value 0 (non-interactive build)."""
        from pascal1981.lexer import Lexer
        lex = Lexer('{$INCONST: MYCONST}')
        lex.tokenize()
        self.assertIn('MYCONST', lex._meta_consts)
        self.assertEqual(lex._meta_consts['MYCONST'], 0)

    def test_if_with_inconst_identifier(self):
        """$IF referencing a $INCONST-defined name: 0 means false."""
        from pascal1981.lexer import Lexer
        lex = Lexer('{$INCONST: FLAG} {$IF FLAG $THEN} BAD {$END}')
        toks = lex.tokenize()
        names = [t.value for t in toks if t.kind == 'IDENTIFIER']
        self.assertNotIn('BAD', names)


if __name__ == '__main__':
    unittest.main()


class TestMetacommandSkipRegressions(unittest.TestCase):
    """Regressions for $IF skip-mode bugs found in review."""

    def _identifiers(self, src: str) -> list:
        from pascal1981.lexer import Lexer
        return [t.value for t in Lexer(src).tokenize() if t.kind == 'IDENTIFIER']

    def test_duplicate_else_resumes_at_second_else_like_vintage_d003(self):
        """A second depth-1 $ELSE while skipping a true branch's
        else-body terminates the skip; source after that second $ELSE leaks
        back into tokenization, matching the observed vintage behavior."""
        names = self._identifiers('PROGRAM P; BEGIN {$IF 1 $THEN} GOOD '
                                  '{$ELSE} BAD1 {$ELSE} GOOD2 {$END} END.')
        self.assertIn('GOOD', names)
        self.assertNotIn('BAD1', names)
        self.assertIn('GOOD2', names)

    def test_string_literal_with_brace_in_skipped_block(self):
        """A '{' inside a quoted string in a skipped block must not be
        treated as a comment opener (previously: Unterminated $IF)."""
        names = self._identifiers("PROGRAM P; VAR s: INTEGER; BEGIN "
                                  "{$IF 0 $THEN} x := '{' ; BAD {$END} s := 1 END.")
        self.assertNotIn('BAD', names)
        self.assertIn('s', [n.lower() for n in names if n])

    def test_string_literal_with_paren_star_in_skipped_block(self):
        """'(*' inside a quoted string in a skipped block is also inert."""
        names = self._identifiers("PROGRAM P; BEGIN {$IF 0 $THEN} x := '(*' ; BAD {$END} END.")
        self.assertNotIn('BAD', names)


class TestForceFlagDefaults(unittest.TestCase):
    """effective_flag must use manual defaults, not blanket True."""

    def test_default_for_off_flag_is_false(self):
        from pascal1981.ast_nodes import EmptyStmt
        from pascal1981.codegen import Codegen
        cg = Codegen()
        stmt = EmptyStmt()
        # ENTRY and INITCK default off per the manual.
        self.assertFalse(cg.effective_flag('ENTRY', stmt))
        self.assertFalse(cg.effective_flag('INITCK', stmt))
        # RANGECK defaults on.
        self.assertTrue(cg.effective_flag('RANGECK', stmt))

    def test_meta_flags_on_stmt_take_effect(self):
        from pascal1981.ast_nodes import EmptyStmt
        from pascal1981.codegen import Codegen
        cg = Codegen()
        stmt = EmptyStmt()
        stmt.meta_flags = {'MATHCK': False}
        self.assertFalse(cg.effective_flag('MATHCK', stmt))

    def test_force_flags_override_stmt(self):
        from pascal1981.ast_nodes import EmptyStmt
        from pascal1981.codegen import Codegen
        cg = Codegen(force_flags={'MATHCK': True})
        stmt = EmptyStmt()
        stmt.meta_flags = {'MATHCK': False}
        self.assertTrue(cg.effective_flag('MATHCK', stmt))


class TestWriteDoubleColon(unittest.TestCase):
    """P::N WRITE formatting (manual 12-17)."""

    def test_double_colon_parses(self):
        from tests.support import parse_source
        ast = parse_source('PROGRAM P; VAR x: REAL; BEGIN WRITELN(x::2) END.')
        self.assertIsNotNone(ast)

    def test_double_colon_width_none_precision_set(self):
        from pascal1981.ast_nodes import WriteArg
        from tests.support import parse_source
        ast = parse_source('PROGRAM P; VAR x: REAL; BEGIN WRITELN(x::2) END.')
        stmt = ast.block.body[0]
        arg = stmt.args[0]
        self.assertIsInstance(arg, WriteArg)
        self.assertIsNone(arg.width)
        self.assertIsNotNone(arg.precision)

    def test_full_form_still_parses(self):
        """P:M:N must be unaffected."""
        from pascal1981.ast_nodes import WriteArg
        from tests.support import parse_source
        ast = parse_source('PROGRAM P; VAR x: REAL; BEGIN WRITELN(x:8:3) END.')
        arg = ast.block.body[0].args[0]
        self.assertIsNotNone(arg.width)
        self.assertIsNotNone(arg.precision)


# ---------------------------------------------------------------------------
# Parser acceptance tests for DEVICE INTERFACE / DEVICE IMPLEMENTATION
# (Checklist §1.2.4)
# ---------------------------------------------------------------------------


class TestDeviceUnitParser(unittest.TestCase):
    """Parser acceptance tests for the DEVICE UNIT surface syntax.

    Each test targets a single acceptance/rejection property so failures give
    a precise signal.  The contextual-keyword safety test mirrors the existing
    DEVICE MODULE regression.
    """

    # ------------------------------------------------------------------ #
    #  Positive: device-marked compilation units set is_device            #
    # ------------------------------------------------------------------ #

    def test_device_interface_sets_is_device(self):
        ast = parse_source("DEVICE INTERFACE;\n"
                           "UNIT U (go);\n"
                           "PROCEDURE go;\n"
                           "END;\n")
        from pascal1981.ast_nodes import InterfaceUnit
        self.assertIsInstance(ast, InterfaceUnit)
        self.assertTrue(ast.is_device, "DEVICE INTERFACE must set InterfaceUnit.is_device = True")

    def test_plain_interface_is_device_false(self):
        ast = parse_source("INTERFACE;\n"
                           "UNIT U (go);\n"
                           "PROCEDURE go;\n"
                           "END;\n")
        from pascal1981.ast_nodes import InterfaceUnit
        self.assertIsInstance(ast, InterfaceUnit)
        self.assertFalse(ast.is_device, "plain INTERFACE must leave InterfaceUnit.is_device = False")

    def test_device_implementation_sets_is_device(self):
        ast = parse_source("DEVICE INTERFACE;\n"
                           "UNIT U (go);\n"
                           "PROCEDURE go;\n"
                           "END;\n"
                           "DEVICE IMPLEMENTATION OF U;\n"
                           "PROCEDURE go;\n"
                           "BEGIN END;\n"
                           ".\n")
        from pascal1981.ast_nodes import ImplementationUnit
        self.assertIsInstance(ast, ImplementationUnit)
        self.assertTrue(ast.is_device, "DEVICE IMPLEMENTATION must set ImplementationUnit.is_device = True")

    def test_plain_implementation_is_device_false(self):
        ast = parse_source("INTERFACE;\n"
                           "UNIT U (go);\n"
                           "PROCEDURE go;\n"
                           "END;\n"
                           "IMPLEMENTATION OF U;\n"
                           "PROCEDURE go;\n"
                           "BEGIN END;\n"
                           ".\n")
        from pascal1981.ast_nodes import ImplementationUnit
        self.assertIsInstance(ast, ImplementationUnit)
        self.assertFalse(ast.is_device, "plain IMPLEMENTATION must leave ImplementationUnit.is_device = False")

    def test_device_module_sets_is_device(self):
        """Regression: the existing DEVICE MODULE path is unaffected."""
        ast = parse_source("DEVICE MODULE M;\n"
                           "PROCEDURE go;\n"
                           "BEGIN END;\n"
                           ".\n")
        from pascal1981.ast_nodes import ModuleUnit
        self.assertIsInstance(ast, ModuleUnit)
        self.assertTrue(ast.is_device)

    def test_plain_module_is_device_false(self):
        ast = parse_source("MODULE M;\n"
                           "PROCEDURE go;\n"
                           "BEGIN END;\n"
                           ".\n")
        from pascal1981.ast_nodes import ModuleUnit
        self.assertFalse(ast.is_device)

    # ------------------------------------------------------------------ #
    #  Contextual keyword safety (§0.2 / §1.2.4)                         #
    # ------------------------------------------------------------------ #

    def test_device_as_variable_identifier_in_program(self):
        """'device' must still be usable as an ordinary identifier in
        host/vintage code.  The contextual-keyword guarantee is the same
        one the existing DEVICE MODULE relies on."""
        ast = parse_source("PROGRAM P;\n"
                           "VAR device: INTEGER;\n"
                           "BEGIN device := 7 END.\n")
        self.assertIsNotNone(ast)

    def test_device_as_type_name_identifier(self):
        ast = parse_source("PROGRAM P;\n"
                           "TYPE device = INTEGER;\n"
                           "VAR x: device;\n"
                           "BEGIN x := 1 END.\n")
        self.assertIsNotNone(ast)

    def test_device_as_procedure_name_identifier(self):
        ast = parse_source("PROGRAM P;\n"
                           "PROCEDURE device;\n"
                           "BEGIN END;\n"
                           "BEGIN device END.\n")
        self.assertIsNotNone(ast)

    # ------------------------------------------------------------------ #
    #  Structural checks: AST field values                                #
    # ------------------------------------------------------------------ #

    def test_device_interface_exports_are_parsed(self):
        ast = parse_source("DEVICE INTERFACE;\n"
                           "UNIT KERN (add, mul);\n"
                           "PROCEDURE add (n: INTEGER);\n"
                           "PROCEDURE mul (n: INTEGER);\n"
                           "END;\n")
        from pascal1981.ast_nodes import InterfaceUnit
        self.assertIsInstance(ast, InterfaceUnit)
        self.assertEqual(set(ast.params), {'add', 'mul'})

    def test_device_implementation_module_name_parsed(self):
        ast = parse_source("DEVICE INTERFACE;\n"
                           "UNIT KERN (add, mul);\n"
                           "PROCEDURE add (n: INTEGER);\n"
                           "PROCEDURE mul (n: INTEGER);\n"
                           "END;\n"
                           "DEVICE IMPLEMENTATION OF KERN;\n"
                           "PROCEDURE add (n: INTEGER);\n"
                           "BEGIN END;\n"
                           "PROCEDURE mul (n: INTEGER);\n"
                           "BEGIN END;\n"
                           ".\n")
        from pascal1981.ast_nodes import ImplementationUnit
        self.assertIsInstance(ast, ImplementationUnit)
        self.assertEqual(ast.name.upper(), 'KERN')

    def test_device_interface_has_init_false_when_no_begin(self):
        ast = parse_source("DEVICE INTERFACE;\n"
                           "UNIT U;\n"
                           "END;\n")
        self.assertFalse(ast.has_init)

    def test_device_interface_has_init_true_when_begin_present(self):
        """Parser records has_init=True; the type checker later rejects it."""
        ast = parse_source("DEVICE INTERFACE;\n"
                           "UNIT U;\n"
                           "BEGIN\n"
                           "END;\n")
        self.assertTrue(ast.has_init)

    # ------------------------------------------------------------------ #
    #  Fixture files (§1.2.4 acceptance corpus)                          #
    # ------------------------------------------------------------------ #

    def test_fixture_device_interface_parses(self):
        from pathlib import Path
        src = (Path(__file__).parent / "fixtures" / "parser" / "should_pass" / "32_device_interface.pas").read_text()
        ast = parse_source(src)
        from pascal1981.ast_nodes import InterfaceUnit
        self.assertIsInstance(ast, InterfaceUnit)
        self.assertTrue(ast.is_device)

    def test_fixture_device_implementation_parses(self):
        from pathlib import Path
        src = (Path(__file__).parent / "fixtures" / "parser" / "should_pass" / "33_device_implementation.pas").read_text()
        ast = parse_source(src)
        from pascal1981.ast_nodes import ImplementationUnit
        self.assertIsInstance(ast, ImplementationUnit)
        self.assertTrue(ast.is_device)

    def test_fixture_device_contextual_identifier_parses(self):
        from pathlib import Path
        src = (Path(__file__).parent / "fixtures" / "parser" / "should_pass" / "34_device_contextual_identifier.pas").read_text()
        ast = parse_source(src)
        self.assertIsNotNone(ast)
