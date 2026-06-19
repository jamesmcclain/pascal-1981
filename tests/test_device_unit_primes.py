"""Phase 1.6 parity acceptance: DEVICE UNIT primes via USES."""

import os
import shutil
import subprocess
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import requires_exe

RUNTIME_LIB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "runtime",
    "build",
    "libpascalrt.a",
)

_INTERFACE = """DEVICE INTERFACE;
UNIT kernel (build_primes, prime_count, nth_prime);
PROCEDURE build_primes;
FUNCTION prime_count: INTEGER;
FUNCTION nth_prime(n: INTEGER): INTEGER;
END;
"""

_IMPLEMENTATION = """DEVICE IMPLEMENTATION OF kernel;
CONST
  limit = 100;
VAR
  [SPACE(SHARED)] work_flags: ARRAY [0..99] OF CHAR;
  [SPACE(GLOBAL)] prime_flags: ARRAY [0..99] OF CHAR;

PROCEDURE build_primes;
VAR
  i, j: INTEGER;
BEGIN
  FOR i := 0 TO limit - 1 DO
    work_flags[i] := 'Y';

  work_flags[0] := 'N';
  work_flags[1] := 'N';

  FOR i := 2 TO limit - 1 DO
    IF work_flags[i] = 'Y' THEN
    BEGIN
      j := i + i;
      WHILE j < limit DO
      BEGIN
        work_flags[j] := 'N';
        j := j + i
      END
    END;

  MOVESL(ADS work_flags, ADS prime_flags, WRD(limit))
END;

FUNCTION prime_count: INTEGER;
VAR
  i, count: INTEGER;
BEGIN
  count := 0;
  FOR i := 0 TO limit - 1 DO
    IF prime_flags[i] = 'Y' THEN
      count := count + 1;
  prime_count := count
END;

FUNCTION nth_prime(n: INTEGER): INTEGER;
VAR
  i, count: INTEGER;
BEGIN
  count := 0;
  nth_prime := 0;
  FOR i := 0 TO limit - 1 DO
    IF prime_flags[i] = 'Y' THEN
    BEGIN
      count := count + 1;
      IF count = n THEN
        nth_prime := i
    END
END;
.
"""

_MAIN = """PROGRAM main(output);
USES kernel;
VAR
  i, count: INTEGER;
BEGIN
  build_primes;
  count := prime_count;
  FOR i := 1 TO count DO
    WRITELN(nth_prime(i))
END.
"""

_EXPECTED = [
    "2", "3", "5", "7", "11", "13", "17", "19", "23", "29",
    "31", "37", "41", "43", "47", "53", "59", "61", "67", "71",
    "73", "79", "83", "89", "97",
]


@requires_exe
class TestDeviceUnitPrimesParity(unittest.TestCase):
    def test_device_unit_primes_builds_and_runs_via_uses(self):
        tmpdir = tempfile.mkdtemp()
        try:
            iface_path = os.path.join(tmpdir, "kernel")
            impl_path = os.path.join(tmpdir, "kernel.pas")
            main_path = os.path.join(tmpdir, "main.pas")
            iface_ll = os.path.join(tmpdir, "kernel-interface.ll")
            impl_ll = os.path.join(tmpdir, "kernel.ll")
            main_ll = os.path.join(tmpdir, "main.ll")
            exe_path = os.path.join(tmpdir, "primes")

            for path, content in (
                (iface_path, _INTERFACE),
                (impl_path, _IMPLEMENTATION),
                (main_path, _MAIN),
            ):
                with open(path, "w") as f:
                    f.write(content)

            for source_path, out_path in (
                (iface_path, iface_ll),
                (impl_path, impl_ll),
                (main_path, main_ll),
            ):
                ast = parse_file(source_path)
                result = PascalTypeChecker(source_file=source_path).check(ast)
                self.assertTrue(result.success, msg=result.errors)
                ir = compile_to_llvm(ast, source_file=source_path)
                with open(out_path, "w") as f:
                    f.write(ir)

            clang = subprocess.run(
                [
                    "clang",
                    impl_ll,
                    main_ll,
                    RUNTIME_LIB,
                    "-Wl,--allow-multiple-definition",
                    "-o",
                    exe_path,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(clang.returncode, 0, msg=clang.stderr)

            run = subprocess.run([exe_path], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(
                [line.strip() for line in run.stdout.splitlines() if line.strip()],
                _EXPECTED,
            )
        finally:
            shutil.rmtree(tmpdir)


if __name__ == '__main__':
    unittest.main()
