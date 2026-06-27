"""C-FFI Phase 0 (foreign-ABI diagnostics) and Phase 1 (the [C] attribute and the
C-ABI fixed-width type aliases).

Layered like the rest of the suite: parser/typecheck cases need no toolchain;
the build-and-run cases are decorated with @requires_exe and auto-skip without
llvmlite/clang. See docs/c-abi-foreign-functions.md.
"""

import unittest

from pascal1981.parser import ParserError

from tests.support import build_and_run_pascal_project, parse_source, requires_exe, typecheck_source
from pascal1981.features import extended_features

# The C-FFI surface ([C] + CINT/CLONG/... aliases) is gated behind the extended
# dialect, so every C-FFI typecheck/build runs with the umbrella on.  _tc() is the
# extended-dialect typecheck; bare typecheck_source() is used only by the gating
# tests that assert the faithful (vintage) dialect rejects the surface.
EXT = extended_features()


def _tc(src):
    return typecheck_source(src, features=EXT)


def _errors(result):
    return [e.message for e in result.errors]


def _warnings(result):
    return [w.message for w in result.warnings]


class TestCAttributeParsing(unittest.TestCase):
    """Phase 1: the [C] / [CDECL] foreign marker parses in attribute position."""

    def test_c_attribute_parses(self):
        ast = parse_source("PROGRAM P(output);\n"
                           "FUNCTION f(x: CINT): CINT [C]; EXTERN;\n"
                           "BEGIN END.")
        self.assertIsNotNone(ast)

    def test_cdecl_attribute_parses_and_normalizes_to_c(self):
        ast = parse_source("PROGRAM P(output);\n"
                           "PROCEDURE g(x: CINT) [CDECL]; EXTERN;\n"
                           "BEGIN END.")
        # Both spellings normalize to the name 'C'.
        proc = next(d for d in ast.block.decls if getattr(d, 'name', '') == 'g')
        self.assertEqual([a.name for a in proc.attributes], ['C'])

    def test_c_combines_with_other_attributes(self):
        ast = parse_source("PROGRAM P(output);\n"
                           "FUNCTION f(x: CINT): CINT [C, EXTERN]; EXTERN;\n"
                           "BEGIN END.")
        self.assertIsNotNone(ast)

    def test_unknown_attribute_still_rejected(self):
        # The [C] addition must not turn the attribute section into a free-for-all.
        with self.assertRaises(ParserError):
            parse_source("PROGRAM P(output);\n"
                        "FUNCTION f(x: CINT): CINT [BOGUS]; EXTERN;\n"
                        "BEGIN END.")


class TestCTypeAliases(unittest.TestCase):
    """The C-ABI fixed-width aliases resolve under the extended dialect."""

    def test_scalar_aliases_typecheck_under_extended(self):
        result = _tc(
            "PROGRAM P(output);\n"
            "FUNCTION cube(x: CINT): CINT [C]; EXTERN;\n"
            "FUNCTION addd(a: CDOUBLE; b: CDOUBLE): CDOUBLE [C]; EXTERN;\n"
            "FUNCTION len(s: CPTR): CSIZE_T [C]; EXTERN;\n"
            "VAR r: CINT;\n"
            "BEGIN r := cube(3) END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_user_type_shadows_alias(self):
        # A user TYPE named like an alias still wins (builtins are shadowable).
        result = _tc(
            "PROGRAM P(output);\n"
            "TYPE cint = BOOLEAN;\n"
            "VAR b: cint;\n"
            "BEGIN b := TRUE END.")
        self.assertTrue(result.success, msg=_errors(result))


class TestCFfiDialectGating(unittest.TestCase):
    """The whole C-FFI surface is available only under the extended dialect.

    Gating it here (rather than on the by-value behavior alone) keeps the wide C
    widths and the interface that needs them arriving together: a faithful 1981
    program cannot reach a wide type through a C alias, nor opt a routine into
    C-ABI lowering. The umbrella is read as "all of extended_features() is on".
    """

    def test_c_attribute_rejected_in_vintage(self):
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: INTEGER32): INTEGER32 [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('[C]' in m and 'extended' in m for m in _errors(result)),
                        msg=_errors(result))

    def test_c_aliases_undeclared_in_vintage(self):
        # Without extended, CINT et al. are simply not predeclared.
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "VAR r: CINT;\n"
            "BEGIN END.")
        self.assertFalse(result.success)

    def test_c_attribute_accepted_under_extended(self):
        result = _tc(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CINT): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_non_extended_with_wide_integers_only_still_rejects(self):
        # wide-integers alone is not the umbrella; the C surface stays gated.
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: INTEGER32): INTEGER32 [C]; EXTERN;\n"
            "BEGIN END.",
            features={'wide-integers': True})
        self.assertFalse(result.success)
        self.assertTrue(any('extended' in m for m in _errors(result)), msg=_errors(result))


class TestForeignAbiDiagnostics(unittest.TestCase):
    """Phase 0: by-value aggregates in foreign routines are rejected."""

    _POINT = "TYPE point = RECORD x: CINT; y: CINT END;\n"

    def test_byvalue_aggregate_param_rejected_without_c(self):
        # Plain EXTERN (no [C]) still rejects by-value aggregates.
        result = _tc(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(p: point): CINT; EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('by-value aggregate parameter' in m for m in _errors(result)))

    def test_byvalue_aggregate_return_rejected_without_c(self):
        result = _tc(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION mk(v: CINT): point; EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('by-value aggregate return' in m for m in _errors(result)))

    def test_byvalue_aggregate_param_accepted_with_c(self):
        # Phase 2: the [C] marker opts into C-ABI-correct by-value lowering.
        result = _tc(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(p: point): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_byvalue_aggregate_return_accepted_with_c(self):
        result = _tc(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION mk(v: CINT): point [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_byvalue_string_param_rejected(self):
        result = _tc(
            "PROGRAM P(output);\n"
            "PROCEDURE g(s: STRING(20)); EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('by-value aggregate parameter' in m for m in _errors(result)))

    def test_const_aggregate_param_accepted(self):
        result = _tc(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(CONST p: point): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_var_aggregate_param_accepted(self):
        result = _tc(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(VAR p: point): CINT; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_non_foreign_byvalue_aggregate_allowed(self):
        # The guard is scoped to EXTERN/EXTERNAL; ordinary routines are untouched.
        result = _tc(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(p: point): CINT;\n"
            "BEGIN sumpt := p.x + p.y END;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_bare_integer_param_warns(self):
        result = _tc(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: INTEGER): INTEGER [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))
        self.assertTrue(any('16-bit INTEGER' in m for m in _warnings(result)))

    def test_cint_param_does_not_warn(self):
        result = _tc(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CINT): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))
        self.assertFalse(any('16-bit INTEGER' in m for m in _warnings(result)))


@requires_exe
class TestCFfiBuildAndRun(unittest.TestCase):
    """Phase 1 end-to-end: a [C] extern using the aliases links against C and runs."""

    def test_scalar_c_extern_builds_and_runs(self):
        files = {
            'p.pas': ("PROGRAM P(output);\n"
                      "FUNCTION cube(x: CINT): CINT [C]; EXTERN;\n"
                      "FUNCTION addd(a: CDOUBLE; b: CDOUBLE): CDOUBLE [C]; EXTERN;\n"
                      "VAR r: CINT;\n"
                      "BEGIN r := cube(3); WRITELN(r); WRITELN(addd(1.5, 2.25)) END."),
            'cimpl.c': ("#include <stdint.h>\n"
                        "int32_t cube(int32_t x){return x*x*x;}\n"
                        "double addd(double a, double b){return a+b;}\n"),
        }
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=[('p.pas', 'p.ll')],
            link_ir_relpaths=['p.ll', 'cimpl.c'],
            exe_name='c-ffi-scalar',
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        self.assertEqual(lines[0], '27')
        self.assertIn('3.7500000E+00', lines[1])

    def test_const_aggregate_by_reference_builds_and_runs(self):
        files = {
            'q.pas': ("PROGRAM P(output);\n"
                      "TYPE point = RECORD x: CINT; y: CINT END;\n"
                      "FUNCTION sumpt(CONST p: point): CINT [C]; EXTERN;\n"
                      "VAR pt: point;\n"
                      "BEGIN pt.x := 10; pt.y := 32; WRITELN(sumpt(pt)) END."),
            'qimpl.c': ("#include <stdint.h>\n"
                        "struct point { int32_t x, y; };\n"
                        "int32_t sumpt(const struct point *p){return p->x + p->y;}\n"),
        }
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=[('q.pas', 'q.ll')],
            link_ir_relpaths=['q.ll', 'qimpl.c'],
            exe_name='c-ffi-aggregate-ref',
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([ln.strip() for ln in out.splitlines() if ln.strip()], ['42'])


class TestSysVClassifier(unittest.TestCase):
    """Phase 2: the System V AMD64 eightbyte classifier (pure, no toolchain).

    Coerced register shapes are asserted against the documented clang lowering.
    """

    def setUp(self):
        import llvmlite.ir as ir
        from pascal1981.codegen.c_abi import SysVAmd64Abi
        self.ir = ir
        self.abi = SysVAmd64Abi()

    def _st(self, *elems):
        return self.ir.LiteralStructType(list(elems))

    def test_two_i32_coerces_to_one_i64(self):
        ir = self.ir
        low = self.abi.classify_aggregate(self._st(ir.IntType(32), ir.IntType(32)))
        self.assertEqual(low.kind, 'coerced')
        self.assertEqual([str(p) for p in low.pieces], ['i64'])

    def test_three_i32_coerces_to_i64_i32(self):
        ir = self.ir
        low = self.abi.classify_aggregate(self._st(ir.IntType(32), ir.IntType(32), ir.IntType(32)))
        self.assertEqual([str(p) for p in low.pieces], ['i64', 'i32'])

    def test_two_doubles_stay_two_sse(self):
        ir = self.ir
        low = self.abi.classify_aggregate(self._st(ir.DoubleType(), ir.DoubleType()))
        self.assertEqual([str(p) for p in low.pieces], ['double', 'double'])

    def test_double_then_int_is_sse_int(self):
        ir = self.ir
        low = self.abi.classify_aggregate(self._st(ir.DoubleType(), ir.IntType(32)))
        self.assertEqual([str(p) for p in low.pieces], ['double', 'i32'])

    def test_two_floats_pack_into_vector(self):
        ir = self.ir
        low = self.abi.classify_aggregate(self._st(ir.FloatType(), ir.FloatType()))
        self.assertEqual([str(p) for p in low.pieces], ['<2 x float>'])

    def test_oversize_struct_is_memory(self):
        ir = self.ir
        low = self.abi.classify_aggregate(self._st(ir.IntType(64), ir.IntType(64), ir.IntType(64)))
        self.assertEqual(low.kind, 'memory')

    def test_non_sysv_triple_raises(self):
        from pascal1981.codegen.base import CodegenError
        from pascal1981.codegen.c_abi import c_abi_for_triple
        c_abi_for_triple('x86_64-pc-linux-gnu')  # ok
        with self.assertRaises(CodegenError):
            c_abi_for_triple('aarch64-unknown-linux-gnu')


@requires_exe
class TestCAbiAggregateBuildAndRun(unittest.TestCase):
    """Phase 2 end-to-end: by-value aggregates cross the C ABI correctly.

    Covers the cases the original analysis showed broken (struct-by-value arg and
    struct return) plus the MEMORY (>16B) byval/sret path.
    """

    _C = ("#include <stdint.h>\n"
          "struct point { int32_t x, y; };\n"
          "struct big { int64_t a, b, c; };\n"
          "int32_t sumpt(struct point p){return p.x + p.y;}\n"
          "struct point makep(int32_t v){struct point s={v, v*2}; return s;}\n"
          "int64_t sumbig(struct big p){return p.a + p.b + p.c;}\n"
          "struct big makebig(int64_t v){struct big s={v, v*2, v*3}; return s;}\n")

    def _run(self, pas, exe):
        rc, out, err = build_and_run_pascal_project(
            files={'m.pas': pas, 'c.c': self._C},
            compile_pairs=[('m.pas', 'm.ll')],
            link_ir_relpaths=['m.ll', 'c.c'],
            exe_name=exe,
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def test_struct_by_value_argument(self):
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE point = RECORD x: CINT; y: CINT END;\n"
            "FUNCTION sumpt(p: point): CINT [C]; EXTERN;\n"
            "VAR pt: point;\n"
            "BEGIN pt.x := 10; pt.y := 32; WRITELN(sumpt(pt)) END.",
            'c-abi-byval-arg')
        self.assertEqual(lines, ['42'])

    def test_struct_return_by_value(self):
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE point = RECORD x: CINT; y: CINT END;\n"
            "FUNCTION makep(v: CINT): point [C]; EXTERN;\n"
            "VAR q: point;\n"
            "BEGIN q := makep(5); WRITELN(q.x); WRITELN(q.y) END.",
            'c-abi-ret')
        self.assertEqual(lines, ['5', '10'])

    def test_memory_class_struct_by_value_and_return(self):
        lines = self._run(
            "PROGRAM P(output);\n"
            "TYPE big = RECORD a: CLONG; b: CLONG; c: CLONG END;\n"
            "FUNCTION sumbig(p: big): CLONG [C]; EXTERN;\n"
            "FUNCTION makebig(v: CLONG): big [C]; EXTERN;\n"
            "VAR g, h: big;\n"
            "BEGIN g.a := 10; g.b := 20; g.c := 30; WRITELN(sumbig(g));\n"
            "  h := makebig(7); WRITELN(h.a); WRITELN(h.b); WRITELN(h.c) END.",
            'c-abi-memory')
        self.assertEqual(lines, ['60', '7', '14', '21'])


if __name__ == '__main__':
    unittest.main()
