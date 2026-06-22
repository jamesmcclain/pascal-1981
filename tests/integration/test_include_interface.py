"""Integration tests for $INCLUDE-spliced INTERFACE units.

These tests verify that a PROGRAM (or IMPLEMENTATION) can use a UNIT whose
interface is delivered exclusively via a $INCLUDE header file rather than
through a separate on-disk interface file.

The vintage IBM Pascal pattern being tested
-------------------------------------------

A single shared header file (e.g. ``GRAPHI``) contains the full INTERFACE
declaration for a unit.  Both the PROGRAM that consumes the unit *and* the
IMPLEMENTATION that provides it include that same header file:

    (* GRAPHI — the shared header *)
    INTERFACE;
    UNIT GRAPHICS (BJUMP, WJUMP);
    PROCEDURE BJUMP (X, Y: INTEGER);
    PROCEDURE WJUMP (X, Y: INTEGER);
    END;

    (* graphics_impl.pas — the implementation *)
    (*$INCLUDE:'GRAPHI'*)
    IMPLEMENTATION OF GRAPHICS;
    ...

    (* plotbox.pas — the consuming program *)
    (*$INCLUDE:'GRAPHI'*)
    PROGRAM PLOTBOX (INPUT, OUTPUT);
    USES GRAPHICS (MOVE, PLOT);
    ...

Crucially, there is **no separate ``GRAPHICS`` file on disk**.  The program
receives the interface declaration only through the spliced text.  Before this fix, the type checker ignored spliced interfaces when resolving USES and
always went to disk; this caused ``Module 'GRAPHICS' not found`` errors.

What is NOT covered here (multiple interfaces)
------------------------------------
The case where an IMPLEMENTATION splices *two* header files — its own
interface plus an additional USES-dependency interface — is handled in the multi-interface tests
and tested in ``test_include_multi_interface.py``.

File layout rules
-------------------------------------
* Include files are **never** listed in ``compile_pairs``.
* Only genuine compilation units (PROGRAM, IMPLEMENTATION OF …) are compiled.
"""

import subprocess
import unittest

from tests.support import (compile_pascal_project, link_pascal_project, requires_exe, temporary_pascal_project)

# ---------------------------------------------------------------------------
# Shared header (the include file — never compiled independently)
# ---------------------------------------------------------------------------

_GRAPHICS_HEADER = """\
INTERFACE;
UNIT GRAPHICS (BJUMP, WJUMP);
PROCEDURE BJUMP (X, Y: INTEGER);
PROCEDURE WJUMP (X, Y: INTEGER);
END;
"""

# ---------------------------------------------------------------------------
# Implementation file
# The implementation includes the header so the parser can attach the
# spliced InterfaceUnit to impl.interface (single-leading-interface path
# that already worked before this fix).
# Named graphics_impl.pas — deliberately *different* from the unit name —
# so there is no ``GRAPHICS`` or ``GRAPHICS.pas`` file on disk for the
# type checker to fall back to.
# ---------------------------------------------------------------------------

_GRAPHICS_IMPL = """\
(*$INCLUDE:'GRAPHI'*)
IMPLEMENTATION OF GRAPHICS;
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
# Program files (two variants, mirroring test_uses_graphics.py)
# Both include the header and then USES GRAPHICS.  No on-disk interface file
# for GRAPHICS exists in the project directory.
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


@requires_exe
class TestIncludeInterfaceResolution(unittest.TestCase):
    """USES resolves against a $INCLUDE-spliced interface, no disk fallback."""

    def _project_files(self, main_source: str) -> dict:
        return {
            # The header — an include file, not a compilation unit.
            'GRAPHI': _GRAPHICS_HEADER,
            # The implementation — includes the header, compiled as a unit.
            'graphics_impl.pas': _GRAPHICS_IMPL,
            # The program — includes the header, compiled as a unit.
            'plotbox.pas': main_source,
            # Deliberately absent: no 'GRAPHICS', 'GRAPHICS.pas', or
            # 'graphics.pas' on disk.  Resolution must come from local_interfaces.
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

    # ------------------------------------------------------------------
    # Renamed USES (USES GRAPHICS (MOVE, PLOT))
    # ------------------------------------------------------------------

    def test_renamed_uses_resolves_from_spliced_interface(self):
        """USES GRAPHICS (MOVE, PLOT) resolves GRAPHICS from the spliced header."""
        plotbox_ir, stdout = self._build_and_run(_RENAMED_PROGRAM, 'plotbox-renamed')
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        self.assertEqual(lines, _EXPECTED_RENAMED)

    def test_renamed_uses_ir_calls_real_export_names(self):
        """IR calls BJUMP/WJUMP (the real export names), not the local aliases."""
        plotbox_ir, _ = self._build_and_run(_RENAMED_PROGRAM, 'plotbox-renamed-ir')
        self.assertIn('@"BJUMP"', plotbox_ir)
        self.assertIn('@"WJUMP"', plotbox_ir)
        # Local alias names must not appear as call targets in the IR.
        self.assertNotIn('@"MOVE"', plotbox_ir)
        self.assertNotIn('@"PLOT"', plotbox_ir)

    # ------------------------------------------------------------------
    # Plain USES (USES GRAPHICS — imports under original export names)
    # ------------------------------------------------------------------

    def test_plain_uses_resolves_from_spliced_interface(self):
        """USES GRAPHICS (plain) resolves GRAPHICS from the spliced header."""
        plotbox_ir, stdout = self._build_and_run(_PLAIN_PROGRAM, 'plotbox-plain')
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        self.assertEqual(lines, _EXPECTED_PLAIN)

    def test_plain_uses_ir_contains_export_names(self):
        """IR contains BJUMP and WJUMP call targets for plain USES."""
        plotbox_ir, _ = self._build_and_run(_PLAIN_PROGRAM, 'plotbox-plain-ir')
        self.assertIn('@"BJUMP"', plotbox_ir)
        self.assertIn('@"WJUMP"', plotbox_ir)

    # ------------------------------------------------------------------
    # Negative: disk fallback is still used when no spliced interface matches
    # ------------------------------------------------------------------

    def test_uses_of_unknown_module_still_errors_when_absent_from_disk(self):
        """If neither local_interfaces nor disk has the module, an error is raised."""
        bad_prog = """\
(*$INCLUDE:'GRAPHI'*)
PROGRAM P (INPUT, OUTPUT);
USES NOSUCHMODULE;
BEGIN
END.
"""
        import os
        import tempfile

        from pascal1981.parser import parse_file
        from pascal1981.type_checker import PascalTypeChecker

        with temporary_pascal_project({'GRAPHI': _GRAPHICS_HEADER, 'p.pas': bad_prog}) as d:
            path = os.path.join(d, 'p.pas')
            ast = parse_file(path)
            checker = PascalTypeChecker(source_file=path)
            result = checker.check(ast)
        self.assertFalse(result.success)
        combined = ' '.join(e.message for e in result.errors)
        self.assertIn('NOSUCHMODULE', combined)


if __name__ == '__main__':
    unittest.main()
