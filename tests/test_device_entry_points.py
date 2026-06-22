"""Exported device routines lower to launchable entry points.

In a DEVICE UNIT the interface's export list *is* the set of launchable
kernels: an exported routine gets the GPU kernel calling convention
(`ptx_kernel` / `amdgpu_kernel`), which PTX renders as a `.visible .entry`;
non-exported implementation routines stay device-internal `.func`s.  The
entry-shape rules bite only where a real GPU entry is formed — at
codegen on a GPU triple — so the x86 CPU-device parity ports are unaffected.

Assertions are at the artifact level: the emitted IR's calling convention and,
where the NVPTX target is available, the emitted PTX's `.entry`/`.func`.
"""

import os
import re
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.codegen.base import CodegenError
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


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
        checker = PascalTypeChecker(source_file=impl_path)
        r = checker.check(ast)
        assert r.success, r.errors
        ir = compile_to_llvm(ast, source_file=impl_path, **kw)
        return ast, ir
    finally:
        shutil.rmtree(tmpdir)


def _emit_ptx(ir_text, triple='nvptx64-nvidia-cuda'):
    """Emit PTX for the given IR, or return None if the target is unavailable."""
    try:
        import llvmlite.binding as llvm
        try:
            llvm.initialize_all_targets()
            llvm.initialize_all_asmprinters()
        except Exception:
            pass
        tm = llvm.Target.from_triple(triple).create_target_machine()
        return tm.emit_assembly(llvm.parse_assembly(ir_text))
    except Exception:
        return None


# A device unit exporting one PROCEDURE; a second routine is NOT exported.
_IFACE = ("DEVICE INTERFACE;\n"
          "UNIT VADD (vecadd);\n"
          "PROCEDURE vecadd (n: INTEGER);\n"
          "END;\n")
_IMPL = ("(*$INCLUDE:'vadd'*)\n"
         "DEVICE IMPLEMENTATION OF VADD;\n"
         "VAR [SPACE(GLOBAL)] a: ARRAY [1..256] OF INTEGER;\n"
         "PROCEDURE helper (k: INTEGER);\n"
         "BEGIN a[k] := k; END;\n"
         "PROCEDURE vecadd (n: INTEGER);\n"
         "VAR i: INTEGER;\n"
         "BEGIN FOR i := 1 TO n DO helper(i); END;\n"
         ".\n")


def _has_cc(ir_text, name, cc):
    return re.search(rf'define {cc} \w+ @"{name}"', ir_text) is not None


def _defined_plain(ir_text, name):
    # `define <rettype> @"name"` with no calling convention between.
    return re.search(rf'define \w+ @"{name}"', ir_text) is not None


class TestEntryPointEmission(unittest.TestCase):

    def test_exported_proc_is_ptx_kernel_helper_is_func(self):
        _, ir = _compile_unit(_IFACE, _IMPL, module_name='VADD', device_triple='nvptx64-nvidia-cuda')
        self.assertTrue(_has_cc(ir, 'vecadd', 'ptx_kernel'), ir)
        # helper has no kernel convention -> stays a plain device function.
        self.assertTrue(_defined_plain(ir, 'helper'), ir)
        self.assertNotIn('ptx_kernel', ir.split('@"helper"')[0].rsplit('define', 1)[-1])

    def test_amdgpu_kernel_convention(self):
        _, ir = _compile_unit(_IFACE, _IMPL, module_name='VADD', device_triple='amdgcn-amd-amdhsa')
        self.assertTrue(_has_cc(ir, 'vecadd', 'amdgpu_kernel'), ir)

    def test_ptx_has_visible_entry_for_export_and_func_for_helper(self):
        _, ir = _compile_unit(_IFACE, _IMPL, module_name='VADD', device_triple='nvptx64-nvidia-cuda')
        ptx = _emit_ptx(ir)
        if ptx is None:
            self.skipTest('NVPTX target not available in this llvmlite build')
        self.assertIn('.visible .entry vecadd', ptx)
        # helper appears as a .func, never an .entry.
        helper_lines = [l for l in ptx.splitlines() if 'helper' in l]
        self.assertTrue(helper_lines)
        self.assertTrue(all('.entry' not in l for l in helper_lines), helper_lines)

    def test_x86_device_is_inert_and_lowers(self):
        _, ir = _compile_unit(_IFACE, _IMPL, module_name='VADD')  # default x86 device triple
        self.assertNotIn('ptx_kernel', ir)
        self.assertNotIn('amdgpu_kernel', ir)
        # The body still lowers (serial CPU correctness): the loop/call is present.
        self.assertIn('vecadd', ir)
        self.assertIn('helper', ir)


class TestDeviceModuleHasNoEntries(unittest.TestCase):

    def test_device_module_emits_only_funcs(self):
        # A DEVICE MODULE has no interface, so nothing is exported and nothing
        # becomes an entry -- it keeps emitting plain device functions even on a
        # GPU triple (green gate: DEVICE MODULE behavior unchanged).
        src = ("DEVICE MODULE M;\n"
               "PROCEDURE go (n: INTEGER);\n"
               "VAR i: INTEGER;\n"
               "BEGIN FOR i := 1 TO n DO ; END;\n"
               ".\n")
        ast = parse_source(src)
        r = PascalTypeChecker().check(ast)
        assert r.success, r.errors
        ir = compile_to_llvm(ast, device_triple='nvptx64-nvidia-cuda')
        self.assertNotIn('ptx_kernel', ir)


class TestEntryShapeRules(unittest.TestCase):
    """Entry-shape rules bite on a GPU triple; inert (serial) on x86."""

    _FUNC_IFACE = "DEVICE INTERFACE;\nUNIT U (f);\nFUNCTION f: INTEGER;\nEND;\n"
    _FUNC_IMPL = "(*$INCLUDE:'u'*)\nDEVICE IMPLEMENTATION OF U;\nFUNCTION f: INTEGER;\nBEGIN f := 1; END;\n.\n"

    _VARPARAM_IFACE = "DEVICE INTERFACE;\nUNIT U (p);\nPROCEDURE p (VAR x: INTEGER);\nEND;\n"
    _VARPARAM_IMPL = "(*$INCLUDE:'u'*)\nDEVICE IMPLEMENTATION OF U;\nPROCEDURE p (VAR x: INTEGER);\nBEGIN x := 1; END;\n.\n"

    def test_exported_function_rejected_on_gpu(self):
        with self.assertRaises(CodegenError) as cm:
            _compile_unit(self._FUNC_IFACE, self._FUNC_IMPL, device_triple='nvptx64-nvidia-cuda')
        self.assertIn('PROCEDURE', str(cm.exception))

    def test_exported_function_allowed_on_x86(self):
        # Serial CPU-device: an exported FUNCTION is just an ordinary function.
        _, ir = _compile_unit(self._FUNC_IFACE, self._FUNC_IMPL)
        self.assertNotIn('ptx_kernel', ir)

    def test_host_pointer_param_rejected_on_gpu(self):
        with self.assertRaises(CodegenError) as cm:
            _compile_unit(self._VARPARAM_IFACE, self._VARPARAM_IMPL, device_triple='nvptx64-nvidia-cuda')
        self.assertIn('device-passable', str(cm.exception))

    def test_host_pointer_param_allowed_on_x86(self):
        _, ir = _compile_unit(self._VARPARAM_IFACE, self._VARPARAM_IMPL)
        self.assertNotIn('ptx_kernel', ir)

    def test_ads_global_param_is_passable_on_gpu(self):
        iface = "DEVICE INTERFACE;\nUNIT U (k);\nPROCEDURE k (p: ADS(GLOBAL) OF INTEGER);\nEND;\n"
        impl = "(*$INCLUDE:'u'*)\nDEVICE IMPLEMENTATION OF U;\nPROCEDURE k (p: ADS(GLOBAL) OF INTEGER);\nBEGIN END;\n.\n"
        _, ir = _compile_unit(iface, impl, device_triple='nvptx64-nvidia-cuda')
        self.assertTrue(_has_cc(ir, 'k', 'ptx_kernel'), ir)


class TestCheckerMarksExportsUnderSeparateCompilation(unittest.TestCase):

    def test_only_exported_routines_are_flagged(self):
        # Compile the implementation *alone* (interface only on disk): the
        # checker loads the interface and flags exports on the impl AST, which
        # is what codegen later reads.
        ast, _ = _compile_unit(_IFACE, _IMPL, module_name='VADD', device_triple='nvptx64-nvidia-cuda')
        flags = {d.name.lower(): d.is_exported_entry for d in ast.decls if hasattr(d, 'is_exported_entry')}
        self.assertTrue(flags.get('vecadd'))
        self.assertFalse(flags.get('helper'))


if __name__ == '__main__':
    unittest.main()
