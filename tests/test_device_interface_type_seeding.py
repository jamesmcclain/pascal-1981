"""Tests for interface TYPE/CONST aliases being visible in the implementation.

Prior to the fix, type aliases declared in a DEVICE INTERFACE were not seeded
into the type checker or codegen scopes when processing the implementation,
forcing authors to restate every TYPE in both files (the mandelbrot workaround).
"""

import os
import re
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker

GPU_TRIPLE = 'nvptx64-nvidia-cuda'


def _compile_module(iface_src, impl_src, module_name='T', device_triple=GPU_TRIPLE):
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
        return compile_to_llvm(ast, source_file=impl_path, device_triple=device_triple)
    finally:
        shutil.rmtree(tmpdir)


def _typecheck_module(iface_src, impl_src, module_name='T'):
    tmpdir = tempfile.mkdtemp()
    try:
        iface_path = os.path.join(tmpdir, module_name.lower())
        impl_path = os.path.join(tmpdir, f'{module_name.lower()}.pas')
        with open(iface_path, 'w') as f:
            f.write(iface_src)
        with open(impl_path, 'w') as f:
            f.write(impl_src)
        ast = parse_file(impl_path)
        return PascalTypeChecker(source_file=impl_path).check(ast)
    finally:
        shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Core seeding tests
# ---------------------------------------------------------------------------


class TestInterfaceTypeSeeding(unittest.TestCase):

    def test_type_alias_from_interface_visible_in_impl(self):
        """Implementation references a TYPE defined only in the interface."""
        iface = """\
DEVICE INTERFACE;
UNIT T (kernel_entry);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN outp^[i] := i
END;
.
"""
        r = _typecheck_module(iface, impl)
        self.assertTrue(r.success, r.errors)

    def test_type_alias_from_interface_lowers_correctly(self):
        """Codegen resolves the interface alias to the correct LLVM type."""
        iface = """\
DEVICE INTERFACE;
UNIT T (kernel_entry);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN outp^[i] := i
END;
.
"""
        ir = _compile_module(iface, impl)
        ir_str = str(ir)
        self.assertIn('addrspace(1)', ir_str)
        self.assertIn('ptx_kernel', ir_str)

    def test_const_alias_from_interface_visible_in_impl(self):
        """Implementation uses a CONST defined only in the interface."""
        iface = """\
DEVICE INTERFACE;
UNIT T (kernel_entry);
CONST
  MAX_N = 1024;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF ARRAY [0..1023] OF INTEGER32);
END;
"""
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF ARRAY [0..1023] OF INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < MAX_N THEN outp^[i] := i
END;
.
"""
        r = _typecheck_module(iface, impl)
        self.assertTrue(r.success, r.errors)

    def test_impl_type_overrides_interface_type(self):
        """When impl and interface both declare the same TYPE, impl wins (no error)."""
        iface = """\
DEVICE INTERFACE;
UNIT T (kernel_entry);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN outp^[i] := i
END;
.
"""
        r = _typecheck_module(iface, impl)
        self.assertTrue(r.success, r.errors)

    def test_impl_private_type_still_resolves(self):
        """A type defined only in the implementation (not the interface) still works."""
        iface = """\
DEVICE INTERFACE;
UNIT T (kernel_entry);
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
END;
"""
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
TYPE
  SCRATCH = ARRAY [0..15] OF INTEGER32;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
VAR [SPACE(SHARED)] s: SCRATCH;
    i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  s[THREADIDX_X] := i;
  SYNCTHREADS;
  IF i < n THEN outp^[i] := s[THREADIDX_X]
END;
.
"""
        r = _typecheck_module(iface, impl)
        self.assertTrue(r.success, r.errors)


# ---------------------------------------------------------------------------
# Mandelbrot example: the workaround duplication is now gone
# ---------------------------------------------------------------------------


class TestMandelbrotNoDuplicateType(unittest.TestCase):

    def test_mandelbrot_compiles_without_duplicate_pixels_type(self):
        """mandelbrot.pas no longer needs TYPE PIXELS restated in the impl."""
        base = os.path.join(os.path.dirname(__file__), '..', 'examples', 'device_ptx', 'mandelbrot')
        impl_path = os.path.abspath(os.path.join(base, 'mandelbrot.pas'))
        if not os.path.exists(impl_path):
            self.skipTest('mandelbrot example not found')

        ast = parse_file(impl_path)
        r = PascalTypeChecker(source_file=impl_path).check(ast)
        self.assertTrue(r.success, r.errors)

        # Confirm the implementation no longer contains a TYPE section
        with open(impl_path) as f:
            src = f.read()
        # The TYPE keyword should only appear in a comment now, not as a decl
        import re
        type_decls = re.findall(r'(?m)^TYPE\b', src)
        self.assertEqual(len(type_decls), 0, "mandelbrot.pas should no longer restate TYPE PIXELS")

        ir = compile_to_llvm(ast, source_file=impl_path, device_triple=GPU_TRIPLE)
        ir_str = str(ir)
        self.assertIn('ptx_kernel', ir_str)
        self.assertIn('mandelbrot_f32', ir_str)
        self.assertIn('mandelbrot_f64', ir_str)


if __name__ == '__main__':
    unittest.main()
