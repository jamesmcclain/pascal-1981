"""Regression: the IF/ELSE-of-assignment `select` peephole must not fire on
host code and speculatively evaluate the not-taken arm.

The peephole (followups.md item 2) rewrites a scalar ``IF c THEN x := a ELSE
x := b`` into a branchless LLVM ``select`` so the NVPTX backend emits ``selp``
instead of a divergent ``bra``.  Its correctness rests on evaluating *both*
arms unconditionally being equivalent to the source branch -- which is only
true where the host-trapping runtime checks are suppressed, i.e. device code.

On the host path, integer ``+``/``-``/``*`` lower through the ``$MATHCK``
overflow guard, which is on by default.  An earlier version of the peephole was
not gated on ``is_device_module``, so a host

    IF n > 0 THEN x := a + a ELSE x := 42

speculatively evaluated ``a + a`` on the (not-taken) THEN arm; when that sum
overflowed INTEGER the program aborted via the $MATHCK trap even though the
ELSE arm was the one selected.  The branch lowering never reaches the overflow,
so the correct observable behavior is to print 42 and exit 0.

These tests pin both the behavior (prints 42, exit 0) and the mechanism (no
``select`` is emitted for the host IF), and confirm that genuine *device* code
still gets the ``selp`` predication so the fix does not regress the
optimization it is gating.
"""

import os
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import build_and_run_pascal_project, requires_exe, requires_llvm

# A host program whose guarded arm overflows INTEGER (16-bit: -32768..32767).
# n = 0, so the ELSE arm runs and the program must print 42.  Speculatively
# evaluating the THEN arm (a + a = 60000) would overflow and trap under the
# default $MATHCK.  `a` is a variable so the sum is a runtime add, not folded.
_HOST_GUARDED_OVERFLOW = (
    "PROGRAM sel;\n"
    "VAR x, n, a: INTEGER;\n"
    "BEGIN\n"
    "  a := 30000;\n"
    "  n := 0;\n"
    "  IF n > 0 THEN x := a + a ELSE x := 42;\n"
    "  WRITELN(x)\n"
    "END.\n"
)


def _host_ir(src: str) -> str:
    """Lower a host program string to LLVM IR text (default x86 triples)."""
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, 'sel.pas')
        with open(path, 'w') as f:
            f.write(src)
        ast = parse_file(path)
        result = PascalTypeChecker(source_file=path).check(ast)
        assert result.success, result.errors
        return compile_to_llvm(ast, source_file=path)
    finally:
        shutil.rmtree(tmpdir)


class TestHostIfSelectNoSpeculativeMathck(unittest.TestCase):

    @requires_exe
    def test_guarded_overflow_arm_is_not_speculated(self):
        """The not-taken overflowing arm must not abort the program."""
        rc, out, err = build_and_run_pascal_project(
            files={'sel.pas': _HOST_GUARDED_OVERFLOW},
            compile_pairs=[('sel.pas', 'sel.ll')],
            link_ir_relpaths=['sel.ll'],
            exe_name='sel',
        )
        self.assertEqual(rc, 0, f"program aborted (rc={rc}); stderr={err!r}")
        self.assertEqual(out.strip(), '42', f"unexpected output: {out!r}")

    @requires_llvm
    def test_host_if_does_not_lower_to_select(self):
        """Host IF/ELSE-of-assignment stays a real branch, never a select."""
        ir = _host_ir(_HOST_GUARDED_OVERFLOW)
        self.assertNotRegex(
            ir, r'select\s+i1',
            "host IF/ELSE must not use the device-only select peephole")


class TestDeviceIfSelectStillPredicates(unittest.TestCase):
    """The gate must not regress the device optimization it protects."""

    _IFACE = ("DEVICE INTERFACE;\n"
              "UNIT SEL (guard);\n"
              "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
              "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
              "END;\n")
    _IMPL = ("(*$INCLUDE:'sel'*)\n"
             "DEVICE IMPLEMENTATION OF SEL;\n"
             "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
             "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
             "VAR w: REAL32;\n"
             "BEGIN\n"
             "  IF n > 1 THEN w := n - 1 ELSE w := 1;\n"
             "  output^[0] := w;\n"
             "END;\n"
             ".\n")

    @requires_llvm
    def test_device_guard_still_lowers_to_select(self):
        tmpdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmpdir, 'sel'), 'w') as f:
                f.write(self._IFACE)
            impl_path = os.path.join(tmpdir, 'sel.pas')
            with open(impl_path, 'w') as f:
                f.write(self._IMPL)
            ast = parse_file(impl_path)
            result = PascalTypeChecker(source_file=impl_path).check(ast)
            assert result.success, result.errors
            ir = compile_to_llvm(ast, source_file=impl_path,
                                 device_triple='nvptx64-nvidia-cuda')
        finally:
            shutil.rmtree(tmpdir)
        self.assertRegex(
            ir, r'select\s+i1',
            "device IF/ELSE-of-assignment should still use the select peephole")


if __name__ == '__main__':
    unittest.main()
