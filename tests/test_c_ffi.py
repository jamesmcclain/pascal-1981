"""C-FFI Phase 0 (foreign-ABI diagnostics) and Phase 1 (the [C] attribute and the
C-ABI fixed-width type aliases).

Layered like the rest of the suite: parser/typecheck cases need no toolchain;
the build-and-run cases are decorated with @requires_exe and auto-skip without
llvmlite/clang. See docs/c-abi-foreign-functions.md.
"""

import unittest

from pascal1981.parser import ParserError

from tests.support import build_and_run_pascal_project, parse_source, requires_exe, typecheck_source


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
    """Phase 1: the C-ABI fixed-width aliases resolve without -f wide-integers."""

    def test_scalar_aliases_typecheck_without_wide_integers(self):
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "FUNCTION cube(x: CINT): CINT [C]; EXTERN;\n"
            "FUNCTION addd(a: CDOUBLE; b: CDOUBLE): CDOUBLE [C]; EXTERN;\n"
            "FUNCTION len(s: CPTR): CSIZE_T [C]; EXTERN;\n"
            "VAR r: CINT;\n"
            "BEGIN r := cube(3) END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_user_type_shadows_alias(self):
        # A user TYPE named like an alias still wins (builtins are shadowable).
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "TYPE cint = BOOLEAN;\n"
            "VAR b: cint;\n"
            "BEGIN b := TRUE END.")
        self.assertTrue(result.success, msg=_errors(result))


class TestForeignAbiDiagnostics(unittest.TestCase):
    """Phase 0: by-value aggregates in foreign routines are rejected."""

    _POINT = "TYPE point = RECORD x: CINT; y: CINT END;\n"

    def test_byvalue_aggregate_param_rejected(self):
        result = typecheck_source(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(p: point): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('by-value aggregate parameter' in m for m in _errors(result)))

    def test_byvalue_aggregate_return_rejected(self):
        result = typecheck_source(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION mk(v: CINT): point [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('by-value aggregate return' in m for m in _errors(result)))

    def test_byvalue_string_param_rejected(self):
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "PROCEDURE g(s: STRING(20)); EXTERN;\n"
            "BEGIN END.")
        self.assertFalse(result.success)
        self.assertTrue(any('by-value aggregate parameter' in m for m in _errors(result)))

    def test_const_aggregate_param_accepted(self):
        result = typecheck_source(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(CONST p: point): CINT [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_var_aggregate_param_accepted(self):
        result = typecheck_source(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(VAR p: point): CINT; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_non_foreign_byvalue_aggregate_allowed(self):
        # The guard is scoped to EXTERN/EXTERNAL; ordinary routines are untouched.
        result = typecheck_source(
            "PROGRAM P(output);\n" + self._POINT +
            "FUNCTION sumpt(p: point): CINT;\n"
            "BEGIN sumpt := p.x + p.y END;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))

    def test_bare_integer_param_warns(self):
        result = typecheck_source(
            "PROGRAM P(output);\n"
            "FUNCTION f(x: INTEGER): INTEGER [C]; EXTERN;\n"
            "BEGIN END.")
        self.assertTrue(result.success, msg=_errors(result))
        self.assertTrue(any('16-bit INTEGER' in m for m in _warnings(result)))

    def test_cint_param_does_not_warn(self):
        result = typecheck_source(
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
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([ln.strip() for ln in out.splitlines() if ln.strip()], ['42'])


if __name__ == '__main__':
    unittest.main()
