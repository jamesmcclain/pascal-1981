"""Multi-file integration test: DEVICE UNIT primes via USES."""

import unittest

from tests.support import build_and_run_pascal_project, requires_exe

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
class TestDevicePrimesIntegration(unittest.TestCase):
    def test_device_unit_primes_builds_and_runs_via_uses(self):
        rc, out, err = build_and_run_pascal_project(
            files={
                'kernel.inc': _INTERFACE,
                'kernel.pas': _IMPLEMENTATION,
                'main.pas': _MAIN,
            },
            compile_pairs=[
                ('kernel.inc', 'kernel-interface.ll'),
                ('kernel.pas', 'kernel.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['kernel.ll', 'main.ll'],
            exe_name='primes',
            link_flags=[],
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([line.strip() for line in out.splitlines() if line.strip()], _EXPECTED)


if __name__ == '__main__':
    unittest.main()
