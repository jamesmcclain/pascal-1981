"""IF/ELSE-of-assignment -> `select` (PTX `selp`) peephole.

followups.md item 2 (branch vs predication on the bounds guard): a narrow
``IF c THEN x := a ELSE x := b`` on a scalar ``x`` with pure, non-faulting RHS
lowers to a branchless LLVM ``select`` so the NVPTX backend emits ``selp``
instead of a divergent ``bra``.  The peephole must bail to real control flow on
anything ambiguous.

These tests pin both the hit (selp emitted) and the bail-outs (real branches),
so a future widening of the pattern is forced to preserve the conservative
boundary.
"""

import os
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import requires_llvm


def _compile_impl(impl_src: str, iface_src: str = None, *, device_triple='nvptx64-nvidia-cuda') -> str:
    tmpdir = tempfile.mkdtemp()
    try:
        iface_path = os.path.join(tmpdir, 'sel')
        impl_path = os.path.join(tmpdir, 'sel.pas')
        with open(iface_path, 'w') as f:
            f.write(iface_src if iface_src is not None else _HIT_IFACE)
        with open(impl_path, 'w') as f:
            f.write(impl_src)
        ast = parse_file(impl_path)
        r = PascalTypeChecker(source_file=impl_path).check(ast)
        assert r.success, r.errors
        return compile_to_llvm(ast, source_file=impl_path, device_triple=device_triple)
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


# A kernel whose body is exactly the bounds-guard pattern: scalar target, pure
# arithmetic RHS, no function calls, no division, no indexing. The result is
# stored through an output pointer so the backend cannot DCE the select.
_HIT_IFACE = ("DEVICE INTERFACE;\n"
              "UNIT SEL (guard);\n"
              "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
              "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
              "END;\n")
_HIT_IMPL = ("(*$INCLUDE:'sel'*)\n"
             "DEVICE IMPLEMENTATION OF SEL;\n"
             "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
             "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
             "VAR w: REAL32;\n"
             "BEGIN\n"
             "  IF n > 1 THEN w := n - 1 ELSE w := 1;\n"
             "  output^[0] := w;\n"
             "END;\n"
             ".\n")


@requires_llvm
class TestIfElseSelectPeephole(unittest.TestCase):

    def test_hit_lowers_to_select_selp(self):
        ir = _compile_impl(_HIT_IMPL)
        # IR-level: a `select` instruction is emitted, not a three-block diamond.
        self.assertRegex(ir, r'select\s+i1')
        ptx = _emit_ptx(ir)
        self.assertIn('selp', ptx)
        # The guard must not have introduced a branch for the IF/ELSE.
        # (No `bra` targeted at an if_then/if_else block label for this construct;
        # a void kernel with only the guard emits a straight-line body.)
        self.assertNotIn('bra ', ptx, ptx)

    def test_mismatched_targets_fall_back_to_branches(self):
        # THEN assigns `w`, ELSE assigns `v`: different targets, so no select.
        impl = ("(*$INCLUDE:'sel'*)\n"
                "DEVICE IMPLEMENTATION OF SEL;\n"
                "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
                "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
                "VAR w, v: REAL32;\n"
                "BEGIN\n"
                "  IF n > 1 THEN w := n - 1 ELSE v := 1;\n"
                "  output^[0] := w;\n"
                "END;\n"
                ".\n")
        ir = _compile_impl(impl)
        self.assertNotRegex(ir, r'select\s+i1')

    def test_multi_statement_branch_falls_back(self):
        # THEN has two statements: not a single assignment, so bail out.
        impl = ("(*$INCLUDE:'sel'*)\n"
                "DEVICE IMPLEMENTATION OF SEL;\n"
                "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
                "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
                "VAR w: REAL32;\n"
                "BEGIN\n"
                "  IF n > 1 THEN BEGIN w := n - 1; w := w + 1 END ELSE w := 1;\n"
                "  output^[0] := w;\n"
                "END;\n"
                ".\n")
        ir = _compile_impl(impl)
        self.assertNotRegex(ir, r'select\s+i1')

    def test_division_in_rhs_falls_back(self):
        # DIV could trap on the not-taken arm; must not speculatively evaluate.
        impl = ("(*$INCLUDE:'sel'*)\n"
                "DEVICE IMPLEMENTATION OF SEL;\n"
                "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
                "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
                "VAR w: REAL32;\n"
                "BEGIN\n"
                "  IF n > 1 THEN w := n DIV 2 ELSE w := 1;\n"
                "  output^[0] := w;\n"
                "END;\n"
                ".\n")
        ir = _compile_impl(impl)
        self.assertNotRegex(ir, r'select\s+i1')

    def test_call_in_rhs_falls_back(self):
        # A function call in the RHS has side effects; must stay branched.
        impl = ("(*$INCLUDE:'sel'*)\n"
                "DEVICE IMPLEMENTATION OF SEL;\n"
                "TYPE R32ARR = SUPER ARRAY [0..*] OF REAL32;\n"
                "PROCEDURE guard (output: ADS(GLOBAL) OF R32ARR; n: INTEGER);\n"
                "VAR w: REAL32;\n"
                "  FUNCTION side: REAL32;\n"
                "  BEGIN side := 0.5; END;\n"
                "BEGIN\n"
                "  IF n > 1 THEN w := n - 1 ELSE w := side;\n"
                "  output^[0] := w;\n"
                "END;\n"
                ".\n")
        ir = _compile_impl(impl)
        self.assertNotRegex(ir, r'select\s+i1')


if __name__ == '__main__':
    unittest.main()
