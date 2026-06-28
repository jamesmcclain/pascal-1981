"""SIZEOF correctness, especially for named types.

Regression coverage for the long-standing bug where SIZEOF of a variable (or
type) whose name was a user TYPE or a C alias returned the 4-byte fallback:
named records, named arrays, named wide-int aliases, and CLONG all reported 4.
Records are now sized with alignment/tail padding (matching the C ABI layout),
so SIZEOF agrees with the marshaller and with C's sizeof.

The build-and-run cases are decorated with @requires_exe and auto-skip without
llvmlite/clang.
"""

import unittest

from pascal1981.features import extended_features
from tests.support import build_and_run_pascal_project, requires_exe

EXT = extended_features()


@requires_exe
class TestSizeofBuildAndRun(unittest.TestCase):
    def _sizes(self, decls, exprs, exe, features=None):
        """Compile a program that WRITELNs each SIZEOF expr; return ints."""
        body = '; '.join(f"WRITELN(SIZEOF({e}))" for e in exprs)
        src = f"PROGRAM P(output);\n{decls}\nBEGIN {body} END."
        rc, out, err = build_and_run_pascal_project(
            files={'m.pas': src},
            compile_pairs=[('m.pas', 'm.ll')],
            link_ir_relpaths=['m.ll'],
            exe_name=exe,
            features=features if features is not None else EXT,
        )
        self.assertEqual(rc, 0, msg=err)
        return [int(ln.strip()) for ln in out.splitlines() if ln.strip()]

    def test_named_record_variable_and_type(self):
        decls = ("TYPE rec = RECORD a: INTEGER32; b: INTEGER32; "
                 "c: ARRAY[0..63] OF CHAR END;\nVAR r: rec;")
        self.assertEqual(self._sizes(decls, ['r', 'rec'], 'sz-rec'), [72, 72])

    def test_padded_record_matches_c_layout(self):
        # char + int32: 1 byte, 3 pad, 4 -> 8 (a naive field sum gives 5).
        decls = "TYPE p = RECORD x: CHAR; y: INTEGER32 END;\nVAR v: p;"
        self.assertEqual(self._sizes(decls, ['v'], 'sz-pad'), [8])

    def test_nested_record(self):
        decls = ("TYPE inner = RECORD a: INTEGER32; b: INTEGER32; "
                 "c: ARRAY[0..63] OF CHAR END;\n"
                 "     outer = RECORD h: inner; n: INTEGER32 END;\nVAR v: outer;")
        self.assertEqual(self._sizes(decls, ['v'], 'sz-nest'), [76])

    def test_named_array_alias(self):
        decls = "TYPE arr = ARRAY[0..9] OF CHAR;\nVAR a: arr;"
        self.assertEqual(self._sizes(decls, ['a'], 'sz-arr'), [10])

    def test_named_wide_int_alias_and_clong(self):
        decls = "TYPE long = INTEGER64;\nVAR n: long; cl: CLONG;"
        self.assertEqual(self._sizes(decls, ['n', 'cl'], 'sz-long'), [8, 8])

    def test_scalars_and_inline_aggregates_unregressed(self):
        decls = ("VAR i: INTEGER; w: WORD; c: CHAR; r: REAL; b: BOOLEAN; "
                 "am: ADRMEM;\n"
                 "    inl: RECORD a: INTEGER32; b: INTEGER32 END;\n"
                 "    arr: ARRAY[0..9] OF CHAR;")
        got = self._sizes(decls, ['i', 'w', 'c', 'r', 'b', 'am', 'inl', 'arr'],
                          'sz-scalars')
        self.assertEqual(got, [2, 2, 1, 8, 1, 8, 8, 10])


if __name__ == '__main__':
    unittest.main()
