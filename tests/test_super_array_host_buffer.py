"""The blessed host-buffer pattern: allocate with long-form NEW on a heap
SUPER ARRAY, pass the pointer outward, index it with wide integers.

This is the sanctioned way for host Pascal to own a large typed buffer that
crosses a foreign boundary (a ``[C]`` routine, or the DEVCOPYTO/DEVCOPYFROM
orchestration builtins) without a ``malloc`` extern and without losing typed
access to the elements:

    TYPE BUF = SUPER ARRAY [0..*] OF INTEGER32;
         PB  = ^BUF;
    VAR p: PB;
    ...
    NEW(p, n);            { i64 bound header + element data (bounds ABI)   }
    some_c_function(p);   { the pointer coerces to an ADRMEM/void* param   }
    x := p^[i];           { typed element access, wide (INTEGER32) index   }
    DISPOSE(p);

What this file pins, all gated on ``wide-integers`` (the vintage dialect is
untouched):

  * NEW's dynamic upper bound may be a wide (INTEGER32) expression or a
    literal beyond 32767 -- the bound header is an i64 either way.
  * Host code may index arrays with INTEGER32, so a buffer larger than the
    16-bit INTEGER range is fully addressable.
  * The super-array pointer itself is accepted where an ADRMEM parameter is
    expected (it lowers to the raw data pointer -- the bound header sits
    *before* the data, so C sees a plain element pointer).
  * The same pointer works as the host-side address in DEVCOPYTO/DEVCOPYFROM.
"""

import unittest

from pascal1981.features import extended_features, resolve_features
from tests.support import (build_and_run_pascal_project, requires_exe,
                           typecheck_source)

_WIDE = resolve_features(overrides=['wide-integers'])
EXT = extended_features()


class TestWideBufferTypecheck(unittest.TestCase):
    def test_wide_new_bound_and_wide_index_gated(self):
        src = ("PROGRAM P;\n"
               "TYPE BUF = SUPER ARRAY [0..*] OF INTEGER32; PB = ^BUF;\n"
               "VAR p: PB; i: INTEGER32;\n"
               "BEGIN NEW(p, 99999); FOR i := 0 TO 99999 DO p^[i] := i; DISPOSE(p) END.")
        self.assertTrue(typecheck_source(src, features=_WIDE).success)

    def test_vintage_dialect_still_rejects_wide_bound_literal(self):
        src = ("PROGRAM P;\n"
               "TYPE BUF = SUPER ARRAY [0..*] OF INTEGER; PB = ^BUF;\n"
               "VAR p: PB;\n"
               "BEGIN NEW(p, 99999); DISPOSE(p) END.")
        result = typecheck_source(src)
        self.assertFalse(result.success)


@requires_exe
class TestWideBufferBuildAndRun(unittest.TestCase):
    def test_large_heap_super_array_end_to_end(self):
        # > 32767 elements: allocation, wide-index writes/reads, UPPER.
        src = ("PROGRAM P(output);\n"
               "TYPE BUF = SUPER ARRAY [0..*] OF INTEGER32; PB = ^BUF;\n"
               "VAR p: PB; i: INTEGER32; s: INTEGER64;\n"
               "BEGIN\n"
               "  NEW(p, 99999);\n"
               "  FOR i := 0 TO 99999 DO p^[i] := i;\n"
               "  s := 0;\n"
               "  FOR i := 0 TO 99999 DO s := s + p^[i];\n"
               "  WRITELN(s, ' ', UPPER(p^));\n"
               "  DISPOSE(p)\n"
               "END.")
        rc, out, err = build_and_run_pascal_project(
            files={'p.pas': src}, compile_pairs=[('p.pas', 'p.ll')],
            link_ir_relpaths=['p.ll'], exe_name='big-super-array', features=_WIDE)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['4999950000', '99999'])

    def test_super_array_pointer_crosses_c_ffi_as_adrmem(self):
        # The pattern this exists for: hand the buffer to a C routine that
        # fills it, then read it back with typed element access.  WORD8
        # elements double as the byte-buffer case.
        src = ("PROGRAM P(output);\n"
               "TYPE BYTES = SUPER ARRAY [0..*] OF WORD8; PB = ^BYTES;\n"
               "PROCEDURE fill_bytes(p: ADRMEM; n: CINT) [C]; EXTERN;\n"
               "VAR p: PB; i: INTEGER32; s: INTEGER32;\n"
               "BEGIN\n"
               "  NEW(p, 9);\n"
               "  fill_bytes(p, 10);\n"
               "  s := 0;\n"
               "  FOR i := 0 TO 9 DO s := s + p^[i];\n"
               "  WRITELN(s, ' ', p^[9]);\n"
               "  DISPOSE(p)\n"
               "END.")
        c = ("#include <stdint.h>\n"
             "void fill_bytes(uint8_t *p, int32_t n){\n"
             "  for (int32_t i = 0; i < n; ++i) p[i] = (uint8_t)(i * 3);\n"
             "}\n")
        rc, out, err = build_and_run_pascal_project(
            files={'p.pas': src, 'c.c': c}, compile_pairs=[('p.pas', 'p.ll')],
            link_ir_relpaths=['p.ll', 'c.c'], exe_name='super-array-cffi',
            features=EXT)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['135', '27'])

    def test_super_array_pointer_as_devcopy_host_address(self):
        # The mandelbrot-host shape: the heap super array is the host-side
        # buffer for DEVCOPYTO/DEVCOPYFROM, replacing the malloc/ADRMEM idiom.
        iface = ("DEVICE INTERFACE;\n"
                 "UNIT dbl (dbl_all);\n"
                 "TYPE BUF = SUPER ARRAY [0..*] OF INTEGER32;\n"
                 "PROCEDURE dbl_all(a: ADS(GLOBAL) OF BUF; n: INTEGER32);\n"
                 "END;\n")
        impl = ("(*$INCLUDE:'dbl.inc'*)\n"
                "DEVICE IMPLEMENTATION OF dbl;\n"
                "PROCEDURE dbl_all(a: ADS(GLOBAL) OF BUF; n: INTEGER32);\n"
                "VAR i, stride: INTEGER32;\n"
                "BEGIN\n"
                "  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;\n"
                "  stride := BLOCKDIM_X * GRIDDIM_X;\n"
                "  WHILE i < n DO\n"
                "  BEGIN a^[i] := a^[i] + a^[i]; i := i + stride END\n"
                "END;\n"
                ".\n")
        main = ("(*$INCLUDE:'dbl.inc'*)\n"
                "PROGRAM main(output);\n"
                "USES dbl (dbl_all);\n"
                "CONST n = 8;\n"
                "TYPE HBUF = SUPER ARRAY [0..*] OF INTEGER32; PH = ^HBUF;\n"
                "VAR h: PH; d: ADRMEM; i: INTEGER32; bytes: INTEGER32;\n"
                "BEGIN\n"
                "  NEW(h, n - 1);\n"
                "  FOR i := 0 TO n - 1 DO h^[i] := i + 1;\n"
                "  bytes := n * 4;\n"
                "  d := DEVALLOC(bytes);\n"
                "  DEVCOPYTO(d, h, bytes);\n"
                "  LAUNCH(dbl_all, 1, n, d, n);\n"
                "  DEVCOPYFROM(h, d, bytes);\n"
                "  DEVFREE(d);\n"
                "  FOR i := 0 TO n - 1 DO WRITELN(h^[i]);\n"
                "  DISPOSE(h)\n"
                "END.")
        rc, out, err = build_and_run_pascal_project(
            files={'dbl.inc': iface, 'dbl.pas': impl, 'main.pas': main},
            compile_pairs=[('dbl.inc', 'dbl-iface.ll'), ('dbl.pas', 'dbl.ll'),
                           ('main.pas', 'main.ll')],
            link_ir_relpaths=['dbl.ll', 'main.ll'],
            exe_name='super-array-devcopy', features=_WIDE)
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['2', '4', '6', '8', '10', '12', '14', '16'])


if __name__ == '__main__':
    unittest.main()
