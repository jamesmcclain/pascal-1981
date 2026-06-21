"""CPU-device integration test for grid-stride indexing.

The host CPU is the device for this test: DEVICE code lowers with the x86
CPU-device constants for THREADIDX/BLOCKIDX/BLOCKDIM/GRIDDIM.  The grid-stride
loop must still cover the full vector when that collapses to one thread and one
block.
"""

import unittest

from tests.support import build_and_run_pascal_project, requires_exe

_INTERFACE = """DEVICE INTERFACE;
UNIT vadd (init_vectors, add_vectors, checksum);
PROCEDURE init_vectors;
PROCEDURE add_vectors;
FUNCTION checksum: INTEGER32;
END;
"""

_IMPLEMENTATION = """(*$INCLUDE:'vadd.inc'*)
DEVICE IMPLEMENTATION OF vadd;
CONST
  n = 16;
VAR
  [SPACE(GLOBAL)] a: ARRAY [0..15] OF INTEGER32;
  [SPACE(GLOBAL)] b: ARRAY [0..15] OF INTEGER32;
  [SPACE(GLOBAL)] c: ARRAY [0..15] OF INTEGER32;

PROCEDURE init_vectors;
VAR
  i: INTEGER;
BEGIN
  FOR i := 0 TO n - 1 DO
  BEGIN
    a[i] := i;
    b[i] := i + i;
    c[i] := 0
  END
END;

PROCEDURE add_vectors;
VAR
  i, stride: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < n DO
  BEGIN
    c[i] := a[i] + b[i];
    i := i + stride
  END
END;

FUNCTION checksum: INTEGER32;
VAR
  i: INTEGER;
  total: INTEGER32;
BEGIN
  total := 0;
  FOR i := 0 TO n - 1 DO
    total := total + c[i];
  checksum := total
END;
.
"""

_MAIN = """(*$INCLUDE:'vadd.inc'*)
PROGRAM main(output);
USES vadd;
BEGIN
  init_vectors;
  add_vectors;
  WRITELN(checksum)
END.
"""


@requires_exe
class TestDeviceGridStrideIntegration(unittest.TestCase):

    def test_cpu_device_grid_stride_vector_add_covers_full_array(self):
        rc, out, err = build_and_run_pascal_project(
            files={
                'vadd.inc': _INTERFACE,
                'vadd.pas': _IMPLEMENTATION,
                'main.pas': _MAIN,
            },
            compile_pairs=[
                ('vadd.inc', 'vadd-interface.ll'),
                ('vadd.pas', 'vadd.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['vadd.ll', 'main.ll'],
            exe_name='grid-stride-vadd',
        )
        self.assertEqual(rc, 0, msg=err)
        # Sum over i=0..15 of i + 2*i = 3 * (15*16/2) = 360.
        self.assertEqual(out.strip(), '360')


if __name__ == '__main__':
    unittest.main()
