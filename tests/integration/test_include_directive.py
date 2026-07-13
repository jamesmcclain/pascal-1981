"""Integration tests for the (*$INCLUDE:'...'*) directive.

These tests lock in the lexer's include-splicing behaviour for the *already-
working* case: a Pascal source file that uses $INCLUDE to splice a fragment of
declarations (VAR, CONST, PROCEDURE) into a compilation unit.

Design constraints
------------------
* Include files are **not** compilation units.  They are never listed in
  ``compile_pairs`` and are never compiled independently.  They exist solely as
  on-disk text that the lexer splices into the token stream of the including
  file at lex time.
* Only the files that are genuine compilation units (PROGRAM, IMPLEMENTATION
  OF …, UNIT) appear in ``compile_pairs``.

Scope
-------------
Only the working case is covered here.  The not-yet-working case (splicing a
full INTERFACE unit block before a PROGRAM or IMPLEMENTATION compilation unit)
is addressed by the spliced-interface tests.
"""

import os
import subprocess
import unittest

from tests.support import (compile_pascal_file, compile_pascal_project, link_pascal_project, requires_exe, temporary_pascal_project)

# ---------------------------------------------------------------------------
# Source fragments used across tests
# ---------------------------------------------------------------------------

# A plain VAR declaration fragment — not a compilation unit, just a block
# declaration that is valid inside a PROGRAM declaration part.
_VAR_INC = "VAR COUNTER: INTEGER;\n"

# A CONST declaration fragment.
_CONST_INC = "CONST LIMIT = 5;\n"

# A PROCEDURE definition fragment — a complete proc body, valid inside the
# declaration part of a PROGRAM.
_PROC_INC = """\
PROCEDURE GREET;
BEGIN
  WRITELN('HELLO FROM INCLUDE')
END;
"""

# A multi-declaration fragment combining CONST and VAR (no subrange type,
# which has separate parser constraints unrelated to include splicing).
_MULTI_INC = """\
CONST BASE = 10;
VAR TOTAL: INTEGER;
"""

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run_exe(exe_path: str, stdin: str = '') -> tuple[int, str, str]:
    run = subprocess.run([exe_path], input=stdin, capture_output=True, text=True)
    return run.returncode, run.stdout, run.stderr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@requires_exe
class TestIncludeDirectiveSplicing(unittest.TestCase):
    """$INCLUDE splices declaration fragments correctly into a PROGRAM."""

    def test_include_splices_var_decl(self):
        """A VAR declaration in an include file is visible in the program body."""
        prog = """\
PROGRAM P (INPUT, OUTPUT);
(*$INCLUDE:'vars.inc'*)
BEGIN
  COUNTER := 42;
  WRITELN(COUNTER)
END.
"""
        with temporary_pascal_project({'vars.inc': _VAR_INC, 'prog.pas': prog}) as d:
            compile_pascal_project(d, [('prog.pas', 'prog.ll')])
            exe = link_pascal_project(d, ['prog.ll'], exe_name='prog')
            rc, out, err = _run_exe(exe)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), '42')

    def test_include_splices_const_decl(self):
        """A CONST declaration in an include file is visible in the program body."""
        prog = """\
PROGRAM P (INPUT, OUTPUT);
(*$INCLUDE:'consts.inc'*)
BEGIN
  WRITELN(LIMIT)
END.
"""
        with temporary_pascal_project({'consts.inc': _CONST_INC, 'prog.pas': prog}) as d:
            compile_pascal_project(d, [('prog.pas', 'prog.ll')])
            exe = link_pascal_project(d, ['prog.ll'], exe_name='prog')
            rc, out, err = _run_exe(exe)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), '5')

    def test_include_splices_procedure_decl(self):
        """A PROCEDURE definition in an include file is callable from the program."""
        prog = """\
PROGRAM P (INPUT, OUTPUT);
(*$INCLUDE:'procs.inc'*)
BEGIN
  GREET
END.
"""
        with temporary_pascal_project({'procs.inc': _PROC_INC, 'prog.pas': prog}) as d:
            compile_pascal_project(d, [('prog.pas', 'prog.ll')])
            exe = link_pascal_project(d, ['prog.ll'], exe_name='prog')
            rc, out, err = _run_exe(exe)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), 'HELLO FROM INCLUDE')

    def test_include_splices_multiple_declaration_kinds(self):
        """An include file mixing CONST and VAR declarations is fully spliced."""
        prog = """\
PROGRAM P (INPUT, OUTPUT);
(*$INCLUDE:'multi.inc'*)
BEGIN
  TOTAL := BASE + 3;
  WRITELN(TOTAL)
END.
"""
        with temporary_pascal_project({'multi.inc': _MULTI_INC, 'prog.pas': prog}) as d:
            compile_pascal_project(d, [('prog.pas', 'prog.ll')])
            exe = link_pascal_project(d, ['prog.ll'], exe_name='prog')
            rc, out, err = _run_exe(exe)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), '13')

    def test_curly_brace_include_syntax(self):
        """The {$INCLUDE:'...'} form is equivalent to (*$INCLUDE:'...'*)."""
        prog = """\
PROGRAM P (INPUT, OUTPUT);
{$INCLUDE:'consts.inc'}
BEGIN
  WRITELN(LIMIT)
END.
"""
        with temporary_pascal_project({'consts.inc': _CONST_INC, 'prog.pas': prog}) as d:
            compile_pascal_project(d, [('prog.pas', 'prog.ll')])
            exe = link_pascal_project(d, ['prog.ll'], exe_name='prog')
            rc, out, err = _run_exe(exe)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), '5')

    def test_include_file_is_not_compiled_as_compilation_unit(self):
        """The include file must NOT appear in compile_pairs; it is not a unit.

        This test makes the constraint explicit: attempting to compile the
        fragment directly (as if it were a compilation unit) must fail.  If this
        test ever starts passing it means the fragment accidentally became a
        valid standalone compilation unit, which would be a specification drift.
        """
        from pascal1981.lexer import LexerError
        from pascal1981.parser import ParserError

        with temporary_pascal_project({'vars.inc': _VAR_INC}) as d:
            inc_path = os.path.join(d, 'vars.inc')
            with self.assertRaises((LexerError, ParserError, RuntimeError)):
                compile_pascal_file(inc_path, os.path.join(d, 'vars.ll'))


class TestIncludeDirectiveNegative(unittest.TestCase):
    """Error-path tests that require no LLVM toolchain."""

    def test_missing_include_file_raises_file_not_found(self):
        """Referencing a nonexistent include file raises FileNotFoundError.

        The error is raised by the OS open() call inside lex_file() when it
        tries to open the resolved (but absent) include path.  It is *not*
        wrapped in a LexerError — it propagates as-is.
        """
        prog = """\
PROGRAM P (INPUT, OUTPUT);
(*$INCLUDE:'no_such_file.inc'*)
BEGIN
END.
"""
        with temporary_pascal_project({'prog.pas': prog}) as d:
            from pascal1981.lexer import lex_file
            prog_path = os.path.join(d, 'prog.pas')
            with self.assertRaises(FileNotFoundError) as ctx:
                lex_file(prog_path)
            self.assertIn('no_such_file.inc', str(ctx.exception))

    def test_recursive_include_raises_lexer_error(self):
        """A file that includes itself must be detected and rejected."""
        from pascal1981.lexer import LexerError, lex_file

        # self.inc includes itself
        self_inc = "(*$INCLUDE:'self.inc'*)\n"
        with temporary_pascal_project({'self.inc': self_inc}) as d:
            with self.assertRaises(LexerError) as ctx:
                lex_file(os.path.join(d, 'self.inc'))
            self.assertIn('ecursive', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
