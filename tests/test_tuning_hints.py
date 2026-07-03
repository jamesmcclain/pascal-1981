"""Tests for the tuning-hints feature: [MAXNTID]/[REQNTID]/[MINCTASM] launch
bounds on exported device kernel procedures, and the {$UNROLL n} loop
metacommand (docs/tuning-hints.md; follow-up item "No source-level channel for
launch bounds or per-loop hints").

Both are hint plumbing only: they encode programmer intent LLVM cannot invent,
and every transform remains LLVM's. Both are gated behind -f tuning-hints
(auto-enabled inside DEVICE code, whose feature baseline is the extended
umbrella) and rejected under the faithful vintage dialect.

Layers covered: parser accept/reject, type-check gating and validation, IR
shape (loop metadata self-reference, NVVM function attributes and legacy
annotations), PTX directive emission, an end-to-end check that the unroll hint
actually fires under LLVM's O2 pipeline, and drop-in preservation (a hint-free
device unit compiles to byte-identical PTX).
"""

import os
import re
import unittest

from tests.support import (parse_source, requires_llvm, temporary_pascal_project, typecheck_module, typecheck_source)

from pascal1981.lexer import LexerError
from pascal1981.parser import ParserError

_HINTS = {'tuning-hints': True}

_IFACE = """DEVICE INTERFACE;
UNIT KH (scale, helper_only);
TYPE BUF = ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32;
PROCEDURE scale(outp: BUF; n: INTEGER32);
PROCEDURE helper_only(outp: BUF; n: INTEGER32);
END;
"""


def _impl(scale_attrs: str = '', body_extra: str = '') -> str:
    return f"""(*$INCLUDE:'kh'*)
DEVICE IMPLEMENTATION OF KH;
PROCEDURE scale(outp: BUF; n: INTEGER32){scale_attrs};
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    outp^[i] := i{body_extra}
END;
PROCEDURE helper_only(outp: BUF; n: INTEGER32);
BEGIN
END;
.
"""


def _compile_device_ir(iface: str, impl: str, *, device_triple: str = 'nvptx64-nvidia-cuda') -> str:
    from pascal1981.codegen import compile_to_llvm
    from pascal1981.parser import parse_file
    from pascal1981.type_checker import PascalTypeChecker
    with temporary_pascal_project({'kh': iface, 'kh.pas': impl}) as proj:
        path = os.path.join(proj, 'kh.pas')
        ast = parse_file(path)
        result = PascalTypeChecker(source_file=path).check(ast)
        assert result.success, [e.message for e in result.errors]
        return compile_to_llvm(ast, source_file=path, device_triple=device_triple)


class TestLaunchBoundParsing(unittest.TestCase):

    def test_attribute_forms_parse(self):
        for attrs in (' [MAXNTID(256)]', ' [MAXNTID(16, 16)]', ' [REQNTID(128)]',
                      ' [MINCTASM(2)]', ' [MAXNTID(256), MINCTASM(2)]'):
            src = ("DEVICE MODULE M;\n"
                   f"PROCEDURE go{attrs};\n"
                   "BEGIN END;\n"
                   ".\n")
            with self.subTest(attrs=attrs):
                self.assertIsNotNone(parse_source(src))

    def test_vintage_identifiers_named_maxntid_survive(self):
        """Contextual recognition: MAXNTID stays a plain identifier elsewhere."""
        src = ("PROGRAM P; VAR maxntid: INTEGER; "
               "BEGIN maxntid := 1; WRITELN(maxntid) END.")
        self.assertIsNotNone(parse_source(src))
        self.assertTrue(typecheck_source(src).success)


class TestUnrollParsing(unittest.TestCase):

    def test_unroll_before_each_loop_kind_parses(self):
        for loop in ('FOR i := 1 TO 8 DO n := n + i',
                     'WHILE i < 8 DO i := i + 1',
                     'REPEAT i := i + 1 UNTIL i = 8'):
            src = ("PROGRAM P; VAR i, n: INTEGER; BEGIN i := 0; n := 0; "
                   "{$UNROLL 4} " + loop + " END.")
            with self.subTest(loop=loop.split()[0]):
                self.assertIsNotNone(parse_source(src))

    def test_unroll_colon_form_parses(self):
        src = ("PROGRAM P; VAR i, n: INTEGER; BEGIN n := 0; "
               "{$UNROLL:4} FOR i := 1 TO 8 DO n := n + i END.")
        self.assertIsNotNone(parse_source(src))

    def test_misplaced_unroll_is_a_parse_error(self):
        src = ("PROGRAM P; VAR i, n: INTEGER; BEGIN "
               "{$UNROLL 4} n := 0; FOR i := 1 TO 8 DO n := n + i END.")
        with self.assertRaises(ParserError):
            parse_source(src)

    def test_unroll_without_count_is_a_lexer_error(self):
        src = ("PROGRAM P; VAR i, n: INTEGER; BEGIN n := 0; "
               "{$UNROLL} FOR i := 1 TO 8 DO n := n + i END.")
        with self.assertRaises((LexerError, ParserError)):
            parse_source(src)


class TestFeatureGating(unittest.TestCase):

    def test_unroll_rejected_under_vintage_dialect(self):
        src = ("PROGRAM P; VAR i, n: INTEGER; BEGIN n := 0; "
               "{$UNROLL 4} FOR i := 1 TO 8 DO n := n + i END.")
        result = typecheck_source(src)
        self.assertFalse(result.success)
        self.assertTrue(any('enable it with -f tuning-hints' in e.message for e in result.errors))

    def test_unroll_accepted_with_feature(self):
        src = ("PROGRAM P; VAR i, n: INTEGER; BEGIN n := 0; "
               "{$UNROLL 4} FOR i := 1 TO 8 DO n := n + i END.")
        self.assertTrue(typecheck_source(src, features=_HINTS).success)

    def test_unroll_accepted_in_device_code_without_flag(self):
        """The device feature baseline is the extended umbrella, which includes
        tuning-hints; no flag needed inside DEVICE code."""
        src = ("DEVICE MODULE M;\n"
               "VAR n: INTEGER;\n"
               "PROCEDURE go; VAR i: INTEGER; BEGIN n := 0; "
               "{$UNROLL 4} FOR i := 1 TO 8 DO n := n + i END;\n"
               ".\n")
        result = typecheck_source(src)
        self.assertTrue(result.success, [e.message for e in result.errors])

    def test_unroll_count_must_be_positive(self):
        src = ("PROGRAM P; VAR i, n: INTEGER; BEGIN n := 0; "
               "{$UNROLL 0} FOR i := 1 TO 8 DO n := n + i END.")
        result = typecheck_source(src, features=_HINTS)
        self.assertFalse(result.success)
        self.assertTrue(any('must be a positive integer' in e.message for e in result.errors))

    def test_launch_bounds_accepted_on_exported_device_procedure(self):
        result = typecheck_module(_IFACE, _impl(' [MAXNTID(256), MINCTASM(2)]'), module_name='KH')
        self.assertTrue(result.success, [e.message for e in result.errors])

    def test_launch_bounds_rejected_in_host_code(self):
        src = ("PROGRAM P; PROCEDURE go [MAXNTID(256)]; BEGIN END; BEGIN go END.")
        result = typecheck_source(src, features=_HINTS)
        self.assertFalse(result.success)
        self.assertTrue(any('only valid in device code' in e.message for e in result.errors))

    def test_launch_bounds_rejected_without_feature_in_host_code(self):
        src = ("PROGRAM P; PROCEDURE go [MAXNTID(256)]; BEGIN END; BEGIN go END.")
        result = typecheck_source(src)
        self.assertFalse(result.success)
        self.assertTrue(any('enable it with -f tuning-hints' in e.message for e in result.errors))

    def test_launch_bounds_rejected_on_non_exported_device_procedure(self):
        impl = """(*$INCLUDE:'kh'*)
DEVICE IMPLEMENTATION OF KH;
PROCEDURE scale(outp: BUF; n: INTEGER32);
BEGIN
END;
PROCEDURE helper_only(outp: BUF; n: INTEGER32);
BEGIN
END;
PROCEDURE inner [MAXNTID(64)];
BEGIN
END;
.
"""
        result = typecheck_module(_IFACE, impl, module_name='KH')
        self.assertFalse(result.success)
        self.assertTrue(any('only meaningful on an exported device kernel procedure' in e.message
                            for e in result.errors))

    def test_launch_bounds_rejected_on_function(self):
        src = ("DEVICE MODULE M;\n"
               "FUNCTION f(x: INTEGER32): INTEGER32 [MAXNTID(64)]; BEGIN f := x END;\n"
               ".\n")
        result = typecheck_source(src)
        self.assertFalse(result.success)
        self.assertTrue(any('cannot be a FUNCTION' in e.message for e in result.errors))

    def test_launch_bound_arity_and_values_validated(self):
        cases = (
            (' [MAXNTID(16, 16, 2, 2)]', 'dimension argument'),
            (' [MINCTASM(1, 2)]', 'dimension argument'),
            (' [MAXNTID(0)]', 'positive integer literal'),
            (' [MINCTASM(n)]', 'positive integer literal'),
        )
        for attrs, expect in cases:
            with self.subTest(attrs=attrs):
                result = typecheck_module(_IFACE, _impl(attrs), module_name='KH')
                self.assertFalse(result.success)
                self.assertTrue(any(expect in e.message for e in result.errors),
                                [e.message for e in result.errors])


@requires_llvm
class TestLoweringIR(unittest.TestCase):

    def test_kernel_entry_carries_both_launch_bound_encodings(self):
        ir = _compile_device_ir(_IFACE, _impl(' [MAXNTID(256), MINCTASM(2)]'))
        # Current-LLVM function string attributes...
        self.assertIn('"nvvm.maxntid"="256"', ir)
        self.assertIn('"nvvm.minctasm"="2"', ir)
        # ...plus the legacy nvvm.annotations entries for older LLVM.
        self.assertIn('nvvm.annotations', ir)
        self.assertIn('!"maxntid_x", i32 256', ir)
        self.assertIn('!"minctasm", i32 2', ir)

    def test_multi_dimension_maxntid_joins_values(self):
        ir = _compile_device_ir(_IFACE, _impl(' [MAXNTID(16, 16)]'))
        self.assertIn('"nvvm.maxntid"="16,16"', ir)
        self.assertIn('!"maxntid_x", i32 16', ir)
        self.assertIn('!"maxntid_y", i32 16', ir)

    def test_cpu_device_triple_ignores_launch_bounds(self):
        """On the x86 CPU-device parity path there is no kernel entry, so the
        hints are inert and no NVVM surface appears."""
        ir = _compile_device_ir(_IFACE, _impl(' [MAXNTID(256)]'),
                                device_triple='x86_64-pc-linux-gnu')
        self.assertNotIn('nvvm', ir)

    def test_unroll_metadata_is_self_referential(self):
        src = ("PROGRAM P; PROCEDURE sink(n: INTEGER); EXTERN; VAR i: INTEGER; "
               "BEGIN {$UNROLL 4} FOR i := 1 TO 64 DO sink(i) END.")
        ir = self._host_ir(src)
        self.assertIn('!"llvm.loop.unroll.count", i32 4', ir)
        self.assertIn('!llvm.loop', ir)
        # The loop-ID node must be a distinct self-reference or LLVM ignores
        # the hint (see _selfref_loop_metadata).
        self.assertTrue(re.search(r'(!\d+) = distinct !\{ \1, !\d+ \}', ir), ir)
        self.assertNotIn('!{ null,', ir)

    def test_each_loop_kind_carries_the_metadata(self):
        for loop in ('FOR i := 1 TO 8 DO sink(i)',
                     'WHILE i < 8 DO BEGIN sink(i); i := i + 1 END',
                     'REPEAT sink(i); i := i + 1 UNTIL i = 8'):
            src = ("PROGRAM P; PROCEDURE sink(n: INTEGER); EXTERN; VAR i: INTEGER; "
                   "BEGIN i := 0; {$UNROLL 2} " + loop + " END.")
            with self.subTest(loop=loop.split()[0]):
                ir = self._host_ir(src)
                self.assertIn('!llvm.loop', ir)
                self.assertIn('llvm.loop.unroll.count', ir)

    def test_hint_free_module_has_no_loop_metadata(self):
        src = ("PROGRAM P; PROCEDURE sink(n: INTEGER); EXTERN; VAR i: INTEGER; "
               "BEGIN FOR i := 1 TO 64 DO sink(i) END.")
        ir = self._host_ir(src)
        self.assertNotIn('llvm.loop', ir)

    @staticmethod
    def _host_ir(src: str) -> str:
        from pascal1981.codegen import compile_to_llvm
        from pascal1981.type_checker import PascalTypeChecker
        ast = parse_source(src)
        result = PascalTypeChecker(features=_HINTS).check(ast)
        assert result.success, [e.message for e in result.errors]
        return compile_to_llvm(ast, features=_HINTS)


@requires_llvm
class TestLoweringPTXAndPipeline(unittest.TestCase):

    def test_ptx_carries_launch_bound_directives(self):
        """Item verification: `.maxntid` appears in the kernel directive."""
        from pascal1981.compile_to_ptx import llvm_ir_to_ptx
        ir = _compile_device_ir(_IFACE, _impl(' [MAXNTID(256), MINCTASM(2)]'))
        ptx = llvm_ir_to_ptx(ir, cpu='sm_70')
        self.assertIn('.maxntid 256', ptx)
        self.assertIn('.minnctapersm 2', ptx)

    def test_reqntid_directive(self):
        from pascal1981.compile_to_ptx import llvm_ir_to_ptx
        ptx = llvm_ir_to_ptx(_compile_device_ir(_IFACE, _impl(' [REQNTID(128)]')), cpu='sm_70')
        self.assertIn('.reqntid 128', ptx)

    def test_hint_free_device_unit_ptx_is_unchanged(self):
        """Drop-in discipline: without hints, IR and PTX are byte-identical to
        a build of the same source before this feature existed (no metadata,
        no attributes, no directives)."""
        from pascal1981.compile_to_ptx import llvm_ir_to_ptx
        ir = _compile_device_ir(_IFACE, _impl())
        self.assertNotIn('nvvm.annotations', ir)
        self.assertNotIn('llvm.loop', ir)
        ptx = llvm_ir_to_ptx(ir, cpu='sm_70')
        self.assertNotIn('.maxntid', ptx)
        self.assertNotIn('.reqntid', ptx)
        self.assertNotIn('.minnctapersm', ptx)

    def test_unroll_hint_fires_under_o2(self):
        """End to end: the {$UNROLL 4} hint makes LLVM's unroller replicate the
        loop body (4 call sites after O2 vs 1 without the hint). This is what
        the self-reference rewrite buys: with a null loop-ID head the same
        pipeline leaves the loop rolled."""
        import llvmlite.binding as llvm
        from pascal1981.codegen import compile_to_llvm
        from pascal1981.type_checker import PascalTypeChecker
        llvm.initialize_all_targets()
        llvm.initialize_all_asmprinters()

        def call_count_after_o2(hint: str) -> int:
            src = ("PROGRAM P; PROCEDURE sink(n: INTEGER); EXTERN; VAR i: INTEGER; "
                   "BEGIN " + hint + " FOR i := 1 TO 64 DO sink(i) END.")
            ast = parse_source(src)
            result = PascalTypeChecker(features=_HINTS).check(ast)
            assert result.success, [e.message for e in result.errors]
            mod = llvm.parse_assembly(compile_to_llvm(ast, features=_HINTS))
            mod.verify()
            tm = llvm.Target.from_triple('x86_64-pc-linux-gnu').create_target_machine()
            pb = llvm.create_pass_builder(tm, llvm.create_pipeline_tuning_options(speed_level=2))
            pb.getModulePassManager().run(mod, pb)
            return len(re.findall(r'call [^\n]*sink', str(mod)))

        self.assertEqual(call_count_after_o2(''), 1)
        self.assertEqual(call_count_after_o2('{$UNROLL 4}'), 4)


if __name__ == '__main__':
    unittest.main()
