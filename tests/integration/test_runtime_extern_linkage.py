"""Integration coverage for lazy runtime externs across separately linked objects."""

import os
import re
import subprocess
import unittest

from tests.support import (
    build_and_run_pascal_project,
    compile_pascal_project,
    link_pascal_project,
    requires_exe,
    temporary_pascal_project,
)


def _strong_globals(ir_text):
    return re.findall(r'^@"([^"]+)"\s*=\s*global\b', ir_text, re.MULTILINE)


def _extern_globals(ir_text):
    return re.findall(r'^@"([^"]+)"\s*=\s*external global\b', ir_text, re.MULTILINE)


_MODULE_READS_INPUT = """MODULE Helper;
VAR x: INTEGER;
PROCEDURE ping;
BEGIN
  READ(x)
END;
.
"""

_PROGRAM_WRITES_OUTPUT = """PROGRAM Main;
BEGIN
  WRITELN('OK')
END.
"""

_PROGRAM_READS_AND_WRITES_INTEGER = """PROGRAM Main;
VAR y: INTEGER;
BEGIN
  READ(y);
  WRITELN(y)
END.
"""


@requires_exe
class TestRuntimeExternLinkageIntegration(unittest.TestCase):
    def test_program_and_separate_module_link_with_single_file_owner(self):
        """A separately compiled MODULE may touch INPUT without owning @input."""
        rc, out, err = build_and_run_pascal_project(
            files={
                'helper.pas': _MODULE_READS_INPUT,
                'main.pas': _PROGRAM_WRITES_OUTPUT,
            },
            compile_pairs=[
                ('helper.pas', 'helper.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['helper.ll', 'main.ll'],
            exe_name='program-module-file-owner',
            stdin='123\n',
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out, 'OK\n')
        self.assertEqual(err, '')

    def test_shared_runtime_extern_declarations_link_cleanly(self):
        """Two objects may both declare the same lazy runtime extern."""
        rc, out, err = build_and_run_pascal_project(
            files={
                'helper.pas': _MODULE_READS_INPUT,
                'main.pas': _PROGRAM_READS_AND_WRITES_INTEGER,
            },
            compile_pairs=[
                ('helper.pas', 'helper.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['helper.ll', 'main.ll'],
            exe_name='shared-runtime-extern',
            stdin='42\n',
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), '42')
        self.assertEqual(err, '')

    def test_program_module_ir_has_one_strong_input_output_owner(self):
        """Project-level artifact check for PROGRAM-owned file globals."""
        with temporary_pascal_project({
            'helper.pas': _MODULE_READS_INPUT,
            'main.pas': _PROGRAM_WRITES_OUTPUT,
        }) as project_dir:
            compile_pascal_project(
                project_dir,
                [
                    ('helper.pas', 'helper.ll'),
                    ('main.pas', 'main.ll'),
                ],
            )
            with open(os.path.join(project_dir, 'helper.ll')) as f:
                helper_ir = f.read()
            with open(os.path.join(project_dir, 'main.ll')) as f:
                main_ir = f.read()

            helper_strong = _strong_globals(helper_ir)
            helper_extern = _extern_globals(helper_ir)
            main_strong = _strong_globals(main_ir)
            combined_strong = helper_strong + main_strong

            self.assertEqual(combined_strong.count('input'), 1)
            self.assertEqual(combined_strong.count('output'), 1)
            self.assertNotIn('input', helper_strong)
            self.assertNotIn('output', helper_strong)
            self.assertIn('input', helper_extern)
            self.assertIn('output', helper_extern)
            self.assertIn('input', main_strong)
            self.assertIn('output', main_strong)

            # Sanity-check the same artifacts still link; no allow-multiple-def
            # escape hatch should be needed.
            exe = link_pascal_project(
                project_dir,
                ['helper.ll', 'main.ll'],
                exe_name='artifact-owner-check',
                link_flags=[],
            )
            run = subprocess.run([exe], input='7\n', capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout, 'OK\n')


if __name__ == '__main__':
    unittest.main()
