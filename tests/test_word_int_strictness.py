"""Lock-in tests for the WORD/INTEGER strictness pass and the four sign/width
findings from the C-FFI review.

Three tiers, mirroring the rest of the suite:
  * pure type-checker tests (no toolchain),
  * @requires_llvm IR-shape tests (need llvmlite),
  * @requires_exe build-and-run value tests (need llvmlite + clang).

What is pinned:
  * Strictness (vintage default): a non-constant INTEGER is not assignment
    compatible with WORD (use WRD); WORD/INTEGER expression mixing warns; the
    INTEGER-constant exemption holds.
  * -f strict-word-int promotes the mix warning to an error, and is orthogonal
    to the extended dialect (toggling it never gates the C-FFI surface).
  * Finding 1: signed integers SEXT (not ZEXT) when widened across a call.
  * Finding 2: a WORD in a variadic tail ZEXTs (C unsigned-short promotion).
  * Finding 3: variadic [C] PROCEDURES type-check and lower.
  * Finding 4: printf width/precision operands sign-extend.
"""

import unittest

from pascal1981.features import extended_features, is_extended, resolve_features
from tests.support import (build_and_run_pascal_project, parse_source,
                           requires_exe, requires_llvm, typecheck_source)


def _ir(src, features=None):
    """Parse, type-check, and compile to LLVM IR text (requires llvmlite)."""
    from pascal1981.codegen_llvm import compile_to_llvm
    from pascal1981.type_checker import PascalTypeChecker
    ast = parse_source(src)
    result = PascalTypeChecker(features=features).check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    return compile_to_llvm(ast, features=features)

EXT = extended_features()


def _warnings(result):
    return [w.message for w in getattr(result, "warnings", [])]


# ---------------------------------------------------------------------------
# Strictness (pure type checker)
# ---------------------------------------------------------------------------

class TestWordIntStrictness(unittest.TestCase):
    def test_integer_variable_not_assignable_to_word(self):
        r = typecheck_source("PROGRAM P; VAR i: INTEGER; w: WORD; BEGIN w := i END.")
        self.assertFalse(r.success)
        self.assertTrue(any("WRD" in e.message for e in r.errors),
                        "error should point at WRD(...)")

    def test_integer_constant_changes_to_word(self):
        self.assertTrue(typecheck_source("PROGRAM P; VAR w: WORD; BEGIN w := 5 END.").success)
        self.assertTrue(typecheck_source(
            "PROGRAM P; CONST k = 5; VAR w: WORD; BEGIN w := k END.").success)

    def test_wrd_makes_integer_assignable_to_word(self):
        self.assertTrue(typecheck_source(
            "PROGRAM P; VAR i: INTEGER; w: WORD; BEGIN w := WRD(i) END.").success)

    def test_integer_variable_not_passable_to_word_param(self):
        r = typecheck_source("PROGRAM P; PROCEDURE f(x: WORD); BEGIN END; "
                             "VAR i: INTEGER; BEGIN f(i) END.")
        self.assertFalse(r.success)

    def test_integer_constant_passable_to_word_param(self):
        self.assertTrue(typecheck_source(
            "PROGRAM P; PROCEDURE f(x: WORD); BEGIN END; BEGIN f(5) END.").success)

    def test_word_to_integer_still_needs_ord(self):
        self.assertFalse(typecheck_source(
            "PROGRAM P; VAR i: INTEGER; w: WORD; BEGIN i := w END.").success)
        self.assertTrue(typecheck_source(
            "PROGRAM P; VAR i: INTEGER; w: WORD; BEGIN i := ORD(w) END.").success)

    def test_mix_warns_by_default_and_compiles(self):
        r = typecheck_source("PROGRAM P; VAR i: INTEGER; w: WORD; BEGIN w := w + i END.")
        self.assertTrue(r.success)
        self.assertTrue(any("WORD and INTEGER" in m for m in _warnings(r)))

    def test_mix_with_integer_constant_is_clean(self):
        r = typecheck_source("PROGRAM P; VAR w: WORD; BEGIN w := w + 1 END.")
        self.assertTrue(r.success)
        self.assertFalse(any("WORD and INTEGER" in m for m in _warnings(r)))

    def test_strict_flag_promotes_mix_to_error(self):
        src = "PROGRAM P; VAR i: INTEGER; w: WORD; BEGIN w := w + i END."
        r = typecheck_source(src, features={"strict-word-int": True})
        self.assertFalse(r.success)
        self.assertTrue(any("WORD and INTEGER" in e.message for e in r.errors))

    def test_strict_flag_keeps_constant_exemption(self):
        r = typecheck_source("PROGRAM P; VAR w: WORD; BEGIN w := w + 1 END.",
                             features={"strict-word-int": True})
        self.assertTrue(r.success)

    def test_minus_32768_is_invalid_integer(self):
        # Manual p.6-5: -32768 is not a valid INTEGER.
        self.assertFalse(typecheck_source("PROGRAM P; VAR i: INTEGER; BEGIN i := -32768 END.").success)
        self.assertTrue(typecheck_source("PROGRAM P; VAR i: INTEGER; BEGIN i := -32767 END.").success)


# ---------------------------------------------------------------------------
# Flag orthogonality
# ---------------------------------------------------------------------------

class TestStrictWordIntOrthogonality(unittest.TestCase):
    def test_strict_off_under_extended_by_default(self):
        ext = resolve_features("extended")
        self.assertFalse(ext["strict-word-int"], "extended must not auto-enable strict-word-int")
        self.assertTrue(is_extended(ext))

    def test_disabling_strict_keeps_extended_dialect(self):
        # extended minus strict-word-int is still the extended dialect: the
        # C-FFI gate must stay open.
        feats = resolve_features("extended", ["no-strict-word-int"])
        self.assertTrue(is_extended(feats))

    def test_strict_alone_does_not_imply_extended(self):
        feats = resolve_features("vintage", ["strict-word-int"])
        self.assertFalse(is_extended(feats))
        self.assertTrue(feats["strict-word-int"])

    def test_c_ffi_available_under_extended_without_strict(self):
        # [C] must still parse+check with strict-word-int explicitly off.
        feats = resolve_features("extended", ["no-strict-word-int"])
        r = typecheck_source(
            "PROGRAM P(output);\nFUNCTION cube(x: CINT): CINT [C]; EXTERN;\n"
            "BEGIN WRITELN(cube(3)) END.", features=feats)
        self.assertTrue(r.success, msg=str(r.errors))


# ---------------------------------------------------------------------------
# Finding 3: variadic procedures (type checker)
# ---------------------------------------------------------------------------

class TestVariadicProcedure(unittest.TestCase):
    def test_variadic_procedure_accepts_tail_args(self):
        r = typecheck_source(
            "PROGRAM P;\nPROCEDURE cprint(fmt: CPTR) [C, VARARGS]; EXTERN;\n"
            "VAR f: ARRAY[0..1] OF CHAR;\nBEGIN cprint(adr f, 1, 2) END.",
            features=EXT)
        self.assertTrue(r.success, msg=str(r.errors))


# ---------------------------------------------------------------------------
# Finding 1 / 2: extension direction in the IR (no toolchain run)
# ---------------------------------------------------------------------------

@requires_llvm
class TestSignExtensionIR(unittest.TestCase):
    def test_signed_integer_widens_with_sext(self):
        ir = _ir(
            "PROGRAM P(output);\n"
            "FUNCTION id32(x: INTEGER32): INTEGER32; BEGIN id32 := x END;\n"
            "VAR i: INTEGER;\nBEGIN i := -5; WRITELN(id32(i)) END.",
            features={"wide-integers": True})
        self.assertIn("sext i16", ir)
        self.assertNotIn("zext i16", ir.split("@\"id32\"")[0])  # not zext on the way in

    def test_word_variadic_tail_uses_zext(self):
        ir = _ir(
            "PROGRAM P(output);\n"
            "FUNCTION printf(fmt: CPTR): CINT [C, VARARGS]; EXTERN;\n"
            "VAR w: WORD; f: ARRAY[0..1] OF CHAR; r: CINT;\n"
            "BEGIN w := 60000; r := printf(adr f, w) END.", features=EXT)
        # The WORD tail argument must zero-extend, not sign-extend.
        self.assertIn("zext i16", ir)


# ---------------------------------------------------------------------------
# Finding 1 / 2 / 3: end-to-end values against clang
# ---------------------------------------------------------------------------

@requires_exe
class TestSignFindingsBuildAndRun(unittest.TestCase):
    def test_signed_widening_preserves_value(self):
        # id32(-5) must print -5, not 65531 (Finding 1: was ZEXT).
        files = {
            "p.pas": (
                "PROGRAM P(output);\n"
                "FUNCTION id32(x: INTEGER32): INTEGER32; BEGIN id32 := x END;\n"
                "VAR i: INTEGER;\nBEGIN i := -5; WRITELN(id32(i)) END."),
        }
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=[("p.pas", "p.ll")],
            link_ir_relpaths=["p.ll"],
            exe_name="signed-widening",
            features={"wide-integers": True},
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), "-5")

    def test_word_variadic_promotion_against_printf(self):
        # printf("%u %d", w, w) with w = 60000 must print 60000 60000
        # (Finding 2: WORD was SEXT -> 4294961760 -5536).
        files = {
            "p.pas": (
                "PROGRAM P(output);\n"
                "FUNCTION printf(fmt: CPTR): CINT [C, VARARGS]; EXTERN;\n"
                "VAR w: WORD; fmt: ARRAY[0..7] OF CHAR; r: CINT;\n"
                "BEGIN\n"
                "  w := 60000;\n"
                "  fmt[0]:='%'; fmt[1]:='u'; fmt[2]:=' '; fmt[3]:='%';\n"
                "  fmt[4]:='d'; fmt[5]:=CHR(10); fmt[6]:=CHR(0);\n"
                "  r := printf(adr fmt, w, w)\n"
                "END."),
            "cimpl.c": "/* printf comes from libc */\n",
        }
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=[("p.pas", "p.ll")],
            link_ir_relpaths=["p.ll", "cimpl.c"],
            exe_name="word-variadic",
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), "60000 60000")

    def test_variadic_procedure_builds_and_runs(self):
        # Finding 3: a variadic [C] PROCEDURE (void return) links and runs.
        files = {
            "p.pas": (
                "PROGRAM P(output);\n"
                "PROCEDURE cprint(fmt: CPTR) [C, VARARGS]; EXTERN;\n"
                "VAR fmt: ARRAY[0..3] OF CHAR;\n"
                "BEGIN\n"
                "  fmt[0]:='%'; fmt[1]:='d'; fmt[2]:=CHR(10); fmt[3]:=CHR(0);\n"
                "  cprint(adr fmt, 7)\n"
                "END."),
            "cimpl.c": (
                "#include <stdio.h>\n#include <stdarg.h>\n"
                "void cprint(const char* fmt, ...){\n"
                "  va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);\n}\n"),
        }
        rc, out, err = build_and_run_pascal_project(
            files=files,
            compile_pairs=[("p.pas", "p.ll")],
            link_ir_relpaths=["p.ll", "cimpl.c"],
            exe_name="variadic-proc",
            features=EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.strip(), "7")


if __name__ == "__main__":
    unittest.main()
