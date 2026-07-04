"""Tests for reading array elements through ADS pointer parameters (Bug #3 fix).

Prior to the fix, `val := inp^[i]` where `inp` is an ADS(GLOBAL) parameter
returned the raw pointer value instead of the element, because the Designator
codegen bailed out on `is_parameter` before walking the selector chain.
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

IFACE = """\
DEVICE INTERFACE;
UNIT {name} ({exports});
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
{decls}
END;
"""


def _compile_module(iface_src, impl_src, module_name='T'):
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
        return compile_to_llvm(ast, source_file=impl_path, device_triple=GPU_TRIPLE)
    finally:
        shutil.rmtree(tmpdir)


class TestADSArrayRead(unittest.TestCase):

    def test_read_from_global_array_param(self):
        """val := inp^[i] must emit a load from addrspace(1), not store a pointer."""
        iface = IFACE.format(name='T', exports='kernel_entry', decls='PROCEDURE kernel_entry(inp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);')
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
TYPE BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(inp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i, val: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    val := inp^[i]
END;
.
"""
        ir = _compile_module(iface, impl)
        ir_str = str(ir)
        # Must contain a load from addrspace(1)
        self.assertIn('addrspace(1)', ir_str)
        self.assertRegex(ir_str, r'load i32, i32 addrspace\(1\)\*')

    def test_vector_add_reads_two_inputs(self):
        """c^[i] := a^[i] + b^[i]: two reads from addrspace(1), one write."""
        iface = IFACE.format(name='T', exports='vadd', decls='PROCEDURE vadd(a, b, c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);')
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
TYPE BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE vadd(a, b, c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    c^[i] := a^[i] + b^[i]
END;
.
"""
        ir = _compile_module(iface, impl)
        ir_str = str(ir)
        # Two loads from addrspace(1) for the reads
        loads = re.findall(r'load i32, i32 addrspace\(1\)\*', ir_str)
        self.assertGreaterEqual(len(loads), 2, "expected at least two addrspace(1) loads")
        # One store to addrspace(1) for the write
        stores = re.findall(r'store i32 .*, i32 addrspace\(1\)\*', ir_str)
        self.assertGreaterEqual(len(stores), 1, "expected at least one addrspace(1) store")

    def test_read_in_grid_stride_loop(self):
        """Accumulate inp^[i] in a WHILE loop — reads across iterations."""
        iface = IFACE.format(name='T', exports='kernel_entry', decls='PROCEDURE kernel_entry(inp, outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);')
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
TYPE BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(inp, outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i, stride, acc: INTEGER32;
BEGIN
  stride := BLOCKDIM_X * GRIDDIM_X;
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  acc := 0;
  WHILE i < n DO
  BEGIN
    acc := acc + inp^[i];
    i := i + stride
  END;
  IF n > 0 THEN
    outp^[THREADIDX_X] := acc
END;
.
"""
        ir = _compile_module(iface, impl)
        ir_str = str(ir)
        self.assertIn('addrspace(1)', ir_str)
        self.assertRegex(ir_str, r'load i32, i32 addrspace\(1\)\*')

    def test_write_only_kernel_unaffected(self):
        """fill_indices style write-only kernel must still work after the fix."""
        iface = IFACE.format(name='T', exports='fill_indices', decls='PROCEDURE fill_indices(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);')
        impl = """\
(*$INCLUDE:'t'*)
DEVICE IMPLEMENTATION OF T;
TYPE BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    outp^[i] := i
END;
.
"""
        ir = _compile_module(iface, impl)
        ir_str = str(ir)
        stores = re.findall(r'store i32 .*, i32 addrspace\(1\)\*', ir_str)
        self.assertGreaterEqual(len(stores), 1)


if __name__ == '__main__':
    unittest.main()
