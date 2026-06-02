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
        self.assertEqual(len(self.files), 14, f"Expected 14 should_pass fixtures, found {len(self.files)}")

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
        self.assertEqual(len(self.files), 14, f"Expected 14 should_fail fixtures, found {len(self.files)}")

    def test_parser_rejects_all_should_fail(self):
        """Each should_fail/ file must raise LexerError or ParserError (not any other exception)."""
        for fixture in self.files:
            src = fixture.read_text()
            with self.subTest(file=fixture.name):
                with self.assertRaises((LexerError, ParserError),
                                       msg=f"{fixture.name} should fail but was accepted"):
                    parse_source(src)


class TestParserJudgmentCalls(unittest.TestCase):
    """Test cases whose verdict depends on dialect decisions (skipped, not passing/failing)."""

    def test_judgment_calls_skipped(self):
        """Judgment call fixtures are informational; skip them pending dialect resolution."""
        fixtures_dir = Path(__file__).parent / "fixtures" / "parser" / "judgment_calls"
        files = sorted(fixtures_dir.glob("*.pas"))
        self.assertEqual(len(files), 2, f"Expected 2 judgment_calls fixtures, found {len(files)}")

        for fixture in files:
            with self.subTest(file=fixture.name):
                self.skipTest("dialect decision pending")


if __name__ == '__main__':
    unittest.main()
