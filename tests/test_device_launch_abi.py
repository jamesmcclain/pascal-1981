"""Milestone D — the real host launch ABI (cuda-kernel-prescription.md §5.4).

LAUNCH no longer lowers to a direct call of the kernel.  It marshals the kernel
arguments into a ``void**`` array (the shape ``cuLaunchKernel`` consumes) and
calls the ``pas_dev_launch`` shim with the kernel-name string, a per-kernel host
dispatch thunk, and the six geometry values.  On the CPU device the shim runs
the thunk (single-thread grid); the same call site is reused unchanged when the
shim is swapped for the CUDA driver path, which dispatches by name and ignores
the thunk.

These tests pin three things:
  1. the host program reaches the kernel *only* through the thunk — there is no
     direct launch call in the host body, and LAUNCH goes through pas_dev_launch
     with a marshalled argument array (IR level);
  2. the widened 2-or-6 geometry surface runs correctly end to end (the 6-value
     gx,gy,gz, bx,by,bz form, complementing the 2-value form already covered by
     test_device_orchestration);
  3. the geometry count (2 or 6, derived from the kernel's arity) is enforced by
     the type checker.
"""

import re
import unittest

from pascal1981.features import resolve_features
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import (build_and_run_pascal_project, compile_pascal_file, parse_source, requires_exe, requires_llvm, temporary_pascal_project, typecheck_source)

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


def _main_with_launch(launch_line: str) -> str:
    return f"""\
(*$INCLUDE:'vadd.inc'*)
PROGRAM main(output);
USES vadd (add);
CONST n = 8;
VAR
  ha, hb, hc: ARRAY [0..7] OF INTEGER32;
  da, db, dc: ADRMEM;
  i, bytes: INTEGER;
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
  {launch_line}
  DEVCOPYFROM(ADR hc, dc, bytes);
  FOR i := 0 TO n - 1 DO
    WRITELN(hc[i]);
  DEVFREE(da);
  DEVFREE(db);
  DEVFREE(dc)
END.
"""


@requires_llvm
class TestLaunchLowersThroughShim(unittest.TestCase):
    """LAUNCH emits the pas_dev_launch ABI, not a direct kernel call."""

    def test_host_reaches_kernel_only_through_thunk(self):
        files = {
            'vadd.inc': _INTERFACE,
            'vadd.pas': _IMPLEMENTATION,
            'main.pas': _main_with_launch('LAUNCH(add, 1, n, da, db, dc, n);'),
        }
        with temporary_pascal_project(files) as proj:
            out = compile_pascal_file(f'{proj}/main.pas', f'{proj}/main.ll', features=_WIDE)
            ir = open(out).read()

        # The launch goes through the shim with a marshalled argument array...
        self.assertIn('pas_dev_launch', ir)
        self.assertIn('launch_argv', ir)
        # ...and a per-kernel dispatch thunk is emitted.
        self.assertIn('__pas_klaunch_add', ir)

        # The kernel is *called* exactly once in the whole module — inside the
        # thunk.  A direct host-side launch call (the old lowering) would be a
        # second call site.  The bare `declare ... @add(...)` is not a call.
        kernel_calls = re.findall(r'call [^\n]*@"add"\(', ir)
        self.assertEqual(len(kernel_calls), 1, ir)


@requires_exe
class TestSixValueGeometryRuns(unittest.TestCase):
    """The widened gx,gy,gz, bx,by,bz geometry form runs end to end."""

    def test_three_dim_geometry_vector_add(self):
        files = {
            'vadd.inc': _INTERFACE,
            'vadd.pas': _IMPLEMENTATION,
            # 6-value geometry: a 1x1x1 grid of 1x1x1 blocks (single-thread grid,
            # so the grid-stride loop still covers the buffer on the CPU device).
            'main.pas': _main_with_launch('LAUNCH(add, 1, 1, 1, 1, 1, 1, da, db, dc, n);'),
        }
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=[
                ('vadd.inc', 'vadd-iface.ll'),
                ('vadd.pas', 'vadd.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['vadd.ll', 'main.ll'],
            exe_name='vadd-geom6',
            features=_WIDE,
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['0', '3', '6', '9', '12', '15', '18', '21'])


class TestLaunchGeometryArity(unittest.TestCase):
    """Geometry must be 2 (grid, block) or 6 (gx,gy,gz, bx,by,bz) values."""

    _PROG = """\
PROGRAM p(output);
VAR d: ADRMEM; m: INTEGER;
PROCEDURE k(a: ADRMEM; n: INTEGER);
BEGIN END;
BEGIN
{body}
END.
"""

    def _check(self, body: str):
        return typecheck_source(self._PROG.format(body=f'  {body}'))

    def test_two_value_geometry_accepted(self):
        result = self._check('LAUNCH(k, 1, 1, d, m)')
        self.assertTrue(result.success, result.errors)

    def test_six_value_geometry_accepted(self):
        result = self._check('LAUNCH(k, 1, 1, 1, 1, 1, 1, d, m)')
        self.assertTrue(result.success, result.errors)

    def test_three_value_geometry_rejected(self):
        result = self._check('LAUNCH(k, 1, 1, 1, d, m)')
        self.assertFalse(result.success)
        self.assertTrue(any('geometry' in str(e).lower() for e in result.errors), result.errors)

    def test_non_integer_geometry_rejected(self):
        result = self._check("LAUNCH(k, 1, 'x', d, m)")
        self.assertFalse(result.success)
        self.assertTrue(any('geometry' in str(e).lower() and 'integer' in str(e).lower() for e in result.errors), result.errors)


if __name__ == '__main__':
    unittest.main()
