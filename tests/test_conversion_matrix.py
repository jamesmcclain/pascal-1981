"""Acceptance matrix for implicit type conversions.

This is a *change-detector*, not a correctness oracle.  It pins the compiler's
accept/reject verdict for every interesting (source -> destination) type pair
across the three contexts where an implicit conversion can happen:

    * assignment      a := b
    * argument pass   f(b)            (b flows into a value parameter)
    * arithmetic mix  a := b <op> c

For each pair we also distinguish a VARIABLE source from a CONSTANT source,
because IBM Pascal 2.0 treats them differently:

    "INTEGER type constants change to WORD type if necessary, but not INTEGER
     variables."  (manual, Elementary Types, p.6-5)

So `w := 5` is legal but `w := i` is not, and a regression net that ignores the
const/var split would miss exactly the rule we care about.

When a conversion rule is tightened, the rows whose verdict flips are the spec
change -- update them in the same commit and the diff *is* the documentation.

State pinned here: the WORD/INTEGER strictness pass.  A non-constant INTEGER is
NOT assignment compatible with WORD (use WRD); WORD -> INTEGER needs ORD; a
WORD/INTEGER expression mix warns by default and errors under -f strict-word-int;
INTEGER constants are exempt everywhere.
"""

import unittest

from tests.support import typecheck_source

ACCEPT = "ACCEPT"
REJECT = "REJECT"

VINTAGE = None  # faithful 1981 dialect: no flags
WIDE = {"wide-integers": True, "wide-reals": True}
STRICT = {"strict-word-int": True}


def verdict(src, features=VINTAGE):
    """Return ACCEPT/REJECT for a source snippet under the given features."""
    try:
        result = typecheck_source(src, features=features)
    except Exception:
        return REJECT
    return ACCEPT if result.success else REJECT


def has_word_int_warning(src, features=VINTAGE):
    result = typecheck_source(src, features=features)
    return any("WORD and INTEGER" in w.message for w in getattr(result, "warnings", []))


# --- program templates ------------------------------------------------------

def assign_prog(dst, src_decl, src_expr):
    return f"PROGRAM P; VAR d: {dst}; {src_decl} BEGIN d := {src_expr} END."


def arg_prog(param_ty, src_decl, src_expr, ret_ty, ret_sink):
    return (f"PROGRAM P; FUNCTION f(x: {param_ty}): {ret_ty}; BEGIN f := x END; "
            f"VAR s: {ret_sink}; {src_decl} BEGIN s := f({src_expr}) END.")


def arith_prog(dst, a_decl, a_expr, b_decl, b_expr, op="+"):
    return (f"PROGRAM P; VAR d: {dst}; {a_decl} {b_decl} "
            f"BEGIN d := {a_expr} {op} {b_expr} END.")


class TestAssignmentMatrix(unittest.TestCase):
    """a := b  (and constant-source variants)."""

    CASES = [
        # (name, dst, src_decl, src_expr, features, expect, note)
        # --- INTEGER <-> REAL : manual blesses INTEGER -> REAL implicitly ---
        ("int_var_to_real",   "REAL",    "i: INTEGER;", "i", VINTAGE, ACCEPT, "manual: INTEGER->REAL ok"),
        ("real_var_to_int",   "INTEGER", "r: REAL;",    "r", VINTAGE, REJECT, "narrowing rejected"),

        # --- INTEGER -> WORD : constants change, variables do NOT ---
        ("int_literal_to_word", "WORD", "",            "5",   VINTAGE, ACCEPT, "manual: const changes to WORD"),
        ("named_const_to_word", "WORD", "",            "5",   VINTAGE, ACCEPT, "manual: const changes to WORD"),
        ("int_var_to_word",   "WORD", "i: INTEGER;", "i",   VINTAGE, REJECT,
         "manual: INTEGER variable NOT assignable to WORD; use WRD(i)"),
        ("int_expr_to_word",  "WORD", "i: INTEGER;", "i+i", VINTAGE, REJECT,
         "non-constant INTEGER expr requires WRD()"),
        ("wrd_int_to_word",   "WORD", "i: INTEGER;", "WRD(i)", VINTAGE, ACCEPT, "explicit WRD"),

        # --- WORD -> INTEGER : not assignment compatible (need ORD) ---
        ("word_var_to_int",   "INTEGER", "w: WORD;", "w",      VINTAGE, REJECT, "manual: need ORD(w)"),
        ("ord_word_to_int",   "INTEGER", "w: WORD;", "ORD(w)", VINTAGE, ACCEPT, "explicit ORD"),

        # --- CHAR/BOOLEAN need explicit ORD/CHR ---
        ("char_var_to_int",   "INTEGER", "c: CHAR;",    "c",      VINTAGE, REJECT, "manual: need ORD(c)"),
        ("ord_char_to_int",   "INTEGER", "c: CHAR;",    "ORD(c)", VINTAGE, ACCEPT, "explicit ORD"),
        ("int_var_to_char",   "CHAR",    "i: INTEGER;", "i",      VINTAGE, REJECT, "manual: need CHR(i)"),
        ("chr_int_to_char",   "CHAR",    "i: INTEGER;", "CHR(i)", VINTAGE, ACCEPT, "explicit CHR"),

        # --- INTEGER literal range: -32768 is invalid (manual p.6-5) ---
        ("int_minus_maxint",  "INTEGER", "", "-32767", VINTAGE, ACCEPT, "-MAXINT is valid"),
        ("int_minus_32768",   "INTEGER", "", "-32768", VINTAGE, REJECT, "manual: -32768 not a valid INTEGER"),
        ("int_over_maxint",   "INTEGER", "", "32768",  VINTAGE, REJECT, "> MAXINT"),

        # --- wide-integer extension family (extension territory, not vintage) ---
        ("int_var_to_int32",  "INTEGER32", "i: INTEGER;",   "i", WIDE, ACCEPT, "ext: signed widening (lowers as SEXT)"),
        ("int32_var_to_int",  "INTEGER",   "j: INTEGER32;", "j", WIDE, REJECT, "narrowing rejected"),
        ("int32_to_int64",    "INTEGER64", "j: INTEGER32;", "j", WIDE, ACCEPT, "ext: widening implicit"),
        ("word_to_int32",     "INTEGER32", "w: WORD;",      "w", WIDE, ACCEPT, "ext: WORD widening (lowers as ZEXT)"),
        ("real32_to_real",    "REAL",      "f: REAL32;",    "f", WIDE, ACCEPT, "ext: f32->f64 widening"),
        ("real_to_real32",    "REAL32",    "r: REAL;",      "r", WIDE, REJECT, "narrowing rejected"),
    ]

    def test_assignment(self):
        for name, dst, src_decl, src_expr, feats, expect, note in self.CASES:
            with self.subTest(case=name, note=note):
                got = verdict(assign_prog(dst, src_decl, src_expr), feats)
                self.assertEqual(got, expect, f"{name}: {note}")


class TestArgumentMatrix(unittest.TestCase):
    """f(b) where b flows into a value parameter."""

    CASES = [
        # (name, param_ty, src_decl, src_expr, ret_ty, ret_sink, features, expect, note)
        ("int_var_arg_to_int32", "INTEGER32", "i: INTEGER;", "i", "INTEGER32", "INTEGER32", WIDE, ACCEPT,
         "arg widening implicit; lowering SEXTs the signed source (Finding 1)"),
        ("int32_arg_to_int_narrow", "INTEGER", "j: INTEGER32;", "j", "INTEGER", "INTEGER", WIDE, REJECT,
         "narrowing arg rejected"),
        ("int_var_arg_to_word", "WORD", "i: INTEGER;", "i", "WORD", "WORD", VINTAGE, REJECT,
         "INTEGER variable -> WORD param needs WRD(i)"),
        ("int_const_arg_to_word", "WORD", "", "5", "WORD", "WORD", VINTAGE, ACCEPT,
         "constant INTEGER -> WORD param stays implicit (manual)"),
        ("word_arg_to_int", "INTEGER", "w: WORD;", "w", "INTEGER", "INTEGER", VINTAGE, REJECT,
         "WORD -> INTEGER param needs ORD(w)"),
        ("wrd_arg_to_word", "WORD", "i: INTEGER;", "WRD(i)", "WORD", "WORD", VINTAGE, ACCEPT,
         "explicit WRD at boundary"),
    ]

    def test_argument(self):
        for name, p, sd, se, rt, rs, feats, expect, note in self.CASES:
            with self.subTest(case=name, note=note):
                got = verdict(arg_prog(p, sd, se, rt, rs), feats)
                self.assertEqual(got, expect, f"{name}: {note}")


class TestArithmeticMatrix(unittest.TestCase):
    """a <op> b mixing."""

    CASES = [
        # (name, dst, a_decl, a_expr, b_decl, b_expr, op, features, expect, note)
        ("int_plus_real", "REAL", "i: INTEGER;", "i", "r: REAL;", "r", "+", VINTAGE, ACCEPT,
         "manual: INTEGER widens to REAL in expr"),
        ("word_plus_int_var_default", "WORD", "w: WORD;", "w", "i: INTEGER;", "i", "+", VINTAGE, ACCEPT,
         "WORD+INTEGER-var: warning by default, mix resolves to WORD so it compiles"),
        ("word_plus_int_var_strict", "WORD", "w: WORD;", "w", "i: INTEGER;", "i", "+", STRICT, REJECT,
         "-f strict-word-int promotes the mix warning to an error"),
        ("word_plus_int_const", "WORD", "w: WORD;", "w", "", "1", "+", VINTAGE, ACCEPT,
         "manual: mix allowed when the INTEGER side is a constant"),
        ("word_plus_int_const_strict", "WORD", "w: WORD;", "w", "", "1", "+", STRICT, ACCEPT,
         "constant exemption holds even under strict-word-int"),
        ("int_plus_int32", "INTEGER32", "i: INTEGER;", "i", "j: INTEGER32;", "j", "+", WIDE, ACCEPT,
         "ext: rank promotion to INTEGER32"),
        ("real_plus_int", "REAL", "r: REAL;", "r", "i: INTEGER;", "i", "*", VINTAGE, ACCEPT,
         "manual: INTEGER widens to REAL"),
    ]

    def test_arithmetic(self):
        for name, dst, ad, ae, bd, be, op, feats, expect, note in self.CASES:
            with self.subTest(case=name, note=note):
                got = verdict(arith_prog(dst, ad, ae, bd, be, op), feats)
                self.assertEqual(got, expect, f"{name}: {note}")

    def test_word_int_mix_emits_warning_by_default(self):
        # The default-dialect mix compiles but must carry the vintage warning.
        src = arith_prog("WORD", "w: WORD;", "w", "i: INTEGER;", "i", "+")
        self.assertTrue(has_word_int_warning(src), "expected a WORD/INTEGER mix warning")

    def test_word_int_const_mix_is_clean(self):
        src = arith_prog("WORD", "w: WORD;", "w", "", "1", "+")
        self.assertFalse(has_word_int_warning(src), "constant INTEGER mix should be clean")


class TestManualKnownGaps(unittest.TestCase):
    """Direct checks of individual manual rules; tracks remaining gaps explicitly."""

    def test_odd_accepts_word_is_a_known_gap(self):
        # Manual: "the ODD function for INTEGER and WORD values".  ODD(WORD) is
        # still REJECTED.  This is a KNOWN CONFORMANCE GAP, tracked in
        # docs/followups.md -- pinned here so a future fix flips a known row
        # rather than surprising us, NOT an endorsement of the current behavior.
        src = "PROGRAM P; VAR w: WORD; b: BOOLEAN; BEGIN b := ODD(w) END."
        self.assertEqual(verdict(src), REJECT,
                         "KNOWN GAP: ODD(WORD) should be accepted per the manual")


if __name__ == "__main__":
    unittest.main()
