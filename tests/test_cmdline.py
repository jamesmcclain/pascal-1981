"""Command-line binding of program-heading parameters (vintage model).

The faithful 1981 dialect populates each program-heading parameter other than
INPUT/OUTPUT from the command line at startup, prompting at the keyboard when an
argument is absent (IBM Pascal manual 13-5..13-7). See runtime/cmdline.c and
docs/command-line-support.md.

IR-level tests need no toolchain; the build-and-run cases are decorated with
@requires_exe and auto-skip without llvmlite/clang.
"""

import unittest

from tests.support import build_and_run_pascal_project, parse_source, requires_exe


def _ir(src):
    from pascal1981.codegen import compile_to_llvm
    return str(compile_to_llvm(parse_source(src)))


class TestCmdlineIR(unittest.TestCase):
    """Structural checks on the generated main()."""

    def setUp(self):
        try:
            import llvmlite.ir  # noqa: F401
        except ImportError:
            self.skipTest('llvmlite not available')

    def test_main_takes_argc_argv(self):
        ir = _ir("PROGRAM p(n);\nVAR n: INTEGER;\nBEGIN WRITELN(n) END.")
        self.assertIn('define i32 @"main"(i32 %"argc", i8** %"argv")', ir)

    def test_bindable_parameter_emits_args_init(self):
        ir = _ir("PROGRAM p(n);\nVAR n: INTEGER;\nBEGIN WRITELN(n) END.")
        self.assertIn('pas_args_init', ir)
        self.assertIn('pas_arg_begin', ir)
        self.assertIn('pas_arg_end', ir)

    def test_output_only_program_stays_runtime_free(self):
        # A program whose only heading parameter is OUTPUT binds nothing, so the
        # command-line runtime must not be referenced (it would force linking
        # libpascalrt even for programs that take no input).
        ir = _ir("PROGRAM p(output);\nBEGIN WRITELN(1) END.")
        self.assertNotIn('pas_args_init', ir)
        self.assertNotIn('pas_arg_begin', ir)

    def test_input_output_not_positional(self):
        # INPUT/OUTPUT occupy no command-line position: the real parameter `n`
        # is bound at position 0 even though it follows OUTPUT in the heading.
        ir = _ir("PROGRAM p(output, n);\nVAR n: INTEGER;\nBEGIN WRITELN(n) END.")
        # pas_arg_begin(i32 0, ...) for the first (and only) bindable parameter.
        self.assertIn('pas_arg_begin', ir)
        self.assertIn('i32 0,', ir.split('pas_arg_begin')[1][:40])


@requires_exe
class TestCmdlineBuildAndRun(unittest.TestCase):
    """End-to-end: parameters populated from argv, with keyboard fallback."""

    _MIXED = ("PROGRAM mandel(view, scale, tag);\n"
              "VAR view: INTEGER; scale: REAL; tag: LSTRING(32);\n"
              "BEGIN\n"
              "  WRITELN('view=', view);\n"
              "  WRITELN('scale=', scale:6:3);\n"
              "  WRITELN('tag=', tag)\n"
              "END.")

    def _run(self, src, exe, run_args=None, stdin=''):
        rc, out, err = build_and_run_pascal_project(
            files={'m.pas': src},
            compile_pairs=[('m.pas', 'm.ll')],
            link_ir_relpaths=['m.ll'],
            exe_name=exe,
            run_args=run_args,
            stdin=stdin,
        )
        self.assertEqual(rc, 0, msg=err)
        return [ln.rstrip() for ln in out.splitlines()]

    def test_all_args_from_command_line(self):
        out = self._run(self._MIXED, 'cli-all', run_args=['3', '0.75', 'zoomA'])
        self.assertEqual(out, ['view=3', 'scale= 0.750', 'tag=zoomA'])

    def test_keyboard_fallback_when_no_args(self):
        # No argv: each parameter prompts and reads a line from stdin.
        out = self._run(self._MIXED, 'cli-kbd', run_args=[], stdin='7\n1.5\nfoo\n')
        # Prompts go to stdout interleaved; assert the parsed values landed.
        joined = '\n'.join(out)
        self.assertIn('view=7', joined)
        self.assertIn('scale= 1.500', joined)
        self.assertIn('tag=foo', joined)

    def test_partial_args_then_prompt(self):
        # One arg on the command line, the rest prompted from stdin.
        out = self._run(self._MIXED, 'cli-partial', run_args=['9'], stdin='2.5\nbar\n')
        joined = '\n'.join(out)
        self.assertIn('view=9', joined)
        self.assertIn('scale= 2.500', joined)
        self.assertIn('tag=bar', joined)

    def test_file_parameter_filename_from_command_line(self):
        # A FILE program parameter takes its filename from the command line; a
        # later REWRITE opens exactly that file.
        import os
        import tempfile
        target = os.path.join(tempfile.mkdtemp(), 'cli_out.txt')
        src = ("PROGRAM writef(outfile, n);\n"
               "VAR outfile: TEXT; n: INTEGER; i: INTEGER;\n"
               "BEGIN\n"
               "  REWRITE(outfile);\n"
               "  FOR i := 1 TO n DO WRITELN(outfile, 'line ', i);\n"
               "  CLOSE(outfile);\n"
               "  WRITELN('wrote ', n)\n"
               "END.")
        out = self._run(src, 'cli-file', run_args=[target, '3'])
        self.assertEqual(out, ['wrote 3'])
        with open(target) as f:
            self.assertEqual([ln.rstrip() for ln in f], ['line 1', 'line 2', 'line 3'])

    def test_char_parameter(self):
        src = ("PROGRAM p(c);\nVAR c: CHAR;\nBEGIN WRITELN('c=', c) END.")
        out = self._run(src, 'cli-char', run_args=['Q'])
        self.assertEqual(out, ['c=Q'])


if __name__ == '__main__':
    unittest.main()
