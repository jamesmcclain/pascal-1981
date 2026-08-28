"""The two front ends must accept the same language.

There are two implementations of the Pascal-1981 front end: the Python
reference (``lexer.py``/``parser.py``) and the self-hosting native compiler
(``pascal_src/lexer.pas``/``parser.pas``), which consumes and produces the
same JSON token and AST streams.  Nothing forces an edit to one to be made in
the other, so a change to the reserved-word set can leave the ports silently
accepting different languages -- the native stage keeps rejecting sources the
reference now accepts, and emits a keyword token where the reference emits
IDENTIFIER for the same text.  Demoting LSTRING to a predeclared identifier
did exactly that.

These checks read the sources rather than building the native stages, so they
cost nothing and run everywhere.
"""

import re
import unittest
from pathlib import Path

from pascal1981.lexer import KEYWORD_CODES

PASCAL_SRC = Path(__file__).resolve().parent.parent / 'src' / 'pascal1981' / 'pascal_src'


def native_keyword_codes() -> dict:
    """The keyword table GetKeywordCode in lexer.pas spells out as an IF chain."""
    text = (PASCAL_SRC / 'lexer.pas').read_text()
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"kw = '([A-Z0-9_]+)' THEN GetKeywordCode := (\d+)", text)}


class TestKeywordTableParity(unittest.TestCase):

    def test_native_keyword_table_matches_the_reference(self):
        native = native_keyword_codes()
        self.assertTrue(native, 'failed to scrape GetKeywordCode from lexer.pas')
        self.assertEqual(set(native), set(KEYWORD_CODES), 'reserved words differ between lexer.py and lexer.pas')
        self.assertEqual(native, {k: v for k, v in KEYWORD_CODES.items()}, 'a shared reserved word carries different token codes')

    def test_lstring_is_not_reserved_in_either_front_end(self):
        # Predeclared identifier, not a reserved word (IBM Pascal, Aug 1981,
        # p.3-7), so it must lex as IDENTIFIER on both sides -- which is what
        # lets a user TYPE of that name shadow it.
        self.assertNotIn('LSTRING', KEYWORD_CODES)
        self.assertNotIn('LSTRING', native_keyword_codes())


class TestTypeGrammarParity(unittest.TestCase):

    def test_neither_parser_builds_an_LStringType_node(self):
        # LSTRING(n) is now a NamedType carrying a param on both sides.  A
        # leftover branch in either parser would emit an AST node the other
        # never produces, which the shared JSON AST format cannot reconcile.
        self.assertNotIn('LStringType', (PASCAL_SRC / 'parser.pas').read_text())
        parser_py = (Path(__file__).resolve().parent.parent / 'src' / 'pascal1981' / 'parser.py').read_text()
        self.assertNotIn('LStringType(', parser_py)


if __name__ == '__main__':
    unittest.main()
