"""CPU-device integration test for SYNCTHREADS in shared-staging DEVICE code."""

import unittest

from tests.support import build_and_run_pascal_project, requires_exe

_INTERFACE = """DEVICE INTERFACE;
UNIT sharedsync (stage_values, checksum);
PROCEDURE stage_values;
FUNCTION checksum: INTEGER32;
END;
"""

_IMPLEMENTATION = """(*$INCLUDE:'sharedsync.inc'*)
DEVICE IMPLEMENTATION OF sharedsync;
CONST
  n = 8;
VAR
  [SPACE(SHARED)] scratch: ARRAY [0..7] OF INTEGER32;
  [SPACE(GLOBAL)] out: ARRAY [0..7] OF INTEGER32;

PROCEDURE stage_values;
VAR
  i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  WHILE i < n DO
  BEGIN
    scratch[i] := i + 1;
    i := i + BLOCKDIM_X * GRIDDIM_X
  END;

  SYNCTHREADS;

  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  WHILE i < n DO
  BEGIN
    out[i] := scratch[i] + scratch[i];
    i := i + BLOCKDIM_X * GRIDDIM_X
  END
END;

FUNCTION checksum: INTEGER32;
VAR
  i: INTEGER;
  total: INTEGER32;
BEGIN
  total := 0;
  FOR i := 0 TO n - 1 DO
    total := total + out[i];
  checksum := total
END;
.
"""

_MAIN = """(*$INCLUDE:'sharedsync.inc'*)
PROGRAM main(output);
USES sharedsync;
BEGIN
  stage_values;
  WRITELN(checksum)
END.
"""


@requires_exe
class TestDeviceSharedSyncthreadsIntegration(unittest.TestCase):

    def test_cpu_device_shared_staging_with_syncthreads_runs_serially(self):
        rc, out, err = build_and_run_pascal_project(
            files={
                'sharedsync.inc': _INTERFACE,
                'sharedsync.pas': _IMPLEMENTATION,
                'main.pas': _MAIN,
            },
            compile_pairs=[
                ('sharedsync.inc', 'sharedsync-interface.ll'),
                ('sharedsync.pas', 'sharedsync.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['sharedsync.ll', 'main.ll'],
            exe_name='shared-syncthreads',
        )
        self.assertEqual(rc, 0, msg=err)
        # 2 * sum(1..8) = 72.
        self.assertEqual(out.strip(), '72')


if __name__ == '__main__':
    unittest.main()
