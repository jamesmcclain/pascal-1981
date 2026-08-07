"""Tests for '{ ... }' comment lexing, and the diagnostic for a stray '}'.

'{ ... }' comments are faithful vintage Pascal: they do not nest and have no
escape mechanism, so any '}' inside the comment text -- including ordinary
prose like "p_{v+1}" -- ends the comment right there. That's correct,
long-standing Pascal behavior, not a bug. But the resulting error used to
surface as a generic "Unexpected character '}'" pointing at the *next* '}'
in the source (the one the author intended to close the comment), which
gives no hint about the real, distant cause. This file pins:

  * apostrophes inside a '{ ... }' comment are fine on their own (they are
    just comment text; nothing in the plain comment skip path treats them
    as starting a string literal),
  * a '}' embedded in comment text ends the comment early, and the
    resulting stray '}' now raises a LexerError whose message explains
    *why* ('{ }' comments do not nest, suggests '(* ... *)' instead),
  * '(* ... *)' comments are unaffected: '}' (and apostrophes) inside them
    are inert, so they are the correct choice for comment text containing
    '}'.
"""

import unittest

from pascal1981.lexer import Lexer, LexerError


def _tokenize(src: str):
    return Lexer(src).tokenize()


class TestBraceCommentApostrophe(unittest.TestCase):

    def test_apostrophe_alone_in_brace_comment_is_fine(self):
        src = "PROGRAM P;\n{ don't do this }\nBEGIN WRITELN(1) END."
        tokens = _tokenize(src)
        self.assertTrue(any(t.kind == 'PROGRAM' for t in tokens))
        self.assertTrue(any(t.kind == 'BEGIN' for t in tokens))


class TestStrayBraceDiagnostic(unittest.TestCase):

    def test_embedded_brace_closes_comment_early_and_orphans_the_real_closer(self):
        # "p_{v+1}" inside the comment closes it right after "p_{v+1}"'s own
        # '}', so the comment's *intended* closing '}' is left as a stray,
        # unexpected token.
        src = "PROGRAM P;\n{ p_{v+1} bracket }\nBEGIN WRITELN(1) END."
        with self.assertRaises(LexerError) as ctx:
            _tokenize(src)
        message = str(ctx.exception)
        self.assertIn("'}'", message)

    def test_stray_brace_error_explains_non_nesting_comments(self):
        src = "PROGRAM P;\n{ p_{v+1} bracket }\nBEGIN WRITELN(1) END."
        with self.assertRaises(LexerError) as ctx:
            _tokenize(src)
        message = str(ctx.exception)
        self.assertIn('do not nest', message)
        self.assertIn('(* ... *)', message)

    def test_bare_stray_brace_gets_the_same_hint(self):
        # No comment involved at all -- still a helpful hint, since a
        # standalone '}' is essentially always this footgun in practice.
        src = "PROGRAM P;\nBEGIN WRITELN(1) } END."
        with self.assertRaises(LexerError) as ctx:
            _tokenize(src)
        self.assertIn('do not nest', str(ctx.exception))


class TestParenStarCommentIsUnaffected(unittest.TestCase):

    def test_brace_and_apostrophe_are_inert_inside_paren_star_comment(self):
        src = "PROGRAM P;\n(* p_{v+1} bracket and don't worry about it *)\nBEGIN WRITELN(1) END."
        tokens = _tokenize(src)
        self.assertTrue(any(t.kind == 'PROGRAM' for t in tokens))
        self.assertTrue(any(t.kind == 'BEGIN' for t in tokens))


if __name__ == '__main__':
    unittest.main()
