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


# =============================================================================
# Phase 3: Variadic foreign functions
# =============================================================================

class TestVariadicParsing(unittest.TestCase):
    """Phase 3: the [VARARGS] attribute parses in attribute position."""

    def test_varargs_attribute_parses_on_procedure(self):
        ast = parse_source(
            "PROGRAM P(output);\n"
            "PROCEDURE vprintf(fmt: CPTR) [C, VARARGS]; EXTERN;\n"
            "BEGIN END.")
        self.assertIsNotNone(ast)

    def test_varargs_attribute_parses_on_function(self):
        ast = parse_source(
            "PROGRAM P(output);\n"
            "FUNCTION printf(fmt: CPTR): CINT [C, VARARGS]; EXTERN;\n"
            "BEGIN END.")
        self.assertIsNotNone(ast)

    def test_varargs_attribute_normalizes(self):
        ast = parse_source(
            "PROGRAM P(output);\n"
            "FUNCTION printf(fmt: CPTR): CINT [C, VARARGS]; EXTERN;\n"
            "BEGIN END.")
        func = next(d for d in ast.block.decls if getattr(d, 'name', '') == 'printf')
        attr_names = [a.name for a in func.attributes]
        self.assertIn('VARARGS', attr_names)
        self.assertIn('C', attr_names)


class TestVariadicTypecheck(unittest.TestCase):
    """Phase 3: type-checker gates for [VARARGS]."""

    def test_varargs_accepted_with_c_and_extern(self):
        result = _tc(
            "PROGRAM P(output);\n"
            "FUNCTION printf(fmt: CPTR): CINT [C, VARARGS]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_varargs_without_c_rejected(self):
        result = _tc(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CINT): CINT [VARARGS]; EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('[VARARGS]' in m and '[C]' in m for m in _errors(result)),
                        msg=_errors(result))

    def test_varargs_rejected_in_vintage_dialect(self):
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: INTEGER32): CINT [C, VARARGS]; EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        # Will hit either [C] or [VARARGS] extended-dialect gate first.
        self.assertTrue(any('extended' in m for m in _errors(result)),
                        msg=_errors(result))

    def test_varargs_with_no_fixed_params_accepted(self):
        # Degenerate: only a format pointer, then varargs.
        result = _tc(
            "PROGRAM P(output);\n"
            "FUNCTION printf(fmt: CPTR): CINT [C, VARARGS]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))


@requires_exe
class TestVariadicBuildAndRun(unittest.TestCase):
    """Phase 3 end-to-end: variadic C functions called from Pascal."""

    def _run(self, files, compile_pairs, link_ir, exe):
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=compile_pairs,
            link_ir_relpaths=link_ir,
            exe_name=exe,
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def test_variadic_sum_integers(self):
        """Call a variadic C function that sums N integer args."""
        files = {
            'v.pas': (
                "PROGRAM P(output);\n"
                "FUNCTION sum_n(count: CINT): CINT [C, VARARGS]; EXTERN;\n"
                "VAR r: CINT;\n"
                "BEGIN r := sum_n(3, 10, 20, 30); WRITELN(r) END."),
            'vimpl.c': (
                "#include <stdarg.h>\n"
                "#include <stdint.h>\n"
                "int32_t sum_n(int32_t count, ...) {\n"
                "  va_list ap; va_start(ap, count);\n"
                "  int32_t s = 0;\n"
                "  for (int i = 0; i < count; i++) s += va_arg(ap, int32_t);\n"
                "  va_end(ap); return s;\n"
                "}"),
        }
        lines = self._run(files, [('v.pas', 'v.ll')], ['v.ll', 'vimpl.c'], 'varargs-sum')
        self.assertEqual(lines, ['60'])

    def test_variadic_float_promotion(self):
        """REAL32 (float) args in the variadic tail must be promoted to double.

        C default argument promotions: float -> double.  Pass two REAL32 vars
        in the variadic tail; the C callee reads them as `double`.  If
        promotion is missing the callee reads garbage (4-byte float bits
        interpreted as 8-byte double).
        """
        files = {
            'fp.pas': (
                "PROGRAM P(output);\n"
                "FUNCTION sum_floats(count: CINT): CDOUBLE [C, VARARGS]; EXTERN;\n"
                "VAR r: CDOUBLE; x, y: REAL32;\n"
                "BEGIN\n"
                "  x := 1.5; y := 2.5;\n"
                "  r := sum_floats(2, x, y);\n"
                "  WRITELN(r)\n"
                "END."),
            'fpimpl.c': (
                "#include <stdarg.h>\n"
                "#include <stdint.h>\n"
                "double sum_floats(int32_t count, ...) {\n"
                "  va_list ap; va_start(ap, count);\n"
                "  double s = 0.0;\n"
                "  for (int i = 0; i < count; i++) s += va_arg(ap, double);\n"
                "  va_end(ap); return s;\n"
                "}"),
        }
        lines = self._run(files, [('fp.pas', 'fp.ll')], ['fp.ll', 'fpimpl.c'], 'varargs-float')
        self.assertEqual(len(lines), 1)
        val = float(lines[0].replace('E', 'e').replace('+', ''))
        self.assertAlmostEqual(val, 4.0, places=5)

    def test_variadic_mixed_args(self):
        """Variadic call with mixed integer and double args."""
        files = {
            'mx.pas': (
                "PROGRAM P(output);\n"
                "FUNCTION mix(n: CINT): CLONG [C, VARARGS]; EXTERN;\n"
                "VAR r: CLONG;\n"
                "BEGIN r := mix(3, 7, 2.5, 100); WRITELN(r) END."),
            'mximpl.c': (
                "#include <stdarg.h>\n"
                "#include <stdint.h>\n"
                "int64_t mix(int32_t n, ...) {\n"
                "  va_list ap; va_start(ap, n);\n"
                "  int64_t a = va_arg(ap, int64_t);\n"
                "  double  b = va_arg(ap, double);\n"
                "  int64_t c = va_arg(ap, int64_t);\n"
                "  va_end(ap); return a + (int64_t)b + c;\n"
                "}"),
        }
        lines = self._run(files, [('mx.pas', 'mx.ll')], ['mx.ll', 'mximpl.c'], 'varargs-mixed')
        # 7 + 2 + 100 = 109
        self.assertEqual(lines, ['109'])

    def test_regression_non_varargs_c_extern_unchanged(self):
        """A [C] EXTERN without [VARARGS] must still work identically (Phase 1/2 regression)."""
        files = {
            'r.pas': (
                "PROGRAM P(output);\n"
                "FUNCTION cube(x: CINT): CINT [C]; EXTERN;\n"
                "BEGIN WRITELN(cube(4)) END."),
            'rimpl.c': (
                "#include <stdint.h>\n"
                "int32_t cube(int32_t x){return x*x*x;}"),
        }
        lines = self._run(files, [('r.pas', 'r.ll')], ['r.ll', 'rimpl.c'], 'varargs-regression')
        self.assertEqual(lines, ['64'])


# =============================================================================
# Phase 4: signext / zeroext / void fidelity
# =============================================================================

class TestPhase4ScalarExtensionIR(unittest.TestCase):
    """Phase 4: verify signext/zeroext appear in emitted IR without running anything.

    These are IR-level checks: parse + codegen, then grep the LLVM text.  No
    llvmlite or clang installation required beyond what codegen needs.
    """

    def _ir_lines(self, src):
        """Return the LLVM IR lines for a C-FFI program."""
        try:
            import llvmlite.ir  # noqa: F401
        except ImportError:
            self.skipTest('llvmlite not available')
        from pascal1981.codegen import compile_to_llvm
        ast = parse_source(src)
        mod = compile_to_llvm(ast, features=EXT)
        return str(mod).splitlines()

    def _decl_line(self, lines, name):
        for ln in lines:
            if f'@"{name}"' in ln or f'@{name}(' in ln:
                return ln
        self.fail(f'no declaration line found for {name!r} in IR')

    def test_char_param_gets_signext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(c: CHAR): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('signext', ln, msg=f'IR line: {ln}')

    def test_integer_param_gets_signext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: INTEGER): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('signext', ln, msg=f'IR line: {ln}')

    def test_word_param_gets_zeroext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(w: WORD): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('zeroext', ln, msg=f'IR line: {ln}')

    def test_boolean_param_gets_zeroext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(b: BOOLEAN): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('zeroext', ln, msg=f'IR line: {ln}')

    def test_cshort_param_gets_signext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CSHORT): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('signext', ln, msg=f'IR line: {ln}')

    def test_cchar_param_gets_signext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(c: CCHAR): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('signext', ln, msg=f'IR line: {ln}')

    def test_char_return_gets_signext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION getc(fd: CINT): CHAR [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'getc')
        self.assertIn('signext i8', ln, msg=f'IR line: {ln}')

    def test_integer_return_gets_signext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CINT): INTEGER [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('signext i16', ln, msg=f'IR line: {ln}')

    def test_word_return_gets_zeroext(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CINT): WORD [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertIn('zeroext i16', ln, msg=f'IR line: {ln}')

    def test_cint_param_no_extension_attr(self):
        """32-bit types do not need extension attrs."""
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CINT): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertNotIn('signext', ln, msg=f'IR line: {ln}')
        self.assertNotIn('zeroext', ln, msg=f'IR line: {ln}')

    def test_clong_no_extension_attr(self):
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: CLONG): CLONG [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'f')
        self.assertNotIn('signext', ln)
        self.assertNotIn('zeroext', ln)

    def test_procedure_emits_void_return(self):
        """[C] EXTERN procedures declare void, not i32.  The i32 in the param is fine."""
        lines = self._ir_lines(
            "PROGRAM P(output);\n"
            "PROCEDURE cnoise [C]; EXTERN;\n"
            "BEGIN END.")
        ln = self._decl_line(lines, 'cnoise')
        # Return type should be void; the old internal convention was i32.
        self.assertIn('void', ln, msg=f'IR line: {ln}')
        self.assertNotIn('i32', ln, msg=f'IR line: {ln}')


@requires_exe
class TestPhase4BuildAndRun(unittest.TestCase):
    """Phase 4 end-to-end: signext/zeroext/void affect actual call correctness.

    These tests construct situations where wrong extension would produce bad
    results: negative char returns, unsigned short manipulation, and a void
    procedure that must not clobber a register the caller expects clean.
    """

    def _run(self, files, compile_pairs, link_ir, exe):
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=compile_pairs,
            link_ir_relpaths=link_ir,
            exe_name=exe,
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def test_negative_char_return_correct(self):
        """C function returning a negative char (-1) must read as -1 in Pascal.

        Without signext on the return, the high bits of eax are undefined; a
        subsequent ORD() or comparison could read garbage.  With signext the
        i8 -1 is guaranteed sign-extended so ORD gives 255 (unsigned) and the
        comparison works.
        """
        files = {
            'nc.pas': (
                "PROGRAM P(output);\n"
                "FUNCTION neg_char(dummy: CINT): CHAR [C]; EXTERN;\n"
                "VAR c: CHAR;\n"
                "BEGIN\n"
                "  c := neg_char(0);\n"
                "  IF ORD(c) = 255 THEN WRITELN('ok') ELSE WRITELN('bad')\n"
                "END."),
            'ncimpl.c': (
                "#include <stdint.h>\n"
                "char neg_char(int32_t dummy) { return (char)-1; }\n"),
        }
        lines = self._run(files, [('nc.pas', 'nc.ll')], ['nc.ll', 'ncimpl.c'], 'p4-negchar')
        self.assertEqual(lines, ['ok'])

    def test_void_procedure_does_not_corrupt(self):
        """A void C procedure called in the middle of a computation must not
        corrupt result registers.  This catches any ABI mismatch from i32 vs
        void on the call.
        """
        files = {
            'vp.pas': (
                "PROGRAM P(output);\n"
                "PROCEDURE do_nothing(x: CINT) [C]; EXTERN;\n"
                "FUNCTION add(a: CINT; b: CINT): CINT [C]; EXTERN;\n"
                "VAR r: CINT;\n"
                "BEGIN\n"
                "  do_nothing(99);\n"
                "  r := add(10, 32);\n"
                "  WRITELN(r)\n"
                "END."),
            'vpimpl.c': (
                "#include <stdint.h>\n"
                "void do_nothing(int32_t x) { (void)x; }\n"
                "int32_t add(int32_t a, int32_t b) { return a + b; }\n"),
        }
        lines = self._run(files, [('vp.pas', 'vp.ll')], ['vp.ll', 'vpimpl.c'], 'p4-void')
        self.assertEqual(lines, ['42'])

    def test_word_param_zeroext_correct(self):
        """A WORD (unsigned 16-bit) parameter must arrive at the callee with the
        upper bits zeroed (zeroext), not sign-extended (which would be wrong for
        values >= 0x8000).
        """
        files = {
            'wp.pas': (
                "PROGRAM P(output);\n"
                "FUNCTION pass_word(w: WORD): CINT [C]; EXTERN;\n"
                "VAR w: WORD; r: CINT;\n"
                "BEGIN\n"
                "  w := 65535;  { 0xFFFF -- would sign-extend to -1 without zeroext }\n"
                "  r := pass_word(w);\n"
                "  WRITELN(r)\n"
                "END."),
            'wpimpl.c': (
                "#include <stdint.h>\n"
                "/* Callee receives uint16_t -- upper bits must be zero */\n"
                "int32_t pass_word(uint16_t w) { return (int32_t)w; }\n"),
        }
        lines = self._run(files, [('wp.pas', 'wp.ll')], ['wp.ll', 'wpimpl.c'], 'p4-word')
        self.assertEqual(lines, ['65535'])


if __name__ == '__main__':
    unittest.main()
