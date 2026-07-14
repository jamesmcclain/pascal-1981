"""Regression tests: IMPLEMENTATION OF X and DEVICE IMPLEMENTATION OF X
must include their own matching INTERFACE header.  Omitting the include
is a hard parse error — even when a matching interface file exists on disk.

These tests verify that the looser disk-fallback behaviour can never
silently creep back in.
"""

import os
import shutil
import tempfile
import unittest

from pascal1981.parser import ParserError, parse_file


def _write(directory, name, content):
    path = os.path.join(directory, name)
    with open(path, 'w') as f:
        f.write(content)
    return path


_PLAIN_IFACE = """\
INTERFACE;
UNIT graphics (move, plot);
PROCEDURE move (x, y: INTEGER);
PROCEDURE plot (x, y: INTEGER);
END;
"""

_DEVICE_IFACE = """\
DEVICE INTERFACE;
UNIT kernel (run);
PROCEDURE run (n: INTEGER);
END;
"""


class TestImplementationRequiresInclude(unittest.TestCase):
    """Host IMPLEMENTATION OF X must include its own interface header."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Write the interface file to disk — it exists, but the impl must NOT
        # rely on a disk-based fallback lookup; it must include it explicitly.
        _write(self.tmpdir, 'graphics', _PLAIN_IFACE)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_impl_without_include_is_a_parse_error(self):
        """IMPLEMENTATION OF GRAPHICS with no (*$INCLUDE:...*) must fail,
        even though graphics is present on disk in the same directory."""
        impl = """\
IMPLEMENTATION OF graphics;
PROCEDURE move (x, y: INTEGER); BEGIN END;
PROCEDURE plot (x, y: INTEGER); BEGIN END;
BEGIN
END.
"""
        impl_path = _write(self.tmpdir, 'graphics.pas', impl)
        with self.assertRaises(ParserError) as cm:
            parse_file(impl_path)
        self.assertIn('must include its matching INTERFACE header', str(cm.exception))
        self.assertIn('IMPLEMENTATION OF graphics', str(cm.exception))

    def test_impl_with_wrong_interface_included_is_a_parse_error(self):
        """(*$INCLUDE:'other'*) before IMPLEMENTATION OF GRAPHICS must fail
        because the spliced unit name ('other') does not match 'graphics'."""
        _write(self.tmpdir, 'other', """\
INTERFACE;
UNIT other (noop);
PROCEDURE noop;
END;
""")
        impl = """\
(*$INCLUDE:'other'*)
IMPLEMENTATION OF graphics;
PROCEDURE move (x, y: INTEGER); BEGIN END;
PROCEDURE plot (x, y: INTEGER); BEGIN END;
BEGIN
END.
"""
        impl_path = _write(self.tmpdir, 'graphics.pas', impl)
        with self.assertRaises(ParserError) as cm:
            parse_file(impl_path)
        self.assertIn('must include its matching INTERFACE header', str(cm.exception))

    def test_impl_with_correct_include_succeeds(self):
        """(*$INCLUDE:'graphics'*) before IMPLEMENTATION OF graphics must parse cleanly."""
        impl = """\
(*$INCLUDE:'graphics'*)
IMPLEMENTATION OF graphics;
PROCEDURE move (x, y: INTEGER); BEGIN END;
PROCEDURE plot (x, y: INTEGER); BEGIN END;
BEGIN
END.
"""
        impl_path = _write(self.tmpdir, 'graphics.pas', impl)
        ast = parse_file(impl_path)
        from pascal1981.ast_nodes import ImplementationUnit
        self.assertIsInstance(ast, ImplementationUnit)
        self.assertEqual(ast.name.upper(), 'GRAPHICS')
        self.assertIsNotNone(ast.interface)


class TestDeviceImplementationRequiresInclude(unittest.TestCase):
    """DEVICE IMPLEMENTATION OF X must include its own device interface header."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        _write(self.tmpdir, 'kernel', _DEVICE_IFACE)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_device_impl_without_include_is_a_parse_error(self):
        """DEVICE IMPLEMENTATION OF kernel with no (*$INCLUDE:...*) must fail,
        even though kernel is present on disk in the same directory."""
        impl = """\
DEVICE IMPLEMENTATION OF kernel;
PROCEDURE run (n: INTEGER);
VAR i: INTEGER;
BEGIN FOR i := 1 TO n DO ; END;
.
"""
        impl_path = _write(self.tmpdir, 'kernel.pas', impl)
        with self.assertRaises(ParserError) as cm:
            parse_file(impl_path)
        self.assertIn('must include its matching INTERFACE header', str(cm.exception))
        self.assertIn('IMPLEMENTATION OF kernel', str(cm.exception))

    def test_device_impl_with_wrong_interface_included_is_a_parse_error(self):
        """A spliced interface whose unit name mismatches must fail."""
        _write(self.tmpdir, 'other', """\
DEVICE INTERFACE;
UNIT other (noop);
PROCEDURE noop;
END;
""")
        impl = """\
(*$INCLUDE:'other'*)
DEVICE IMPLEMENTATION OF kernel;
PROCEDURE run (n: INTEGER);
VAR i: INTEGER;
BEGIN FOR i := 1 TO n DO ; END;
.
"""
        impl_path = _write(self.tmpdir, 'kernel.pas', impl)
        with self.assertRaises(ParserError) as cm:
            parse_file(impl_path)
        self.assertIn('must include its matching INTERFACE header', str(cm.exception))

    def test_device_impl_with_correct_include_succeeds(self):
        """(*$INCLUDE:'kernel'*) before DEVICE IMPLEMENTATION OF kernel must parse cleanly."""
        impl = """\
(*$INCLUDE:'kernel'*)
DEVICE IMPLEMENTATION OF kernel;
PROCEDURE run (n: INTEGER);
VAR i: INTEGER;
BEGIN FOR i := 1 TO n DO ; END;
.
"""
        impl_path = _write(self.tmpdir, 'kernel.pas', impl)
        ast = parse_file(impl_path)
        from pascal1981.ast_nodes import ImplementationUnit
        self.assertIsInstance(ast, ImplementationUnit)
        self.assertEqual(ast.name.upper(), 'KERNEL')
        self.assertTrue(ast.is_device)
        self.assertIsNotNone(ast.interface)


class TestProgramDoesNotRequireInclude(unittest.TestCase):
    """A PROGRAM is not an implementation; it must not be required to include
    anything before it can be parsed.  Existing module files on disk must not
    interfere with a clean program parse."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_bare_program_parses_without_any_include(self):
        src = "PROGRAM P; BEGIN END.\n"
        prog_path = _write(self.tmpdir, 'prog.pas', src)
        from pascal1981.ast_nodes import ProgramUnit
        ast = parse_file(prog_path)
        self.assertIsInstance(ast, ProgramUnit)


if __name__ == '__main__':
    unittest.main()
