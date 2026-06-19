"""Checklist S2.2.2 — device IR carries no host-runtime extern dump.

`_register_predeclared_externs` used to add the whole host-runtime family
(fillc/fillsc/movel/mover/movesl/movesr/memmove/pas_read_*/...) to *every*
module at construction, so device IR shipped dead host-runtime `declare`s even
though the seg-bridge lowers FILLSC/MOVESL/MOVESR inline and host I/O/heap are
rescinded in device code.  S2.2.1 skips that dump for a device compiland that
lowers to a GPU triple.

These are *artifact*-level guards: they compile to LLVM IR and assert on the
emitted module's symbol references, catching the whole class of leak rather
than one symbol.  The forbidden set is the union named by the checklist green
gate (S2.2.2) and the S2.1 trap pair, so this file doubles as the comprehensive
"no host-runtime symbol in device IR" check.
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

# The full set that must never appear in device IR lowered to a GPU triple:
# the S2.2.2 host-runtime externs plus the S2.1 host-trap pair.
_FORBIDDEN = (
    'abort', 'fflush', 'memmove',
    'movel', 'mover', 'movesl', 'movesr', 'fillc', 'fillsc',
    'pas_read_int', 'pas_read_word', 'pas_read_real',
)

_GPU_TRIPLES = ('nvptx64-nvidia-cuda', 'amdgcn-amd-amdhsa')


def _refs(ir_text, sym):
    return len(re.findall(r'\b' + re.escape(sym) + r'\b', ir_text))


def _present(ir_text):
    return [s for s in _FORBIDDEN if _refs(ir_text, s)]


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


def _compile_unit(iface_src, impl_src, module_name='U', **kw):
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


# A device unit doing vector-add over two GLOBAL arrays (the checklist's
# canonical example), plus a MOVESL seg-bridge use so the segmented externs
# would definitely be dumped under the old behavior.
_VADD_IFACE = (
    "DEVICE INTERFACE;\n"
    "UNIT VADD (vecadd);\n"
    "PROCEDURE vecadd (n: INTEGER);\n"
    "END;\n"
)
_VADD_IMPL = (
    "DEVICE IMPLEMENTATION OF VADD;\n"
    "VAR [SPACE(GLOBAL)] a: ARRAY [1..256] OF INTEGER;\n"
    "    [SPACE(GLOBAL)] b: ARRAY [1..256] OF INTEGER;\n"
    "    [SPACE(SHARED)] s: CHAR;\n"
    "    [SPACE(GLOBAL)] g: CHAR;\n"
    "PROCEDURE vecadd (n: INTEGER);\n"
    "VAR i: INTEGER;\n"
    "BEGIN\n"
    "  FOR i := 1 TO n DO a[i] := a[i] + b[i];\n"
    "  MOVESL(ADS g, ADS s, WRD(1));\n"
    "END;\n"
    ".\n"
)

# Single-file DEVICE MODULE counterpart (no interface) doing the same shape.
_VADD_MODULE = (
    "DEVICE MODULE VADD;\n"
    "VAR [SPACE(GLOBAL)] a: ARRAY [1..256] OF INTEGER;\n"
    "    [SPACE(GLOBAL)] b: ARRAY [1..256] OF INTEGER;\n"
    "PROCEDURE vecadd (n: INTEGER);\n"
    "VAR i: INTEGER;\n"
    "BEGIN\n"
    "  FOR i := 1 TO n DO a[i] := a[i] + b[i];\n"
    "END;\n"
    ".\n"
)


class TestDeviceUnitNoHostExterns(unittest.TestCase):
    def test_vecadd_unit_has_no_host_runtime_symbols(self):
        for triple in _GPU_TRIPLES:
            with self.subTest(triple=triple):
                ir = _compile_unit(_VADD_IFACE, _VADD_IMPL, module_name='VADD', device_triple=triple)
                self.assertIn(f'target triple = "{triple}"', ir)
                self.assertEqual(_present(ir), [], f'host-runtime symbols leaked into {triple} device IR\n{ir}')

    def test_no_predeclared_extern_dump_at_all(self):
        # The dump is what produced the dead declares; assert it is gone
        # wholesale, not just symbol-by-symbol.  (A device unit with no host
        # calls should declare nothing host-runtime.)
        ir = _compile_unit(_VADD_IFACE, _VADD_IMPL, module_name='VADD', device_triple='nvptx64-nvidia-cuda')
        self.assertEqual(ir.count('declare'), 0)


class TestDeviceModuleNoHostExterns(unittest.TestCase):
    def test_device_module_has_no_host_runtime_symbols(self):
        # Proves the skip is not unit-specific: a single-file DEVICE MODULE on
        # a GPU triple is equally clean (it is device code; is_device is set).
        ir = _compile(_VADD_MODULE, device_triple='nvptx64-nvidia-cuda')
        self.assertEqual(_present(ir), [], ir)


class TestHostAndCpuDeviceUnchanged(unittest.TestCase):
    """The skip is scoped to GPU triples; everything else keeps the externs."""

    def test_plain_unit_still_dumps_externs(self):
        host_iface = "INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n"
        host_impl = "IMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n"
        ir = _compile_unit(host_iface, host_impl)
        # Host runtime is linked against, so the predeclared externs remain.
        self.assertGreater(_refs(ir, 'memmove'), 0)
        self.assertGreater(_refs(ir, 'movel'), 0)

    def test_x86_cpu_device_keeps_externs(self):
        # x86 CPU-device is NOT a GPU triple: it links the host runtime and so
        # keeps the externs.  This is the deliberate green-safe boundary
        # (checklist S2.2.1) — byte-identical to pre-change behavior.
        ir = _compile_unit(_VADD_IFACE, _VADD_IMPL, module_name='VADD')  # default x86 triple
        self.assertIn('target triple = "x86_64-pc-linux-gnu"', ir)
        self.assertGreater(_refs(ir, 'memmove'), 0)


if __name__ == '__main__':
    unittest.main()
