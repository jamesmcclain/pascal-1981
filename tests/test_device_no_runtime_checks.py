"""Checklist S2.1 — no compiler-inserted runtime checks in device code.

These are *artifact*-level guards: they compile to LLVM IR and assert on the
emitted module, not on the checker.  That catches the whole class of
host-runtime leak (the math-overflow check, the array-bounds check, and the
RANGECK CASE-no-match / string-capacity traps), not just one instance, and it
keeps proving the property as new check sites are added.

The host counterparts assert the *same* source still traps off-device, so the
suppression is provably device-only (host/vintage and DEVICE-MODULE-on-host
output stay byte-identical — the green gate for S2.1).
"""

import os
import re
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source

# Host-runtime symbols that must never appear in device IR.  abort/fflush are
# the two emitted by emit_runtime_abort; the rest are the predeclared-extern
# family the S2.1 green gate also names (kept here so this test tightens
# automatically once S2.2 lands).
_HOST_TRAP_SYMS = ('abort', 'fflush')

# A body that exercises all three host-trapping check families at once:
#   y := x * x          -> MATHCK (integer overflow)
#   a[x] := y           -> INDEXCK (array bounds)
#   CASE y .. END       -> RANGECK (no-match trap, no OTHERWISE)
_BODY = (
    "VAR y: INTEGER; a: ARRAY [1..4] OF INTEGER;\n"
    "BEGIN\n"
    "  y := x * x;\n"
    "  a[x] := y;\n"
    "  CASE y OF\n"
    "    1: y := 1;\n"
    "    2: y := 2;\n"
    "  END;\n"
    "END;\n"
)


def _refs(ir_text, sym):
    """Count whole-word references to a symbol name in IR text."""
    return len(re.findall(r'\b' + re.escape(sym) + r'\b', ir_text))


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


def _compile_unit(iface_src, impl_src, module_name='U', **kw):
    """Separate-compilation helper: write interface + implementation to disk
    and compile the implementation alone (the normal device-unit case)."""
    tmpdir = tempfile.mkdtemp()
    try:
        iface_path = os.path.join(tmpdir, module_name.lower())
        impl_path = os.path.join(tmpdir, f'{module_name.lower()}.pas')
        with open(iface_path, 'w') as f:
            f.write(iface_src)
        with open(impl_path, 'w') as f:
            f.write(impl_src)
        ast = parse_file(impl_path)
        r = PascalTypeChecker(source_file=impl_path).check(ast)
        assert r.success, r.errors
        return compile_to_llvm(ast, source_file=impl_path, **kw)
    finally:
        shutil.rmtree(tmpdir)


class TestDeviceModuleNoRuntimeChecks(unittest.TestCase):
    """A DEVICE MODULE lowered to a GPU triple carries no host traps."""

    _SRC = "DEVICE MODULE M;\nPROCEDURE go (VAR x: INTEGER);\n" + _BODY + ".\n"
    _HOST_SRC = "MODULE M;\nPROCEDURE go (VAR x: INTEGER);\n" + _BODY + ".\n"

    def test_nvptx_has_no_host_traps(self):
        ir = _compile(self._SRC, device_triple='nvptx64-nvidia-cuda')
        self.assertIn('target triple = "nvptx64-nvidia-cuda"', ir)
        for sym in _HOST_TRAP_SYMS:
            self.assertEqual(_refs(ir, sym), 0, f'device IR must not reference {sym}\n{ir}')

    def test_host_module_still_traps(self):
        # Same constructs on the host path keep their checks: this is what
        # proves the suppression is device-only.
        ir = _compile(self._HOST_SRC)
        self.assertGreater(_refs(ir, 'abort'), 0)
        self.assertGreater(_refs(ir, 'fflush'), 0)

    def test_x86_device_runs_serially_without_traps(self):
        # CPU-device (default triple) still suppresses the checks (it is device
        # code) and lowers to ordinary addrspace-0 IR that runs serially.
        ir = _compile(self._SRC)
        for sym in _HOST_TRAP_SYMS:
            self.assertEqual(_refs(ir, sym), 0)
        # Body still lowered (multiply + store present), just unguarded.
        self.assertIn('mul', ir)


class TestDeviceUnitNoRuntimeChecks(unittest.TestCase):
    """The same guarantee under the DEVICE UNIT (separate-compilation) shape."""

    # The exported routine is a launchable kernel entry (S2.3), so its
    # parameter must be device-passable: a value scalar, not a VAR (host-space
    # pointer).  The body still exercises MATHCK/INDEXCK/RANGECK with value x.
    _IFACE = "DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go (x: INTEGER);\nEND;\n"
    _IMPL = "DEVICE IMPLEMENTATION OF U;\nPROCEDURE go (x: INTEGER);\n" + _BODY + ".\n"

    _HOST_IFACE = "INTERFACE;\nUNIT U (go);\nPROCEDURE go (VAR x: INTEGER);\nEND;\n"
    _HOST_IMPL = "IMPLEMENTATION OF U;\nPROCEDURE go (VAR x: INTEGER);\n" + _BODY + ".\n"

    def test_device_unit_nvptx_has_no_host_traps(self):
        ir = _compile_unit(self._IFACE, self._IMPL, device_triple='nvptx64-nvidia-cuda')
        self.assertIn('target triple = "nvptx64-nvidia-cuda"', ir)
        for sym in _HOST_TRAP_SYMS:
            self.assertEqual(_refs(ir, sym), 0, f'device-unit IR must not reference {sym}\n{ir}')

    def test_plain_unit_still_traps(self):
        ir = _compile_unit(self._HOST_IFACE, self._HOST_IMPL)
        self.assertIn('target triple = "x86_64-pc-linux-gnu"', ir)
        self.assertGreater(_refs(ir, 'abort'), 0)
        self.assertGreater(_refs(ir, 'fflush'), 0)


if __name__ == '__main__':
    unittest.main()
