"""Milestone D — the PTX module + by-name launch dispatch (§5.3/§5.4).

LAUNCH now resolves a kernel the way the CUDA driver API does, in three steps:

    module = pas_dev_module_load(registry, ptx)        # cuModuleLoadData
    entry  = pas_dev_module_get_function(module, name)  # cuModuleGetFunction
    pas_dev_launch(entry, gx,gy,gz, bx,by,bz, argv)     # cuLaunchKernel

The host compiland emits a per-compiland kernel *registry* (a name table and a
parallel dispatch-thunk table) as the CPU-device stand-in for a loaded module,
and embeds the companion device unit's PTX as the ``__pas_device_ptx`` blob so
the launch path is self-contained.  On the CPU device the registry is the
"module"; swapping the shim for the CUDA driver path reuses these call sites
unchanged.

These tests pin:
  1. the three-step lowering and the registry/PTX globals (IR level);
  2. PTX embedding via ``--embed-device-ptx`` (the blob carries the bytes; an
     empty blob is emitted when none is supplied — the mechanism is always
     present);
  3. by-name dispatch actually selecting the correct kernel — a two-kernel
     program whose results would be wrong if get_function ignored the name.
"""

import os
import unittest

from tests.support import (build_and_run_pascal_project, parse_source,
                           requires_exe, requires_llvm, temporary_pascal_project)

from pascal1981.codegen import compile_to_llvm
from pascal1981.features import resolve_features
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker

_WIDE = resolve_features(overrides=['wide-integers'])

_IFACE = """\
DEVICE INTERFACE;
UNIT vadd (add);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""

_IMPL = """\
(*$INCLUDE:'vadd.inc'*)
DEVICE IMPLEMENTATION OF vadd;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i, stride: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < n DO BEGIN c^[i] := a^[i] + b^[i]; i := i + stride END
END;
.
"""

_MAIN = """\
(*$INCLUDE:'vadd.inc'*)
PROGRAM main(output);
USES vadd (add);
CONST n = 4;
VAR ha, hb, hc: ARRAY [0..3] OF INTEGER32;
    da, db, dc: ADRMEM; i, bytes: INTEGER;
BEGIN
  bytes := n * 4;
  FOR i := 0 TO n - 1 DO BEGIN ha[i] := i; hb[i] := i + i; hc[i] := 0 END;
  da := DEVALLOC(bytes); db := DEVALLOC(bytes); dc := DEVALLOC(bytes);
  DEVCOPYTO(da, ADR ha, bytes); DEVCOPYTO(db, ADR hb, bytes);
  LAUNCH(add, 1, n, da, db, dc, n);
  DEVCOPYFROM(ADR hc, dc, bytes);
  FOR i := 0 TO n - 1 DO WRITELN(hc[i]);
  DEVFREE(da); DEVFREE(db); DEVFREE(dc)
END.
"""


def _compile_main_ir(proj_files, *, embed_ptx=None, device_backend='cpu'):
    """Compile main.pas of a project to host IR, optionally embedding PTX."""
    with temporary_pascal_project(proj_files) as proj:
        main_path = os.path.join(proj, 'main.pas')
        ast = parse_file(main_path)
        result = PascalTypeChecker(source_file=main_path, features=_WIDE).check(ast)
        assert result.success, result.errors
        return compile_to_llvm(ast, source_file=main_path, features=_WIDE,
                               embed_device_ptx_text=embed_ptx,
                               device_backend=device_backend)


@requires_llvm
class TestByNameLaunchLowering(unittest.TestCase):

    def test_three_step_driver_shaped_lowering(self):
        ir = _compile_main_ir({'vadd.inc': _IFACE, 'main.pas': _MAIN})
        # The driver-API-shaped triple is present, in order...
        self.assertIn('pas_dev_module_load', ir)
        self.assertIn('pas_dev_module_get_function', ir)
        self.assertIn('pas_dev_launch', ir)
        # ...and the kernel is resolved by name out of a compiler-emitted
        # registry whose name table carries "add".
        self.assertIn('__pas_klaunch_registry', ir)
        self.assertIn('add\\00', ir)
        # The dispatch thunk is what the registry's entry table points at.
        self.assertIn('__pas_klaunch_add', ir)


@requires_llvm
class TestDevicePtxEmbedding(unittest.TestCase):

    def test_supplied_ptx_is_embedded(self):
        ptx = '.visible .entry add() { ret; }\n'
        ir = _compile_main_ir({'vadd.inc': _IFACE, 'main.pas': _MAIN}, embed_ptx=ptx)
        self.assertIn('__pas_device_ptx', ir)
        # The PTX bytes ride in the blob (a distinctive fragment is enough).
        self.assertIn('visible .entry add', ir)

    def test_empty_blob_emitted_without_ptx(self):
        ir = _compile_main_ir({'vadd.inc': _IFACE, 'main.pas': _MAIN})
        # The embedding mechanism is always present, even with no PTX supplied:
        # an empty (single-NUL) blob, so the launch path stays self-contained.
        self.assertIn('__pas_device_ptx', ir)
        self.assertIn('@"__pas_device_ptx" = constant [1 x i8]', ir)


@requires_llvm
class TestCudaBackendDecoupling(unittest.TestCase):
    """--device-backend cuda removes the CPU stand-in machinery entirely.

    On the CUDA backend the kernel is the loaded PTX module and the shim
    dispatches it by name, so the host must NOT emit the per-kernel dispatch
    thunk, the registry, or any reference to the kernel symbol -- those were the
    only reason the device unit had to be compiled a second time (dev.ll) and
    linked into the host.  The PTX blob is referenced as an external symbol
    (its own object at link time), so host compile no longer depends on the
    device artifact.
    """

    def test_no_thunk_no_registry_no_kernel_ref(self):
        ir = _compile_main_ir({'vadd.inc': _IFACE, 'main.pas': _MAIN},
                              device_backend='cuda')
        # The three-step driver path is still emitted...
        self.assertIn('pas_dev_module_load', ir)
        self.assertIn('pas_dev_module_get_function', ir)
        self.assertIn('pas_dev_launch', ir)
        # ...but with none of the CPU stand-in scaffolding.
        self.assertNotIn('__pas_klaunch', ir)         # no thunk, no registry
        self.assertNotIn('define i32 @"add"', ir)     # no kernel definition
        # The kernel symbol is never *referenced* (an unused extern declare is
        # harmless; a call/thunk would force the dead dev.ll link).
        self.assertNotIn('call void @"add"', ir)

    def test_ptx_blob_is_external_not_embedded(self):
        ir = _compile_main_ir({'vadd.inc': _IFACE, 'main.pas': _MAIN},
                              device_backend='cuda')
        # Host references the blob as an external symbol; the bytes live in a
        # separate object built from the .ptx at link time.
        self.assertIn('@"__pas_device_ptx" = external constant', ir)

    def test_explicit_embed_still_wins_on_cuda_backend(self):
        # Legacy opt-in: --embed-device-ptx still bakes the bytes in even on the
        # cuda backend (two-input link), so the old path keeps working.
        ptx = '.visible .entry add() { ret; }\n'
        ir = _compile_main_ir({'vadd.inc': _IFACE, 'main.pas': _MAIN},
                              embed_ptx=ptx, device_backend='cuda')
        self.assertNotIn('external constant', ir.split('__pas_device_ptx')[1][:40])
        self.assertIn('visible .entry add', ir)


@requires_llvm
class TestRegistryDedup(unittest.TestCase):

    def test_repeated_launch_of_same_kernel_is_one_entry(self):
        main_twice = _MAIN.replace(
            'LAUNCH(add, 1, n, da, db, dc, n);',
            'LAUNCH(add, 1, n, da, db, dc, n);\n  LAUNCH(add, 1, n, da, db, dc, n);')
        ir = _compile_main_ir({'vadd.inc': _IFACE, 'main.pas': main_twice})
        # One kernel launched (twice) -> a single registry entry (count 1).
        self.assertIn('i64 1}', ir)
        self.assertEqual(ir.count('@"__pas_klaunch_add"(i8**'), 1)  # one thunk defined


# ---- by-name dispatch must pick the right kernel ---------------------------

_IFACE2 = """\
DEVICE INTERFACE;
UNIT vops (add, mul);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
PROCEDURE mul(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""

_IMPL2 = """\
(*$INCLUDE:'vops.inc'*)
DEVICE IMPLEMENTATION OF vops;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i, stride: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < n DO BEGIN c^[i] := a^[i] + b^[i]; i := i + stride END
END;
PROCEDURE mul(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i, stride: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < n DO BEGIN c^[i] := a^[i] * b^[i]; i := i + stride END
END;
.
"""

_MAIN2 = """\
(*$INCLUDE:'vops.inc'*)
PROGRAM main(output);
USES vops (add, mul);
CONST n = 4;
VAR ha, hb, hsum, hprod: ARRAY [0..3] OF INTEGER32;
    da, db, dsum, dprod: ADRMEM; i, bytes: INTEGER;
BEGIN
  bytes := n * 4;
  FOR i := 0 TO n - 1 DO BEGIN ha[i] := i; hb[i] := i + 1 END;
  da := DEVALLOC(bytes); db := DEVALLOC(bytes);
  dsum := DEVALLOC(bytes); dprod := DEVALLOC(bytes);
  DEVCOPYTO(da, ADR ha, bytes); DEVCOPYTO(db, ADR hb, bytes);
  LAUNCH(add, 1, n, da, db, dsum, n);
  LAUNCH(mul, 1, n, da, db, dprod, n);
  DEVCOPYFROM(ADR hsum, dsum, bytes);
  DEVCOPYFROM(ADR hprod, dprod, bytes);
  FOR i := 0 TO n - 1 DO WRITELN(hsum[i]);
  FOR i := 0 TO n - 1 DO WRITELN(hprod[i]);
  DEVFREE(da); DEVFREE(db); DEVFREE(dsum); DEVFREE(dprod)
END.
"""


@requires_exe
class TestByNameDispatchSelectsCorrectKernel(unittest.TestCase):

    def test_two_kernels_resolved_by_name(self):
        rc, out, err = build_and_run_pascal_project(
            files={'vops.inc': _IFACE2, 'vops.pas': _IMPL2, 'main.pas': _MAIN2},
            compile_pairs=[
                ('vops.inc', 'vops-iface.ll'),
                ('vops.pas', 'vops.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['vops.ll', 'main.ll'],
            exe_name='vops-byname',
            features=_WIDE,
        )
        self.assertEqual(rc, 0, msg=err)
        # add: a+b = i+(i+1) = 1,3,5,7   mul: a*b = i*(i+1) = 0,2,6,12
        # If get_function ignored the name and returned entries[0] for both,
        # the second block would be 1,3,5,7 too -- so this pins by-name dispatch.
        self.assertEqual(out.split(), ['1', '3', '5', '7', '0', '2', '6', '12'])


if __name__ == '__main__':
    unittest.main()
