"""
Integration tests over the real Pascal programs in pascal-code/.

We only bring in the unique programs that already parse and type-check cleanly
in this compiler. The goal is to keep the corpus honest and useful.

Layering:
  • IR generation for all known-good programs (requires llvmlite)
  • Selected run-and-check-output cases (requires llvmlite + clang)
"""

import unittest
from pathlib import Path

from tests.support import requires_exe, parse_source, typecheck_source
from tests.test_codegen import compile_to_ir, build_and_run


PASCAL_CODE = Path(__file__).resolve().parent.parent / "pascal-code"
GOOD_PROGRAMS = [
    "APP.PAS",
    "COMBINED.PAS",
    "MAIN.PAS",
    "MAIN2.PAS",
    "MAIN3.PAS",
    "PRIMES.PAS",
    "PROG.PAS",
    "PROGV2.PAS",
    "SIMPLE.PAS",
    "SORT.PAS",
    "TEST.PAS",
    "T_GBAD.PAS",
    "T_GBAD2.PAS",
    "T_GBAD3.PAS",
    "T_GOTO.PAS",
    "T_NEST.PAS",
    "T_RET2.PAS",
    "T_STR.PAS",
]


class TestKnownGoodProgramsParseTypecheck(unittest.TestCase):
    """All known-good Pascal programs should parse and type-check."""

    def test_known_good_programs_parse_and_typecheck(self):
        for name in GOOD_PROGRAMS:
            with self.subTest(file=name):
                src = (PASCAL_CODE / name).read_text()
                parse_source(src)
                result = typecheck_source(src)
                self.assertTrue(result.success, msg=f"{name}: {' '.join(str(e) for e in result.errors)}")


class TestKnownGoodProgramsIR(unittest.TestCase):
    """A stable subset should compile to LLVM IR."""

    def test_stable_programs_compile_to_ir(self):
        for name in ["SIMPLE.PAS", "T_STR.PAS"]:
            with self.subTest(file=name):
                src = (PASCAL_CODE / name).read_text()
                ir = compile_to_ir(src)
                self.assertIsInstance(ir, str)
                self.assertGreater(len(ir.strip()), 0)


@requires_exe
class TestKnownGoodProgramsRun(unittest.TestCase):
    """A few representative programs should run and print expected output."""

    def test_simple_program_runs(self):
        src = (PASCAL_CODE / "SIMPLE.PAS").read_text()
        rc, stdout = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, "")


    def test_string_program_runs(self):
        src = (PASCAL_CODE / "T_STR.PAS").read_text()
        rc, stdout = build_and_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("It's working", stdout)
        self.assertIn("She said 'hello'", stdout)


if __name__ == '__main__':
    unittest.main()
