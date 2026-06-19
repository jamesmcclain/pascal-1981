"""Multi-file integration tests for the GRAPHICS / PLOTBOX USES examples."""

import os
import subprocess
import unittest

from tests.support import (
    compile_pascal_project,
    link_pascal_project,
    requires_exe,
    temporary_pascal_project,
)

_INTERFACE = """INTERFACE;
UNIT GRAPHICS (BJUMP, WJUMP);
PROCEDURE BJUMP (X, Y: INTEGER);
PROCEDURE WJUMP (X, Y: INTEGER);
END;
"""

_IMPLEMENTATION = """IMPLEMENTATION OF GRAPHICS;
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

_RENAMED_PROGRAM = """PROGRAM PLOTBOX (INPUT, OUTPUT);
USES GRAPHICS (MOVE, PLOT);
BEGIN
  MOVE (0, 0);
  PLOT (10, 0); PLOT (10, 10);
  PLOT (0, 10); PLOT (0, 0)
END.
"""

_PLAIN_PROGRAM = """PROGRAM PLOTBOX (INPUT, OUTPUT);
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
class TestUsesGraphicsIntegration(unittest.TestCase):
    def _run_case(self, main_source: str, exe_name: str):
        with temporary_pascal_project(
            {
                'graphics.pas': _INTERFACE,
                'graphics_impl.pas': _IMPLEMENTATION,
                'plotbox.pas': main_source,
            }
        ) as project_dir:
            outputs = compile_pascal_project(
                project_dir,
                [
                    ('plotbox.pas', 'plotbox.ll'),
                    ('graphics_impl.pas', 'graphics_impl.ll'),
                ],
            )
            with open(outputs['plotbox.pas'], 'r') as f:
                plotbox_ir = f.read()
            exe_path = link_pascal_project(
                project_dir,
                ['plotbox.ll', 'graphics_impl.ll'],
                exe_name=exe_name,
                link_flags=['-Wl,--allow-multiple-definition'],
            )
            run = subprocess.run([exe_path], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            return plotbox_ir, run.stdout

    def test_plain_uses_graphics_builds_and_runs(self):
        plotbox_ir, stdout = self._run_case(_PLAIN_PROGRAM, 'plotbox-plain')
        self.assertIn('@"BJUMP"', plotbox_ir)
        self.assertIn('@"WJUMP"', plotbox_ir)
        self.assertEqual(
            [line.strip() for line in stdout.splitlines() if line.strip()],
            _EXPECTED_PLAIN,
        )

    def test_renamed_uses_graphics_builds_and_runs_and_binds_real_exports(self):
        plotbox_ir, stdout = self._run_case(_RENAMED_PROGRAM, 'plotbox-renamed')
        self.assertEqual(
            [line.strip() for line in stdout.splitlines() if line.strip()],
            _EXPECTED_RENAMED,
        )
        self.assertIn('@"BJUMP"', plotbox_ir)
        self.assertIn('@"WJUMP"', plotbox_ir)
        self.assertNotIn('@"MOVE"', plotbox_ir)
        self.assertNotIn('@"PLOT"', plotbox_ir)


if __name__ == '__main__':
    unittest.main()
