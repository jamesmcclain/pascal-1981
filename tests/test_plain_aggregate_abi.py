"""Plain-Pascal (non-[C]) by-value aggregate parameters/returns: SysV
byval/sret/coerced ABI conversion.

Mirrors tests/test_c_ffi.py's TestCAbiAggregateBuildAndRun coverage, but for
ordinary PROCEDURE/FUNCTION declarations, which now route through the same
build_c_abi_plan/codegen_c_abi_call machinery as [C] FOREIGN routines
(c_abi.py) instead of crossing calls as first-class LLVM aggregate values.
There is no external C reference to cross-check against here (both caller
and callee are this same compiler's own output), so runtime coverage
instead checks Pascal by-value semantics survive the conversion: a callee
mutating its own copy of a byval/sret-passed aggregate must never affect
the caller's.
"""

import unittest

from pascal1981.features import extended_features
from tests.support import (build_and_run_pascal_project, parse_source, requires_exe, requires_llvm, typecheck_source)

EXT = extended_features()


def compile_to_ir(src: str, features=None) -> str:
    from pascal1981.codegen_llvm import compile_to_llvm
    from pascal1981.type_checker import PascalTypeChecker
    ast = parse_source(src)
    result = PascalTypeChecker(features=features).check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    return compile_to_llvm(ast, features=features)


@requires_llvm
class TestPlainAggregateIRShape(unittest.TestCase):
    """IR-shape assertions mirroring byval_sret_plain_aggregate.pas (native's
    checklit analog): a plain routine now emits byval/sret exactly like the
    [C] FOREIGN path."""

    def test_plain_value_param_is_byval(self):
        ir = compile_to_ir("PROGRAM P(output);\n"
                           "TYPE Str255 = LSTRING(255);\n"
                           "PROCEDURE TakesStr(s: Str255);\n"
                           "BEGIN WRITELN(s); END;\n"
                           "VAR msg: Str255;\n"
                           "BEGIN msg := 'hello'; TakesStr(msg); END.")
        self.assertIn('byval', ir)
        # PROCEDUREs keep the vintage i32-returning convention (untouched by
        # this conversion, which only affects aggregate parameter/return
        # shape) -- only the parameter itself becomes byval.
        self.assertIn('define i32 @"TakesStr"([256 x i8]* byval', ir)

    def test_plain_memory_return_is_sret(self):
        ir = compile_to_ir("PROGRAM P(output);\n"
                           "TYPE Str255 = LSTRING(255);\n"
                           "FUNCTION MakeStr: Str255;\n"
                           "VAR res: Str255;\n"
                           "BEGIN res := 'hello'; MakeStr := res; END;\n"
                           "VAR msg: Str255;\n"
                           "BEGIN msg := MakeStr; END.")
        self.assertIn('sret', ir)
        self.assertIn('define void @"MakeStr"([256 x i8]* noalias sret', ir)


@requires_exe
class TestPlainAggregateBuildAndRun(unittest.TestCase):
    """Runtime-behavior analog of TestCAbiAggregateBuildAndRun: MEMORY-class
    (sret, with and without extra scalar params, and a niladic call reached
    via the bare-Identifier-as-call path) and COERCED-class plain aggregate
    returns, plus explicit by-value-semantics checks."""

    def _run(self, pas, exe):
        rc, out, err = build_and_run_pascal_project(
            files={'m.pas': pas},
            compile_pairs=[('m.pas', 'm.ll')],
            link_ir_relpaths=['m.ll'],
            exe_name=exe,
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def test_memory_class_niladic_and_scaled_return(self):
        # BigConst: MEMORY class, zero real parameters -- exercises the bare
        # Identifier-as-niladic-call path (g := BigConst), which previously
        # relied on an LLVM arg-count heuristic invalidated by the hidden
        # sret argument. BigScale: MEMORY class in AND out plus a scalar
        # parameter, mutating its own copy -- must not affect the caller's.
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE Big = RECORD a, b, c, d, e: CINT END;\n"
            "FUNCTION BigConst: Big;\n"
            "VAR r: Big;\n"
            "BEGIN r.a := 1; r.b := 2; r.c := 3; r.d := 4; r.e := 5; BigConst := r; END;\n"
            "FUNCTION BigScale(b: Big; k: CINT): Big;\n"
            "BEGIN b.a := b.a * k; b.b := b.b * k; b.c := b.c * k; b.d := b.d * k; b.e := b.e * k; BigScale := b; END;\n"
            "VAR g, h: Big;\n"
            "BEGIN\n"
            "  g := BigConst;\n"
            "  WRITELN(g.a, ' ', g.b, ' ', g.c, ' ', g.d, ' ', g.e);\n"
            "  h := BigScale(g, 10);\n"
            "  WRITELN(h.a, ' ', h.b, ' ', h.c, ' ', h.d, ' ', h.e);\n"
            "  WRITELN(g.a, ' ', g.b, ' ', g.c, ' ', g.d, ' ', g.e);\n"
            "END.", 'plain-agg-memory')
        self.assertEqual(
            lines,
            [
                '1 2 3 4 5',
                '10 20 30 40 50',
                '1 2 3 4 5',  # g untouched by BigScale's mutation of its own copy
            ])

    def test_coerced_class_return(self):
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE Pair = RECORD a, b: CINT END;\n"
            "FUNCTION MakePair(x, y: CINT): Pair;\n"
            "VAR p: Pair;\n"
            "BEGIN p.a := x; p.b := y; MakePair := p; END;\n"
            "VAR p: Pair;\n"
            "BEGIN p := MakePair(11, 22); WRITELN(p.a, ' ', p.b); END.", 'plain-agg-coerced')
        self.assertEqual(lines, ['11 22'])

    def test_coerced_class_explicit_return_statement(self):
        # Exercises codegen_return_stmt's coerced-class path specifically
        # (distinct from the implicit end-of-body epilogue above).
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE Pair = RECORD a, b: CINT END;\n"
            "FUNCTION EarlyPair(early: BOOLEAN): Pair;\n"
            "VAR p: Pair;\n"
            "BEGIN\n"
            "  IF early THEN\n"
            "  BEGIN\n"
            "    p.a := 99; p.b := 100; EarlyPair := p; RETURN;\n"
            "  END;\n"
            "  p.a := 1; p.b := 2; EarlyPair := p;\n"
            "END;\n"
            "VAR r: Pair;\n"
            "BEGIN\n"
            "  r := EarlyPair(TRUE); WRITELN(r.a, ' ', r.b);\n"
            "  r := EarlyPair(FALSE); WRITELN(r.a, ' ', r.b);\n"
            "END.", 'plain-agg-coerced-return-stmt')
        self.assertEqual(lines, ['99 100', '1 2'])

    def test_byval_string_literal_argument(self):
        # A bare string-literal argument to a value-mode LSTRING parameter
        # must build the real length-prefixed wire format, not spill
        # codegen_expr's raw char-pointer representation of the literal.
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE Str255 = LSTRING(255);\n"
            "PROCEDURE TakesStr(s: Str255);\n"
            "BEGIN WRITELN(s); END;\n"
            "BEGIN TakesStr('literal hello'); END.", 'plain-agg-byval-literal')
        self.assertEqual(lines, ['literal hello'])

    def test_byval_mutation_does_not_leak_to_caller(self):
        # A byval parameter's storage is the callee's own private copy (the
        # caller made it, per SysV byval semantics) -- a field write through
        # it must not affect the caller's own aggregate.
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE Pair = RECORD a, b: CINT END;\n"
            "PROCEDURE Mutate(p: Pair);\n"
            "BEGIN p.a := 999; WRITELN('inside: ', p.a); END;\n"
            "VAR outer: Pair;\n"
            "BEGIN\n"
            "  outer.a := 1; outer.b := 2;\n"
            "  Mutate(outer);\n"
            "  WRITELN('outer after: ', outer.a);\n"
            "END.", 'plain-agg-byval-mutate')
        self.assertEqual(lines, ['inside: 999', 'outer after: 1'])
