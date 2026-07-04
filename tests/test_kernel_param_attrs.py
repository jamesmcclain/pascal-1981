"""Tests for kernel-entry parameter attributes: readonly/nocapture,
dereferenceable, and gated noalias (docs/followups.md item "Kernel entries
carry no parameter facts").

LLVM cannot infer any of these for a bare device pointer parameter -- they
are facts only Pascal semantics (this procedure's own body, for readonly) or
the LAUNCH contract (distinct buffers don't overlap, for noalias) can supply.
Alignment was already handled by a prior item; this covers the rest.

Layers covered: IR-shape assertions for readonly/nocapture/dereferenceable,
the write-through-body / passed-to-another-call / WITH-statement conservatism
of the readonly analysis, feature gating for noalias (default off even under
--dialect extended, on only via -f noalias-kernel-params), and a PTX
round-trip proving the emitted attributes are accepted by parse_assembly/
verify/emit_assembly.
"""

import os
import unittest

from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import requires_llvm, temporary_pascal_project

_IFACE = """DEVICE INTERFACE;
UNIT KH (scale, via_helper, uses_with);
TYPE BUF = ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32;
PROCEDURE scale(inp: BUF; outp: BUF; n: INTEGER32);
PROCEDURE via_helper(inp: BUF; outp: BUF; n: INTEGER32);
PROCEDURE helper(b: BUF; n: INTEGER32);
PROCEDURE uses_with(inp: BUF; outp: BUF; n: INTEGER32);
END;
"""

_IMPL = """(*$INCLUDE:'kh'*)
DEVICE IMPLEMENTATION OF KH;

PROCEDURE helper(b: BUF; n: INTEGER32);
BEGIN
END;

PROCEDURE scale(inp: BUF; outp: BUF; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    outp^[i] := inp^[i]
END;

PROCEDURE via_helper(inp: BUF; outp: BUF; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X;
  helper(inp, n)
END;

PROCEDURE uses_with(inp: BUF; outp: BUF; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X;
  outp^[i] := inp^[i]
END;
.
"""


def _compile_device_ir(*, features=None, device_triple='nvptx64-nvidia-cuda'):
    from pascal1981.codegen import compile_to_llvm
    with temporary_pascal_project({'kh': _IFACE, 'kh.pas': _IMPL}) as proj:
        path = os.path.join(proj, 'kh.pas')
        ast = parse_file(path)
        tc = PascalTypeChecker(source_file=path, features=features or {})
        result = tc.check(ast)
        assert result.success, [e.message for e in result.errors]
        return compile_to_llvm(ast, source_file=path, features=features or {}, device_triple=device_triple)


@requires_llvm
class TestReadonlyAnalysis(unittest.TestCase):

    def test_written_through_param_is_not_readonly(self):
        ir = _compile_device_ir()
        scale_def = ir[ir.index('define ptx_kernel void @"scale"'):]
        scale_sig = scale_def.split('\n', 1)[0]
        # outp (the written-through buffer) must not carry readonly; inp
        # (read-only in this body) must.
        self.assertIn('nocapture readonly align 4 dereferenceable(1024) %"inp"', scale_sig)
        self.assertNotIn('readonly', scale_sig.split('%"outp"')[0].rsplit('%"inp"', 1)[-1])

    def test_param_passed_to_another_call_is_conservatively_not_readonly(self):
        """`via_helper` never itself writes through `inp`, but passes it on
        to `helper` -- this compiler does not prove helper is pure, so `inp`
        must NOT be marked readonly here (conservative, not incorrect). `outp`
        is untouched in this body (never written, never passed on), so it
        legitimately does still get readonly -- that is the correct,
        expected result of the same analysis, not a bug."""
        ir = _compile_device_ir()
        via_def = ir[ir.index('define ptx_kernel void @"via_helper"'):]
        via_sig = via_def.split('\n', 1)[0]
        self.assertNotIn('nocapture readonly align 4 dereferenceable(1024) %"inp"', via_sig)
        self.assertIn('nocapture readonly align 4 dereferenceable(1024) %"outp"', via_sig)

    def test_uses_with_body_with_no_with_stmt_gets_readonly_normally(self):
        """Sanity check / control for the WITH-disqualification unit test
        below: this body has no WITH statement and never writes through
        either buffer, so ordinary readonly detection applies to both."""
        ir = _compile_device_ir()
        with_def = ir[ir.index('define ptx_kernel void @"uses_with"'):]
        with_sig = with_def.split('\n', 1)[0]
        self.assertIn('readonly', with_sig)

    def test_with_statement_disqualifies_the_whole_procedure(self):
        """A body containing a WITH statement is conservatively excluded from
        readonly consideration entirely -- WITH's field designators are not
        tied back to the originating pointer expression by the (purely
        syntactic) write-through walk, so a write inside a WITH block could
        go unnoticed. Exercised directly against the analysis on a hand-built
        AST fixture (a realistic parser round trip needs a record-typed ADS
        buffer, which is orthogonal to what this unit is checking)."""
        from pascal1981.ast_nodes import (AssignStmt, Block, Designator, IntLiteral, Param, ProcDecl, Selector, WithStmt)
        from pascal1981.codegen.decls import DeclsMixin

        class _Host(DeclsMixin):
            pass

        # PROCEDURE p(buf: BUF); BEGIN WITH buf^ DO field := 1 END;
        # (never explicitly writes through `buf` via a Designator the walk
        # recognizes, since the WITH-desugared target is just `field`.)
        with_stmt = WithStmt(targets=[Designator(name='buf', selectors=[Selector(kind='DEREF', index_or_field=None)])],
                             body=AssignStmt(target=Designator(name='field', selectors=[]), expr=IntLiteral(value=1)))
        body = Block(decls=[], body=[with_stmt])
        decl = ProcDecl(name='p', params=[Param(mode=None, names=['buf'], type_expr=None)], attributes=[], body=body)
        names = _Host()._kernel_readonly_param_names(decl)
        self.assertEqual(names, set())


@requires_llvm
class TestDereferenceable(unittest.TestCase):

    def test_fixed_array_pointee_gets_dereferenceable(self):
        ir = _compile_device_ir()
        self.assertIn('dereferenceable(1024)', ir)  # 256 * 4 bytes

    def test_cpu_device_triple_has_no_kernel_entry_attrs(self):
        """Inert on the x86 CPU-device parity path (no kernel entry at all)."""
        ir = _compile_device_ir(device_triple='x86_64-pc-linux-gnu')
        self.assertNotIn('dereferenceable', ir)
        self.assertNotIn('readonly', ir)
        self.assertNotIn('noalias', ir)


@requires_llvm
class TestNoaliasFeatureGating(unittest.TestCase):

    def test_noalias_absent_by_default_in_device_dialect(self):
        ir = _compile_device_ir(features={})
        self.assertNotIn('noalias', ir)

    def test_noalias_absent_under_extended_umbrella_without_explicit_flag(self):
        """noalias-kernel-params is a policy flag (in_extended=False): the
        extended umbrella alone must not turn it on."""
        from pascal1981.features import extended_features
        ir = _compile_device_ir(features=extended_features())
        self.assertNotIn('noalias', ir)

    def test_noalias_present_with_explicit_feature_flag(self):
        from pascal1981.features import resolve_features
        features = resolve_features('extended', ['noalias-kernel-params'])
        ir = _compile_device_ir(features=features)
        self.assertIn('noalias', ir)
        # Present on both buffer params (distinct-buffers-don't-overlap is a
        # per-parameter fact under this feature, not just the readonly one).
        scale_def = ir[ir.index('define ptx_kernel void @"scale"'):]
        scale_sig = scale_def.split('\n', 1)[0]
        self.assertEqual(scale_sig.count('noalias'), 2)


@requires_llvm
class TestPtxRoundTrip(unittest.TestCase):

    def test_attributes_survive_parse_assembly_verify_emit_assembly(self):
        from pascal1981.compile_to_ptx import llvm_ir_to_ptx
        from pascal1981.features import resolve_features
        features = resolve_features('extended', ['noalias-kernel-params'])
        ir = _compile_device_ir(features=features)
        ptx = llvm_ir_to_ptx(ir, cpu='sm_70')  # verify() is called inside; raises on rejection
        self.assertIn('.visible .entry scale', ptx)


if __name__ == '__main__':
    unittest.main()
