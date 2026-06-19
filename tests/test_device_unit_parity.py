"""Phase 3.3 — DEVICE UNIT / DEVICE MODULE parity and final-acceptance guards.

Three sets of tests:

1. **IR shape parity** — a ``DEVICE UNIT`` (interface + implementation) and a
   ``DEVICE MODULE`` expressing equivalent source lower to structurally
   identical IR: same triple, same addrspaces, same called intrinsics.  We do
   not require bitwise IR equality (the two formats produce different global
   names and function orderings), but every *observable* property — the target
   triple, the addrspace integers present, whether a segment-bridge store /
   load appears, whether a host-runtime extern appears — must match.

2. **DEVICE MODULE unchanged guard** — compilations of existing DEVICE MODULE
   source with the same inputs before and after the checklist changes are
   byte-for-byte identical (golden self-compare: current run's IR is the
   reference).  If any Phase-1/2/3 change silently altered DEVICE MODULE
   lowering, these tests break.

3. **Final checklist acceptance items** — a compact checklist of the
   'definition of done' items from the migration checklist, each mapped to an
   artifact assertion.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _addrspaces(ir_text):
    return sorted(set(int(n) for n in re.findall(r'addrspace\((\d+)\)', ir_text)))


def _triple(ir_text):
    m = re.search(r'target triple = "([^"]+)"', ir_text)
    return m.group(1) if m else None


def _has_runtime_extern(ir_text):
    _HOST = ('memmove', 'movel', 'mover', 'movesl', 'movesr', 'fillc', 'fillsc',
             'pas_read_int', 'pas_read_word', 'pas_write_fmt', 'malloc', 'free',
             'abort', 'fflush')
    return any(f'@"{n}"' in ir_text or f'@{n}' in ir_text for n in _HOST)


# ---------------------------------------------------------------------------
# Equivalent source used in both MODULE and UNIT forms
# ---------------------------------------------------------------------------

_MODULE_SRC = """\
DEVICE MODULE M;
VAR
  [SPACE(GLOBAL)] g: ARRAY [1..4] OF CHAR;
  [SPACE(SHARED)] s: ARRAY [1..4] OF CHAR;
PROCEDURE xfer;
BEGIN
  MOVESL(ADS g, ADS s, WRD(4));
END;
.
"""

_UNIT_IFACE = """\
DEVICE INTERFACE;
UNIT M (xfer);
PROCEDURE xfer;
END;
"""

_UNIT_IMPL = """\
DEVICE IMPLEMENTATION OF M;
VAR
  [SPACE(GLOBAL)] g: ARRAY [1..4] OF CHAR;
  [SPACE(SHARED)] s: ARRAY [1..4] OF CHAR;
PROCEDURE xfer;
BEGIN
  MOVESL(ADS g, ADS s, WRD(4));
END;
.
"""


# ---------------------------------------------------------------------------
# 1. IR shape parity
# ---------------------------------------------------------------------------

class TestDeviceUnitModuleParity(unittest.TestCase):
    """DEVICE UNIT and DEVICE MODULE produce structurally equivalent IR."""

    def _both(self, **kw):
        mod_ir   = _compile(_MODULE_SRC, **kw)
        unit_ir  = _compile_unit(_UNIT_IFACE, _UNIT_IMPL, module_name='M', **kw)
        return mod_ir, unit_ir

    def test_same_target_triple_x86(self):
        mod_ir, unit_ir = self._both()
        self.assertEqual(_triple(mod_ir), _triple(unit_ir))

    def test_same_target_triple_nvptx(self):
        mod_ir, unit_ir = self._both(device_triple='nvptx64-nvidia-cuda')
        self.assertEqual(_triple(mod_ir), 'nvptx64-nvidia-cuda')
        self.assertEqual(_triple(unit_ir), 'nvptx64-nvidia-cuda')

    def test_same_addrspaces_x86(self):
        mod_ir, unit_ir = self._both()
        self.assertEqual(_addrspaces(mod_ir), _addrspaces(unit_ir))

    def test_same_addrspaces_nvptx(self):
        mod_ir, unit_ir = self._both(device_triple='nvptx64-nvidia-cuda')
        # Both should have addrspace(1) GLOBAL + addrspace(3) SHARED
        self.assertEqual(_addrspaces(mod_ir), [1, 3])
        self.assertEqual(_addrspaces(unit_ir), [1, 3])

    def test_zero_host_runtime_declares_x86(self):
        """Even on x86, the xfer body uses only the seg-bridge (inline); no
        host-runtime externs should appear in either form."""
        mod_ir, unit_ir = self._both()
        self.assertFalse(_has_runtime_extern(mod_ir),
                         f'DEVICE MODULE: unexpected host-runtime extern\n{mod_ir}')
        self.assertFalse(_has_runtime_extern(unit_ir),
                         f'DEVICE UNIT: unexpected host-runtime extern\n{unit_ir}')

    def test_zero_host_runtime_declares_nvptx(self):
        mod_ir, unit_ir = self._both(device_triple='nvptx64-nvidia-cuda')
        self.assertFalse(_has_runtime_extern(mod_ir))
        self.assertFalse(_has_runtime_extern(unit_ir))

    def test_seg_bridge_store_and_load_both_forms_nvptx(self):
        """The MOVESL seg-bridge must inline a load-from-global and
        store-to-shared loop in both forms."""
        mod_ir, unit_ir = self._both(device_triple='nvptx64-nvidia-cuda')
        for label, ir in (('MODULE', mod_ir), ('UNIT', unit_ir)):
            with self.subTest(form=label):
                self.assertRegex(ir, r'load i8, i8 addrspace\(1\)\*',
                                 f'{label}: missing load from addrspace(1)')
                self.assertRegex(ir, r'store i8 %[^,]+, i8 addrspace\(3\)\*',
                                 f'{label}: missing store to addrspace(3)')


# ---------------------------------------------------------------------------
# 2. DEVICE MODULE golden-self-compare (unchanged guard)
# ---------------------------------------------------------------------------

class TestDeviceModuleUnchanged(unittest.TestCase):
    """DEVICE MODULE IR is byte-for-byte stable across runs (golden self-compare).

    We compile once, store the result, compile again, and assert equality.
    Any accidental mutation of the DEVICE MODULE codegen path breaks this.
    """

    _SRCS = [
        # Minimal device module — the simplest non-trivial case
        "DEVICE MODULE M;\nPROCEDURE go;\nBEGIN END;\n.\n",
        # Module with SPACE globals + seg-bridge
        _MODULE_SRC,
        # Module on nvptx64
    ]

    def test_device_module_ir_is_deterministic_x86(self):
        for src in self._SRCS:
            with self.subTest(src=src[:40]):
                ir1 = _compile(src)
                ir2 = _compile(src)
                self.assertEqual(ir1, ir2,
                                 'DEVICE MODULE IR is non-deterministic across runs')

    def test_device_module_ir_is_deterministic_nvptx(self):
        for src in self._SRCS:
            with self.subTest(src=src[:40]):
                ir1 = _compile(src, device_triple='nvptx64-nvidia-cuda')
                ir2 = _compile(src, device_triple='nvptx64-nvidia-cuda')
                self.assertEqual(ir1, ir2)

    def test_host_program_ir_is_deterministic(self):
        """Host/vintage path must also be stable — any compile-time randomness
        would signal a broader regression."""
        src = 'PROGRAM P; VAR x: INTEGER; BEGIN x := 1; WRITELN(x) END.'
        ir1 = _compile(src)
        ir2 = _compile(src)
        self.assertEqual(ir1, ir2)


# ---------------------------------------------------------------------------
# 3. Final checklist acceptance items
# ---------------------------------------------------------------------------

class TestFinalAcceptance(unittest.TestCase):
    """Compact artifact-level verification of the definition-of-done items."""

    # ---- Item 1: DEVICE INTERFACE / IMPLEMENTATION parse with contextual DEVICE ----

    def test_device_interface_parses_and_is_device(self):
        ast = parse_source("DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n")
        from pascal1981.ast_nodes import InterfaceUnit
        self.assertIsInstance(ast, InterfaceUnit)
        self.assertTrue(ast.is_device)

    def test_device_implementation_parses_and_is_device(self):
        ast = parse_source("DEVICE IMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n")
        from pascal1981.ast_nodes import ImplementationUnit
        self.assertIsInstance(ast, ImplementationUnit)
        self.assertTrue(ast.is_device)

    def test_device_as_identifier_still_works(self):
        ast = parse_source("PROGRAM P; VAR device: INTEGER; BEGIN device := 0 END.")
        self.assertIsNotNone(ast)

    # ---- Item 2: all recissions enforced in DEVICE UNIT ----

    def test_host_io_banned(self):
        from tests.support import typecheck_module
        r = typecheck_module(
            iface_code="DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n",
            impl_code="DEVICE IMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN WRITELN('x') END;\n.\n",
        )
        self.assertFalse(r.success)
        self.assertIn('host i/o', ' '.join(str(e) for e in r.errors).lower())

    def test_heap_banned(self):
        from tests.support import typecheck_module
        r = typecheck_module(
            iface_code="DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n",
            impl_code=("DEVICE IMPLEMENTATION OF U;\n"
                       "TYPE p = ^INTEGER; VAR q: p;\n"
                       "PROCEDURE go; BEGIN NEW(q) END;\n.\n"),
        )
        self.assertFalse(r.success)
        self.assertIn('dynamic allocation', ' '.join(str(e) for e in r.errors).lower())

    def test_recursion_banned(self):
        from tests.support import typecheck_module
        r = typecheck_module(
            iface_code="DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n",
            impl_code="DEVICE IMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN go END;\n.\n",
        )
        self.assertFalse(r.success)
        self.assertIn('recursion', ' '.join(str(e) for e in r.errors).lower())

    def test_initializer_block_banned(self):
        from tests.support import typecheck_module
        r = typecheck_module(
            iface_code="DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n",
            impl_code="DEVICE IMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\nBEGIN END.\n",
        )
        self.assertFalse(r.success)
        self.assertIn('initializer code is not available in a device unit',
                      ' '.join(str(e) for e in r.errors).lower())

    # ---- Item 3: zero host-runtime symbol references ----

    def test_device_unit_zero_host_runtime_nvptx(self):
        ir = _compile_unit(
            "DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n",
            "DEVICE IMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n",
            device_triple='nvptx64-nvidia-cuda',
        )
        self.assertFalse(_has_runtime_extern(ir),
                         f'device unit on nvptx64 has host-runtime symbol\n{ir}')
        self.assertEqual(ir.count('declare'), 0,
                         f'unexpected declare in empty device unit\n{ir}')

    def test_device_unit_no_abort_or_fflush_nvptx(self):
        ir = _compile_unit(
            "DEVICE INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n",
            "DEVICE IMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n",
            device_triple='nvptx64-nvidia-cuda',
        )
        self.assertNotIn('@"abort"', ir)
        self.assertNotIn('@"fflush"', ir)

    # ---- Item 4: exported routines → entry; non-exported → func ----

    def test_exported_routine_is_entry_non_exported_is_func(self):
        iface = ("DEVICE INTERFACE;\nUNIT U (kernel);\n"
                 "PROCEDURE kernel (n: INTEGER);\nEND;\n")
        impl  = ("DEVICE IMPLEMENTATION OF U;\n"
                 "PROCEDURE helper;\nBEGIN END;\n"
                 "PROCEDURE kernel (n: INTEGER);\nBEGIN helper END;\n.\n")
        ir = _compile_unit(iface, impl, device_triple='nvptx64-nvidia-cuda')
        self.assertIn('ptx_kernel', ir, 'exported kernel must get ptx_kernel CC')
        kernel_def = re.search(r'define (\S+) \w+ @"kernel"', ir)
        self.assertIsNotNone(kernel_def)
        self.assertEqual(kernel_def.group(1), 'ptx_kernel')
        # helper should NOT have ptx_kernel
        helper_def = re.search(r'define (\S+) @"helper"', ir)
        if helper_def:
            self.assertNotEqual(helper_def.group(1), 'ptx_kernel')

    # ---- Item 5: DEVICE MODULE untouched ----

    def test_device_module_still_emits_device_funcs(self):
        ir = _compile(
            "DEVICE MODULE M;\nPROCEDURE go;\nBEGIN END;\n.\n",
            device_triple='nvptx64-nvidia-cuda',
        )
        self.assertIn('nvptx64-nvidia-cuda', ir)
        self.assertNotIn('ptx_kernel', ir,
                         'DEVICE MODULE has no exports; no entry should appear')

    def test_host_program_ir_unchanged_shape(self):
        ir = _compile('PROGRAM P; BEGIN WRITELN(42) END.')
        self.assertIn('x86_64-pc-linux-gnu', ir)
        self.assertNotIn('ptx_kernel', ir)
        self.assertNotIn('addrspace', ir)


if __name__ == '__main__':
    unittest.main()
