"""No dead host-runtime externs in any IR.

The lazy registration scheme (replacing the old eager dump) ensures that a
runtime extern only appears in the emitted module when codegen actually
references it.  Dead externs — never referenced — never appear in the IR,
for *every* compile path (host, GPU device, x86 CPU-device).

These are artifact-level guards: they compile to LLVM IR and assert on the
emitted module's symbol references, catching the whole class of leak rather
than one symbol.  The forbidden set is the union of the host-runtime externs and the
host-trap (abort/fflush) pair.
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
# the host-runtime externs plus the host-trap pair.
_FORBIDDEN = (
    'abort',
    'fflush',
    'memmove',
    'movel',
    'mover',
    'movesl',
    'movesr',
    'fillc',
    'fillsc',
    'pas_read_int',
    'pas_read_word',
    'pas_read_real',
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


# A device unit doing vector-add over two GLOBAL arrays (the
# canonical example), plus a MOVESL seg-bridge use so the segmented externs
# would definitely be dumped under the old behavior.
_VADD_IFACE = ("DEVICE INTERFACE;\n"
               "UNIT VADD (vecadd);\n"
               "PROCEDURE vecadd (n: INTEGER);\n"
               "END;\n")
_VADD_IMPL = ("(*$INCLUDE:'vadd'*)\n"
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
              ".\n")

# Single-file DEVICE MODULE counterpart (no interface) doing the same shape.
_VADD_MODULE = ("DEVICE MODULE VADD;\n"
                "VAR [SPACE(GLOBAL)] a: ARRAY [1..256] OF INTEGER;\n"
                "    [SPACE(GLOBAL)] b: ARRAY [1..256] OF INTEGER;\n"
                "PROCEDURE vecadd (n: INTEGER);\n"
                "VAR i: INTEGER;\n"
                "BEGIN\n"
                "  FOR i := 1 TO n DO a[i] := a[i] + b[i];\n"
                "END;\n"
                ".\n")


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


def _io_globals(ir_text):
    """Return the predeclared host-stream globals (input/output) that appear as
    module-level global definitions/declarations in the IR.  Matches the
    `@"input" = ... global ...` / `@"output" = ... global ...` shape only, so a
    same-named local or parameter elsewhere would not be mistaken for a leak."""
    found = []
    for name in ('input', 'output'):
        if re.search(r'@"?' + name + r'"?\s*=\s*[^\n]*\bglobal\b', ir_text):
            found.append(name)
    return found


class TestDeviceNoPhantomInputOutput(unittest.TestCase):
    """followups.md item 2: the predeclared INPUT/OUTPUT host-stream globals
    must not be emitted in DEVICE compilands.  They are unreferenced there (a
    device kernel has no host I/O) and used to leak two dead
    `.extern .global ... input/output` lines into the device PTX."""

    def test_device_module_has_no_input_output_globals(self):
        for triple in _GPU_TRIPLES + ('x86_64-pc-linux-gnu', ):
            with self.subTest(triple=triple):
                ir = _compile(_VADD_MODULE, device_triple=triple)
                self.assertEqual(_io_globals(ir), [], f'phantom input/output globals leaked into {triple} device IR\n{ir}')

    def test_device_unit_has_no_input_output_globals(self):
        for triple in _GPU_TRIPLES + ('x86_64-pc-linux-gnu', ):
            with self.subTest(triple=triple):
                ir = _compile_unit(_VADD_IFACE, _VADD_IMPL, module_name='VADD', device_triple=triple)
                self.assertEqual(_io_globals(ir), [], f'phantom input/output globals leaked into {triple} device unit IR\n{ir}')

    def test_host_program_still_defines_input_output(self):
        # Regression guard: the host owner keeps the strong definitions;
        # the device suppression must not touch the host path.
        ir = _compile("PROGRAM P;\nBEGIN\n WRITELN('hi');\nEND.\n")
        self.assertEqual(_io_globals(ir), ['input', 'output'], f'host PROGRAM lost its input/output globals\n{ir}')

    def test_host_module_still_declares_input_output(self):
        # A plain host MODULE keeps the declare-only externals (linker resolves
        # them to the PROGRAM root).  Only DEVICE compilands suppress them.
        ir = _compile_unit("INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n", "(*$INCLUDE:'u'*)\nIMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n")
        self.assertEqual(_io_globals(ir), ['input', 'output'], f'host MODULE lost its input/output declarations\n{ir}')


class TestLazyExternProperty(unittest.TestCase):
    """Lazy registration: externs appear IFF codegen references them."""

    def test_host_unit_with_no_io_or_strings_has_zero_host_runtime_declares(self):
        # A plain host UNIT that does no I/O, string ops, or heap allocation
        # must emit zero host-runtime declares under lazy registration.
        host_iface = "INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n"
        host_impl = "(*$INCLUDE:'u'*)\nIMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n"
        ir = _compile_unit(host_iface, host_impl)
        self.assertEqual(_present(ir), [], f'dead host-runtime externs in plain unit with no I/O:\n{ir}')
        # No declares at all for an empty procedure body
        self.assertEqual(ir.count('declare'), 0, f'unexpected declares in empty host unit:\n{ir}')

    def test_host_unit_with_movel_only_emits_movel(self):
        # A unit that uses MOVEL should emit exactly 'movel' and nothing else
        # from the host-runtime family.  This is the proof that lazy registration
        # is narrower than the old eager dump.
        host_iface = "INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n"
        host_impl = ("(*$INCLUDE:'u'*)\n"
                     "IMPLEMENTATION OF U;\n"
                     "VAR a, b: ARRAY[1..4] OF CHAR;\n"
                     "PROCEDURE go;\nBEGIN MOVEL(ADR a, ADR b, WRD(4)) END;\n.\n")
        ir = _compile_unit(host_iface, host_impl)
        self.assertGreater(_refs(ir, 'movel'), 0, 'movel should be referenced')
        self.assertEqual(_refs(ir, 'memmove'), 0, 'memmove should NOT appear')
        self.assertEqual(_refs(ir, 'mover'), 0, 'mover should NOT appear')

    def test_x86_cpu_device_unit_without_io_has_no_host_runtime_declares(self):
        # The lazy form is wider than the old GPU-triple gated skip:
        # even an x86 CPU-device unit emits zero host-runtime declares when
        # it doesn't reference any of them (the VADD kernel only does
        # MOVESL, which is lowered inline by the seg-bridge).
        ir = _compile_unit(_VADD_IFACE, _VADD_IMPL, module_name='VADD')  # default x86 triple
        self.assertIn('target triple = "x86_64-pc-linux-gnu"', ir)
        self.assertEqual(_present(ir), [], f'dead host-runtime externs in x86 device unit:\n{ir}')


if __name__ == '__main__':
    unittest.main()
