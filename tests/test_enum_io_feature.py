import unittest

from tests.support import requires_exe
from tests.test_codegen import _build_pascal_with_runtime, build_and_run


@requires_exe
class TestEnumIoFeature(unittest.TestCase):

    def test_boolean_write_always_names(self):
        src = "PROGRAM P; BEGIN WRITELN(TRUE); WRITELN(FALSE) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "TRUE\nFALSE\n")
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], features={'symbolic-enum-io': True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "TRUE\nFALSE\n")

    def test_enum_write_ordinal_by_default(self):
        src = "PROGRAM P; TYPE Color=(RED,GREEN,BLUE); BEGIN WRITELN(GREEN) END."
        rc, out = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "1\n")

    def test_enum_write_symbolic_under_feature(self):
        src = "PROGRAM P; TYPE Color=(RED,GREEN,BLUE); BEGIN WRITELN(GREEN) END."
        rc, out = _build_pascal_with_runtime(src, ["readq.c"], features={'symbolic-enum-io': True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "GREEN\n")


if __name__ == "__main__":
    unittest.main()
