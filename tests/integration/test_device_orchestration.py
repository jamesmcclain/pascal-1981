"""Milestone D — host device orchestration on the CPU-device path.

cuda-kernel-prescription.md §5/§7: a host Pascal program allocates device
buffers, copies host arrays in, LAUNCHes a kernel, copies the result back, and
prints it.  On the CPU-device stand-in DEVALLOC=malloc, the copies are memcpy,
and LAUNCH is a direct call to the kernel (which runs as a single-thread grid,
so its grid-stride loop still covers the whole buffer).  This is the §5.5
minimal-orchestration acceptance, runnable with no GPU.

Also pins the host-only restriction: the orchestration builtins are rejected
inside DEVICE code.
"""

import unittest

from pascal1981.features import resolve_features
from tests.support import (build_and_run_pascal_project, requires_exe, typecheck_source)

# wide-integers lets the *host* program name INTEGER32 for its buffers so the
# 4-byte element layout matches the device kernel's INTEGER32 buffers.
_WIDE = resolve_features(overrides=['wide-integers'])

_INTERFACE = """\
DEVICE INTERFACE;
UNIT vadd (add);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""

_IMPLEMENTATION = """\
(*$INCLUDE:'vadd.inc'*)
DEVICE IMPLEMENTATION OF vadd;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR
  i, stride: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < n DO
  BEGIN
    c^[i] := a^[i] + b^[i];
    i := i + stride
  END
END;
.
"""

_MAIN = """\
(*$INCLUDE:'vadd.inc'*)
PROGRAM main(output);
USES vadd (add);
CONST n = 8;
VAR
  ha, hb, hc: ARRAY [0..7] OF INTEGER32;
  da, db, dc: ADRMEM;
  i: INTEGER;
  bytes: INTEGER;
BEGIN
  bytes := n * 4;
  FOR i := 0 TO n - 1 DO
  BEGIN
    ha[i] := i;
    hb[i] := i + i;
    hc[i] := 0
  END;
  da := DEVALLOC(bytes);
  db := DEVALLOC(bytes);
  dc := DEVALLOC(bytes);
  DEVCOPYTO(da, ADR ha, bytes);
  DEVCOPYTO(db, ADR hb, bytes);
  LAUNCH(add, 1, n, da, db, dc, n);
  DEVCOPYFROM(ADR hc, dc, bytes);
  FOR i := 0 TO n - 1 DO
    WRITELN(hc[i]);
  DEVFREE(da);
  DEVFREE(db);
  DEVFREE(dc)
END.
"""


@requires_exe
class TestDeviceOrchestrationVectorAdd(unittest.TestCase):

    def test_allocate_copy_launch_copyback_runs(self):
        rc, out, err = build_and_run_pascal_project(
            files={
                'vadd.inc': _INTERFACE,
                'vadd.pas': _IMPLEMENTATION,
                'main.pas': _MAIN,
            },
            compile_pairs=[
                ('vadd.inc', 'vadd-iface.ll'),
                ('vadd.pas', 'vadd.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['vadd.ll', 'main.ll'],
            exe_name='vadd-orchestration',
            features=_WIDE,
        )
        self.assertEqual(rc, 0, msg=err)
        # c[i] = a[i] + b[i] = i + 2i = 3i, for i = 0..7.
        self.assertEqual(out.split(), ['0', '3', '6', '9', '12', '15', '18', '21'])


class TestOrchestrationIsHostOnly(unittest.TestCase):
    """The orchestration builtins must be rejected inside DEVICE code."""

    def _device_module_calling(self, call: str):
        return typecheck_source(
            "DEVICE MODULE m;\n"
            "PROCEDURE k;\n"
            "VAR p: ADRMEM;\n"
            "BEGIN\n"
            f"  {call}\n"
            "END;\n"
            ".\n",
            features=_WIDE,
        )

    def test_devalloc_rejected_in_device_code(self):
        result = self._device_module_calling("p := DEVALLOC(16)")
        self.assertFalse(result.success)
        self.assertTrue(any('DEVALLOC' in e and 'host' in e.lower() for e in map(str, result.errors)), result.errors)

    def test_launch_rejected_in_device_code(self):
        result = self._device_module_calling("LAUNCH(k, 1, 1)")
        self.assertFalse(result.success)
        self.assertTrue(any('LAUNCH' in e and 'host' in e.lower() for e in map(str, result.errors)), result.errors)

    def test_devcopyto_rejected_in_device_code(self):
        result = self._device_module_calling("DEVCOPYTO(p, p, 16)")
        self.assertFalse(result.success)
        self.assertTrue(any('DEVCOPYTO' in e and 'host' in e.lower() for e in map(str, result.errors)), result.errors)


if __name__ == '__main__':
    unittest.main()
