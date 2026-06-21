"""The FILLSC/MOVESL/MOVESR cross-space
bridge inside a DEVICE MODULE (design S5.4).

Inside a DEVICE MODULE these three segmented builtins become genuine
cross-space data movement: their two ADSMEM operands may name *different*
concrete spaces, and they lower to an addrspace-aware byte loop (load from the
source space, store to the destination space) instead of the vintage extern
call with the {ptr, i16} segmented ABI.

  * GPU device triple  -> loads/stores carry addrspace(k) (emitted, not run here).
  * CPU device (x86)   -> spaces collapse to addrspace 0: a real, runnable copy.
  * Host MODULE        -> byte-identical to before (extern movesl/movesr/fillsc).
"""
import os
import re
import subprocess
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.type_checker import PascalTypeChecker
from tests.support import CAN_BUILD_EXE, parse_source


def _check(src):
    return PascalTypeChecker().check(parse_source(src))


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


# A device module that stages GLOBAL -> SHARED then fills GLOBAL.
_BRIDGE_SRC = (
    "DEVICE MODULE M;\n"
    "VAR\n"
    "  [SPACE(GLOBAL)] g: CHAR;\n"
    "  [SPACE(SHARED)] s: CHAR;\n"
    "PROCEDURE go;\n"
    "BEGIN\n"
    "  MOVESL(ADS g, ADS s, WRD(1));\n"
    "  FILLSC(ADS g, WRD(1), 'Z');\n"
    "END;\n"
    ".\n"
)


class TestSegBridgeTypeChecking(unittest.TestCase):
    def test_cross_space_movesl_accepted_in_device_module(self):
        r = _check(_BRIDGE_SRC)
        self.assertTrue(r.success, r.errors)

    def test_movesr_cross_space_accepted(self):
        src = ("DEVICE MODULE M;\n"
               "VAR [SPACE(GLOBAL)] g: CHAR; [SPACE(LOCAL)] l: CHAR;\n"
               "PROCEDURE go; BEGIN MOVESR(ADS g, ADS l, WRD(1)); END; .\n")
        self.assertTrue(_check(src).success, _check(src).errors)

    def test_seg_bridge_rejected_outside_device_module(self):
        # ADS(s) is only legal inside a DEVICE MODULE, so the spaces cannot even
        # be spelled in a host module -- the bridge has nothing to cross.
        src = ("MODULE M;\n"
               "VAR [SPACE(GLOBAL)] g: CHAR;\n"
               "PROCEDURE go; BEGIN FILLSC(ADS g, WRD(1), 'Z'); END; .\n")
        self.assertFalse(_check(src).success)


class TestSegBridgeGpuLowering(unittest.TestCase):
    def test_movesl_loads_src_space_stores_dst_space(self):
        ir = _compile(_BRIDGE_SRC, device_triple='nvptx64-nvidia-cuda')
        body = ir.split('@"go"', 1)[1]
        # GLOBAL=addrspace(1) load, SHARED=addrspace(3) store (design S3.2).
        self.assertRegex(body, r'load i8, i8 addrspace\(1\)\*')
        self.assertRegex(body, r'store i8 %[^,]+, i8 addrspace\(3\)\*')
        # FILLSC writes the constant 'Z' (=90) into the SHARED/GLOBAL space.
        self.assertRegex(body, r'store i8 90, i8 addrspace\(1\)\*')
        # No vintage extern call / segmented pair in device code.
        self.assertNotIn('call i32 @"movesl"', body)
        self.assertNotIn('{ptr, i16}', body)

    def test_movesr_is_a_backward_loop(self):
        src = ("DEVICE MODULE M;\n"
               "VAR [SPACE(GLOBAL)] g: CHAR; [SPACE(SHARED)] s: CHAR;\n"
               "PROCEDURE go; BEGIN MOVESR(ADS g, ADS s, WRD(4)); END; .\n")
        ir = _compile(src, device_triple='nvptx64-nvidia-cuda')
        body = ir.split('@"go"', 1)[1]
        # backward: index runs against (len-1 - i), so a subtraction feeds the GEP.
        self.assertRegex(body, r'sub i64')


class TestSegBridgeCpuDevice(unittest.TestCase):
    def test_x86_device_collapses_to_addrspace_zero(self):
        ir = _compile(_BRIDGE_SRC)  # device triple defaults to x86
        self.assertEqual(re.findall(r'addrspace\((\d+)\)', ir), [])
        body = ir.split('@"go"', 1)[1]
        # Still a real load/store byte loop, just in addrspace 0.
        self.assertIn('load i8', body)
        self.assertIn('store i8', body)

    @unittest.skipUnless(CAN_BUILD_EXE, "requires llvmlite + clang")
    def test_cpu_device_bridge_runs(self):
        ir = _compile(_BRIDGE_SRC)
        tmp = tempfile.mkdtemp()
        try:
            ll = os.path.join(tmp, "dev.ll")
            c = os.path.join(tmp, "harness.c")
            exe = os.path.join(tmp, "run")
            with open(ll, "w") as f:
                f.write(ir)
            with open(c, "w") as f:
                f.write("extern char g, s;\nvoid go(void);\n"
                        "int main(void){g='A';s='?';go();"
                        "return (g=='Z'&&s=='A')?0:1;}\n")
            r = subprocess.run(["clang", ll, c, "-o", exe],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            run = subprocess.run([exe])
            # MOVESL copies g('A')->s, FILLSC sets g='Z'  =>  g=Z, s=A.
            self.assertEqual(run.returncode, 0)
        finally:
            import shutil
            shutil.rmtree(tmp)


class TestHostBridgeUnchanged(unittest.TestCase):
    def test_host_movesl_still_extern_call(self):
        src = ("PROGRAM P; VAR buf: ARRAY[1..8] OF CHAR;\n"
               "PROCEDURE movesl (src, dst: ADSMEM; len: WORD); extern;\n"
               "BEGIN MOVESL(ADS buf, ADS buf, WRD(4)) END.\n")
        ir = _compile(src)
        self.assertIn('movesl', ir.lower())
        self.assertEqual(re.findall(r'addrspace\((\d+)\)', ir), [])


if __name__ == '__main__':
    unittest.main()
