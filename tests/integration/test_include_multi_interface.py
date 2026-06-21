"""Integration tests for multiple $INCLUDE-spliced INTERFACE units.

These tests verify the case where a single source file splices in *more than
one* INTERFACE header before its main compilation unit.  The canonical example
from the IBM Pascal manual is an IMPLEMENTATION file that includes both its
own interface header AND the interface header of a module it depends on:

    (*$INCLUDE:'GRAPHI'*)   -- UNIT GRAPHICS interface (own)
    (*$INCLUDE:'BASEPL'*)   -- UNIT BASEPLOT interface (dependency)
    IMPLEMENTATION OF GRAPHICS;
    USES BASEPLOT;
    ...

The parser originally accepted exactly one leading interface unit.  When
a second INTERFACE keyword appeared after the first was parsed, the parser
called parse_compilation_unit(), which consumed it as if it were a standalone
unit and then choked on the following IMPLEMENTATION keyword with:

    ParserError: expected EOF, got IMPLEMENTATION

Multi-interface changes
---------------
* ``parser.parse()`` now loops over leading INTERFACE units instead of
  handling at most one.
* ``unit.interface`` is assigned by *name match* (the interface whose name
  equals the implementation unit name), not blindly to the first header.
* All spliced interfaces are placed in ``unit.local_interfaces`` regardless,
  so the USES-from-local-interfaces resolution applies to all of them.

What is NOT covered here
-------------------------
Codegen for an IMPLEMENTATION that *calls* a procedure from a USES-imported
module (i.e. the implementation body itself references DRAWLINE from BASEPLOT)
is a separate issue: ``codegen_implementation`` does not process USES.  The
tests below keep implementation bodies self-contained (WRITELN only) so that
the codegen path remains unchanged while the parser, type-checker, and
full build are exercised.

File layout rules
----------------------------------------------
* Include files are **never** listed in ``compile_pairs``.
* Only genuine compilation units are compiled.
"""

import os
import subprocess
import unittest

from tests.support import (
    compile_pascal_project,
    link_pascal_project,
    requires_exe,
    temporary_pascal_project,
)

# ---------------------------------------------------------------------------
# Shared header files (include files — never compiled independently)
# ---------------------------------------------------------------------------

_GRAPHICS_HEADER = """\
INTERFACE;
UNIT GRAPHICS (BJUMP, WJUMP);
PROCEDURE BJUMP (X, Y: INTEGER);
PROCEDURE WJUMP (X, Y: INTEGER);
END;
"""

_BASEPLOT_HEADER = """\
INTERFACE;
UNIT BASEPLOT (DRAWLINE);
PROCEDURE DRAWLINE (X, Y: INTEGER);
END;
"""

# ---------------------------------------------------------------------------
# Implementation: splices TWO headers before IMPLEMENTATION OF GRAPHICS.
# USES BASEPLOT is present (exercises type-checker local_interfaces lookup).
# The body calls WRITELN directly — it does NOT call DRAWLINE — so codegen
# requires no special handling for implementation USES.
# ---------------------------------------------------------------------------

_GRAPHICS_IMPL = """\
(*$INCLUDE:'GRAPHI'*)
(*$INCLUDE:'BASEPL'*)
IMPLEMENTATION OF GRAPHICS;
USES BASEPLOT;
PROCEDURE BJUMP (X, Y: INTEGER);
BEGIN
  WRITELN('BJUMP ', X, ' ', Y)
END;
PROCEDURE WJUMP (X, Y: INTEGER);
BEGIN
  WRITELN('WJUMP ', X, ' ', Y)
END;
BEGIN
END.
"""

# ---------------------------------------------------------------------------
# Program: splices only the GRAPHICS header (single-interface path).
# ---------------------------------------------------------------------------

_RENAMED_PROGRAM = """\
(*$INCLUDE:'GRAPHI'*)
PROGRAM PLOTBOX (INPUT, OUTPUT);
USES GRAPHICS (MOVE, PLOT);
BEGIN
  MOVE (0, 0);
  PLOT (10, 0); PLOT (10, 10);
  PLOT (0, 10); PLOT (0, 0)
END.
"""

_PLAIN_PROGRAM = """\
(*$INCLUDE:'GRAPHI'*)
PROGRAM PLOTBOX (INPUT, OUTPUT);
USES GRAPHICS;
BEGIN
  BJUMP (0, 0);
  WJUMP (10, 0); BJUMP (10, 10);
  WJUMP (0, 10); BJUMP (0, 0)
END.
"""

_EXPECTED_RENAMED = [
    "BJUMP 0 0",
    "WJUMP 10 0",
    "WJUMP 10 10",
    "WJUMP 0 10",
    "WJUMP 0 0",
]

_EXPECTED_PLAIN = [
    "BJUMP 0 0",
    "WJUMP 10 0",
    "BJUMP 10 10",
    "WJUMP 0 10",
    "BJUMP 0 0",
]


# ---------------------------------------------------------------------------
# AST-level tests (no LLVM / clang required)
# ---------------------------------------------------------------------------

class TestMultiInterfaceParser(unittest.TestCase):
    """Parser correctly accumulates multiple leading interfaces."""

    def _parse_impl(self, source: str, headers: dict):
        """Parse a source string in a temp project directory with given headers."""
        from pascal1981.lexer import lex_file
        from pascal1981.parser import Parser
        import tempfile, shutil

        tmpdir = tempfile.mkdtemp()
        try:
            for name, content in headers.items():
                with open(os.path.join(tmpdir, name), 'w') as f:
                    f.write(content)
            src_path = os.path.join(tmpdir, 'impl.pas')
            with open(src_path, 'w') as f:
                f.write(source)
            tokens = lex_file(src_path)
            return Parser(tokens).parse()
        finally:
            shutil.rmtree(tmpdir)

    def test_two_leading_interfaces_parse_without_error(self):
        """A file with two spliced interfaces before IMPLEMENTATION parses cleanly."""
        from pascal1981.ast_nodes import ImplementationUnit
        unit = self._parse_impl(_GRAPHICS_IMPL,
                                {'GRAPHI': _GRAPHICS_HEADER, 'BASEPL': _BASEPLOT_HEADER})
        self.assertIsInstance(unit, ImplementationUnit)

    def test_unit_interface_assigned_by_name_match(self):
        """unit.interface is the GRAPHICS interface, not the BASEPLOT interface."""
        unit = self._parse_impl(_GRAPHICS_IMPL,
                                {'GRAPHI': _GRAPHICS_HEADER, 'BASEPL': _BASEPLOT_HEADER})
        self.assertIsNotNone(unit.interface)
        self.assertEqual(unit.interface.name.upper(), 'GRAPHICS')

    def test_both_interfaces_in_local_interfaces(self):
        """Both GRAPHICS and BASEPLOT appear in unit.local_interfaces."""
        unit = self._parse_impl(_GRAPHICS_IMPL,
                                {'GRAPHI': _GRAPHICS_HEADER, 'BASEPL': _BASEPLOT_HEADER})
        names = {i.name.upper() for i in unit.local_interfaces}
        self.assertIn('GRAPHICS', names)
        self.assertIn('BASEPLOT', names)

    def test_local_interfaces_length(self):
        """Exactly two interfaces are collected."""
        unit = self._parse_impl(_GRAPHICS_IMPL,
                                {'GRAPHI': _GRAPHICS_HEADER, 'BASEPL': _BASEPLOT_HEADER})
        self.assertEqual(len(unit.local_interfaces), 2)

    def test_single_interface_standalone_file_still_works(self):
        """A file containing only an INTERFACE unit returns that interface (regression)."""
        from pascal1981.ast_nodes import InterfaceUnit
        import tempfile, shutil
        from pascal1981.lexer import lex_file
        from pascal1981.parser import Parser

        tmpdir = tempfile.mkdtemp()
        try:
            src_path = os.path.join(tmpdir, 'iface.pas')
            with open(src_path, 'w') as f:
                f.write(_GRAPHICS_HEADER)
            tokens = lex_file(src_path)
            unit = Parser(tokens).parse()
        finally:
            shutil.rmtree(tmpdir)
        self.assertIsInstance(unit, InterfaceUnit)
        self.assertEqual(unit.name.upper(), 'GRAPHICS')


# ---------------------------------------------------------------------------
# Type-checker level (no LLVM / clang required)
# ---------------------------------------------------------------------------

class TestMultiInterfaceTypeCheck(unittest.TestCase):
    """Type checker resolves USES against the second spliced interface."""

    def _typecheck_impl(self):
        from pascal1981.lexer import lex_file
        from pascal1981.parser import Parser
        from pascal1981.type_checker import PascalTypeChecker
        import tempfile, shutil

        tmpdir = tempfile.mkdtemp()
        try:
            for name, content in [('GRAPHI', _GRAPHICS_HEADER),
                                   ('BASEPL', _BASEPLOT_HEADER),
                                   ('impl.pas', _GRAPHICS_IMPL)]:
                with open(os.path.join(tmpdir, name), 'w') as f:
                    f.write(content)
            src_path = os.path.join(tmpdir, 'impl.pas')
            tokens = lex_file(src_path)
            ast = Parser(tokens).parse()
            checker = PascalTypeChecker(source_file=src_path)
            return checker.check(ast)
        finally:
            shutil.rmtree(tmpdir)

    def test_implementation_uses_baseplot_from_spliced_interface(self):
        """USES BASEPLOT in the implementation resolves from local_interfaces, not disk."""
        result = self._typecheck_impl()
        self.assertTrue(result.success,
                        msg=f"Type check failed: {[e.message for e in result.errors]}")

    def test_no_disk_baseplot_file_required(self):
        """Type check passes even though no BASEPLOT or BASEPL file exists on disk."""
        # This is already guaranteed by test_implementation_uses_baseplot_from_spliced_interface
        # since the temp directory never has a BASEPLOT file — only the include
        # files GRAPHI and BASEPL.  This explicit test documents the intent.
        from pascal1981.lexer import lex_file
        from pascal1981.parser import Parser
        from pascal1981.type_checker import PascalTypeChecker
        import tempfile, shutil

        tmpdir = tempfile.mkdtemp()
        try:
            # Write ONLY the include files + impl source.  Deliberately no
            # 'BASEPLOT', 'baseplot', 'BASEPLOT.pas', etc.
            for name, content in [('GRAPHI', _GRAPHICS_HEADER),
                                   ('BASEPL', _BASEPLOT_HEADER),
                                   ('impl.pas', _GRAPHICS_IMPL)]:
                with open(os.path.join(tmpdir, name), 'w') as f:
                    f.write(content)
            disk_files = os.listdir(tmpdir)
            self.assertNotIn('BASEPLOT', disk_files)
            self.assertNotIn('BASEPLOT.pas', disk_files)

            src_path = os.path.join(tmpdir, 'impl.pas')
            tokens = lex_file(src_path)
            ast = Parser(tokens).parse()
            checker = PascalTypeChecker(source_file=src_path)
            result = checker.check(ast)
        finally:
            shutil.rmtree(tmpdir)

        self.assertTrue(result.success,
                        msg=f"Expected success without disk BASEPLOT: {[e.message for e in result.errors]}")


# ---------------------------------------------------------------------------
# Full build-and-run tests (require llvmlite + clang)
# ---------------------------------------------------------------------------

@requires_exe
class TestMultiInterfaceBuildAndRun(unittest.TestCase):
    """End-to-end: two-header implementation compiles, links, and runs correctly."""

    def _project_files(self, main_source: str) -> dict:
        return {
            # Include files — never in compile_pairs.
            'GRAPHI': _GRAPHICS_HEADER,
            'BASEPL': _BASEPLOT_HEADER,
            # Implementation splices TWO headers.
            'graphics_impl.pas': _GRAPHICS_IMPL,
            # Program splices only the GRAPHICS header (single-interface path).
            'plotbox.pas': main_source,
            # Deliberately absent from disk: no GRAPHICS, GRAPHICS.pas,
            # BASEPLOT, or BASEPLOT.pas files.
        }

    def _build_and_run(self, main_source: str, exe_name: str):
        with temporary_pascal_project(self._project_files(main_source)) as d:
            outputs = compile_pascal_project(
                d,
                [
                    ('plotbox.pas', 'plotbox.ll'),
                    ('graphics_impl.pas', 'graphics_impl.ll'),
                ],
            )
            plotbox_ir = open(outputs['plotbox.pas']).read()
            exe = link_pascal_project(
                d,
                ['plotbox.ll', 'graphics_impl.ll'],
                exe_name=exe_name,
            )
            run = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            return plotbox_ir, run.stdout

    def test_renamed_uses_builds_and_runs(self):
        """Two-header implementation + renamed USES program: correct output."""
        _, stdout = self._build_and_run(_RENAMED_PROGRAM, 'plotbox-renamed')
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        self.assertEqual(lines, _EXPECTED_RENAMED)

    def test_plain_uses_builds_and_runs(self):
        """Two-header implementation + plain USES program: correct output."""
        _, stdout = self._build_and_run(_PLAIN_PROGRAM, 'plotbox-plain')
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        self.assertEqual(lines, _EXPECTED_PLAIN)

    def test_renamed_uses_ir_calls_real_export_names(self):
        """IR for the renamed-USES program calls BJUMP/WJUMP, not MOVE/PLOT."""
        plotbox_ir, _ = self._build_and_run(_RENAMED_PROGRAM, 'plotbox-renamed-ir')
        self.assertIn('@"BJUMP"', plotbox_ir)
        self.assertIn('@"WJUMP"', plotbox_ir)
        self.assertNotIn('@"MOVE"', plotbox_ir)
        self.assertNotIn('@"PLOT"', plotbox_ir)


if __name__ == '__main__':
    unittest.main()
