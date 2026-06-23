"""Device FMA contraction (followups.md item 2, FMA leg).

Device floating-point arithmetic carries the LLVM `contract` fast-math flag so
the NVPTX backend fuses `a*b + c` into a single `fma.rn`, matching `nvcc`'s
default `--fmad=true`. This is a deliberate **device-only** choice: the host
path must stay strict (flag-free) so host float results keep their IEEE shape
and the host IR stays byte-identical to the pre-change baseline.
"""

import os
import re
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import requires_llvm


_DEVICE_IFACE = ("DEVICE INTERFACE;\n"
                 "UNIT FMA (kern);\n"
                 "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
                 "PROCEDURE kern (out: ADS(GLOBAL) OF R32ARR; a: REAL32; b: REAL32; c: REAL32);\n"
                 "END;\n")

_DEVICE_IMPL = ("(*$INCLUDE:'fma'*)\n"
                "DEVICE IMPLEMENTATION OF FMA;\n"
                "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
                "PROCEDURE kern (out: ADS(GLOBAL) OF R32ARR; a: REAL32; b: REAL32; c: REAL32);\n"
                "BEGIN\n"
                "  out^[0] := 2 * a * b + c;\n"
                "END;\n"
                ".\n")

_HOST_PROGRAM = ("PROGRAM fmatest;\n"
                 "VAR a, b, c, r: REAL;\n"
                 "BEGIN\n"
                 "  a := 1.5; b := 2.5; c := 3.5;\n"
                 "  r := 2 * a * b + c;\n"
                 "END.\n")


def _compile_src(src: str, *, is_device: bool, device_triple='nvptx64-nvidia-cuda') -> str:
    tmpdir = tempfile.mkdtemp()
    try:
        if is_device:
            iface_path = os.path.join(tmpdir, 'fma')
            impl_path = os.path.join(tmpdir, 'fma.pas')
            with open(iface_path, 'w') as f:
                f.write(_DEVICE_IFACE)
            with open(impl_path, 'w') as f:
                f.write(src)
            path = impl_path
        else:
            path = os.path.join(tmpdir, 'host.pas')
            with open(path, 'w') as f:
                f.write(src)
        ast = parse_file(path)
        r = PascalTypeChecker(source_file=path).check(ast)
        assert r.success, r.errors
        return compile_to_llvm(ast, source_file=path, device_triple=device_triple)
    finally:
        shutil.rmtree(tmpdir)


def _emit_ptx(ir_text: str, cpu='sm_86'):
    import llvmlite.binding as llvm
    try:
        llvm.initialize_all_targets()
        llvm.initialize_all_asmprinters()
    except Exception:
        pass
    tm = llvm.Target.from_triple('nvptx64-nvidia-cuda').create_target_machine(cpu=cpu)
    return tm.emit_assembly(llvm.parse_assembly(ir_text))


@requires_llvm
class TestFmaContraction(unittest.TestCase):

    def test_device_fp_ops_carry_contract_flag(self):
        ir = _compile_src(_DEVICE_IMPL, is_device=True)
        # Every emitted device fp binop carries the `contract` flag.
        self.assertRegex(ir, r'fmul(\s|{).*contract', ir)
        self.assertRegex(ir, r'fadd(\s|{).*contract', ir)

    def test_device_emits_fma_rn(self):
        ir = _compile_src(_DEVICE_IMPL, is_device=True)
        ptx = _emit_ptx(ir)
        self.assertIn('fma.rn.f32', ptx, ptx)

    def test_host_fp_ops_are_strict_no_fast_math(self):
        # The contract flag must NOT leak onto the host path; host float results
        # keep their strict IEEE shape and the host IR stays byte-identical to
        # the pre-change baseline.
        ir = _compile_src(_HOST_PROGRAM, is_device=False)
        for line in ir.splitlines():
            if re.search(r'\b(fmul|fadd|fsub|fdiv|frem)\b', line):
                self.assertNotIn('contract', line, line)
                self.assertNotIn(' fast', line, line)
        # Confirm host fp ops are actually emitted (sanity).
        self.assertTrue(re.search(r'\b(fmul|fadd)\b', ir), ir)


if __name__ == '__main__':
    unittest.main()
