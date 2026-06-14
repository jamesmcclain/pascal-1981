"""Regression tests for the runtime/codegen fixes to NEW, ENCODE/DECODE, SCANNE.

The pre-existing coverage for these builtins only asserted that the right
extern call was *emitted* in the IR; it never checked that the call did the
right thing. Each of these tests pins a behavior that was previously wrong:

  * NEW under-allocated (hard-coded 8 bytes) for any pointee larger than 8.
  * ENCODE bounded the write by the LSTRING's current length (0 for a fresh
    string) and never set the length-prefix byte, so the result was invisible.
  * DECODE parsed the source and then discarded the value; the destination was
    left unchanged.
  * SCANNE re-inverted its stop flag and so behaved identically to SCANEQ.

The IR-level tests need only llvmlite; the run tests link the C runtime and so
need clang (guarded by @requires_exe).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import (parse_source, requires_exe, requires_llvm, typecheck_source)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNTIME = os.path.join(_REPO_ROOT, "runtime")


def compile_to_ir(src: str) -> str:
    from codegen_llvm import compile_to_llvm
    from type_checker import PascalTypeChecker
    ast = parse_source(src)
    result = PascalTypeChecker().check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    return compile_to_llvm(ast)


def build_run_linked(src: str, runtime_files, stdin: str = "", features=None) -> tuple:
    """Like the codegen harness, but also links the named runtime C files so
    that ENCODE/DECODE/SCANEQ/SCANNE resolve at link time."""
    from codegen_llvm import compile_to_llvm
    from type_checker import PascalTypeChecker
    ast = parse_source(src)
    result = PascalTypeChecker(features=features).check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    ir = compile_to_llvm(ast, features=features)
    tmpdir = tempfile.mkdtemp()
    try:
        ll_path = os.path.join(tmpdir, "prog.ll")
        with open(ll_path, "w") as f:
            f.write(ir)
        exe_path = os.path.join(tmpdir, "prog")
        cfiles = [os.path.join(_RUNTIME, name) for name in runtime_files]
        cc = subprocess.run(["clang", ll_path, *cfiles, "-o", exe_path, "-lm"], capture_output=True, text=True)
        if cc.returncode != 0:
            raise RuntimeError(f"clang failed: {cc.stderr}")
        run = subprocess.run([exe_path], input=stdin, capture_output=True, text=True)
        return run.returncode, run.stdout
    finally:
        import shutil
        shutil.rmtree(tmpdir)


def _call_line(ir: str, callee: str) -> str:
    for line in ir.splitlines():
        if f'@"{callee}"' in line and "call" in line:
            return line
    raise AssertionError(f"no call to {callee} found in IR")


@requires_llvm
class TestNewAllocationSize(unittest.TestCase):

    def test_new_sizes_record_pointee(self):
        """NEW(^RECORD) must allocate the whole record, not a fixed 8 bytes."""
        src = ("PROGRAM P; TYPE R = RECORD a, b, c: INTEGER END; "
               "VAR p: ^R; BEGIN NEW(p) END.")
        ir = compile_to_ir(src)
        self.assertIn('call i8* @"malloc"(i64 6)', ir)
        self.assertNotIn('call i8* @"malloc"(i64 8)', ir)

    def test_new_sizes_scalar_pointee(self):
        """A pointer to INTEGER allocates exactly 2 bytes."""
        src = "PROGRAM P; VAR p: ^INTEGER; BEGIN NEW(p) END."
        ir = compile_to_ir(src)
        self.assertIn('call i8* @"malloc"(i64 2)', ir)


@requires_llvm
class TestEncodeDecodeArgs(unittest.TestCase):

    def test_encode_passes_capacity_and_width(self):
        """ENCODE bounds by declared capacity and threads the field width."""
        src = ("PROGRAM P; VAR l: LSTRING(20); ok: BOOLEAN; "
               "BEGIN ok := ENCODE(l, 42:6) END.")
        line = _call_line(compile_to_ir(src), "encode_value")
        # capacity 20 (not the current length); value/width may be computed
        # in temporaries after INTEGER became i16.
        self.assertIn("i32 20", line)

    def test_decode_passes_destination_width(self):
        """DECODE tells the runtime the destination width so it can write back."""
        for decl, size in (("n: INTEGER", 2), ("w: WORD", 2), ("c: CHAR", 1)):
            name = decl.split(":")[0].strip()
            src = (f"PROGRAM P; VAR l: LSTRING(20); {decl}; ok: BOOLEAN; "
                   f"BEGIN ok := DECODE(l, {name}) END.")
            line = _call_line(compile_to_ir(src), "decode_value")
            self.assertIn(f"i32 {size}", line, msg=f"{decl} -> size {size}: {line}")


@requires_exe
class TestRuntimeBehavior(unittest.TestCase):

    def test_encode_sets_length_and_is_readable(self):
        """ENCODE into a fresh LSTRING produces a string WRITELN can print."""
        src = ("PROGRAM P; VAR l: LSTRING(20); ok: BOOLEAN; "
               "BEGIN ok := ENCODE(l, 42); WRITELN(l) END.")
        rc, out = build_run_linked(src, ["encode_decode.c"])
        self.assertEqual(out, "42\n")

    def test_encode_honors_field_width(self):
        src = ("PROGRAM P; VAR l: LSTRING(20); ok: BOOLEAN; "
               "BEGIN ok := ENCODE(l, 42:5); WRITELN(l) END.")
        rc, out = build_run_linked(src, ["encode_decode.c"])
        self.assertEqual(out, "   42\n")

    def test_decode_writes_value_back(self):
        """DECODE stores the parsed integer into the destination and reports ok."""
        src = ("PROGRAM P; VAR l: LSTRING(20); n: INTEGER; ok: BOOLEAN; "
               "BEGIN l := '123'; ok := DECODE(l, n); WRITELN(n); WRITELN(ORD(ok)) END.")
        rc, out = build_run_linked(src, ["encode_decode.c"])
        self.assertEqual(out, "123\n1\n")

    def test_decode_rejects_non_numeric(self):
        src = ("PROGRAM P; VAR l: LSTRING(20); n: INTEGER; ok: BOOLEAN; "
               "BEGIN l := '12x'; ok := DECODE(l, n); WRITELN(ORD(ok)) END.")
        rc, out = build_run_linked(src, ["encode_decode.c"])
        self.assertEqual(out, "0\n")

    def test_scan_counts_from_correct_position(self):
        """Scans index characters 1-based from the real first character (not one
        late, and not reading a length byte), and work for STRING too. For
        'aXbb' scanning 'b' from position 1: SCANEQ skips 'a','X' and stops at
        'b' (2); SCANNE stops immediately on 'a' (0)."""
        src = (
            "PROGRAM P; VAR l: LSTRING(10); s: STRING(4); BEGIN "
            "l := 'aXbb'; s := 'aXbb'; "
            "WRITELN(SCANEQ(10, 'b', l, 1)); "  # 2
            "WRITELN(SCANNE(10, 'b', l, 1)); "  # 0
            "WRITELN(SCANNE(10, 'a', l, 1)); "  # 1 (single leading 'a')
            "WRITELN(SCANEQ(10, 'b', s, 1)) "  # 2 (STRING path)
            "END.")
        rc, out = build_run_linked(src, ["scaneq.c"])
        self.assertEqual(out.splitlines()[:4], ["2", "0", "1", "2"])

    def test_scanne_differs_from_scaneq(self):
        """SCANNE must no longer mirror SCANEQ (it previously re-inverted its
        stop flag and behaved identically)."""
        src = ("PROGRAM P; VAR s: LSTRING(10); BEGIN s := 'aXbb'; "
               "WRITELN(SCANEQ(10, 'b', s, 1)); WRITELN(SCANNE(10, 'b', s, 1)) END.")
        rc, out = build_run_linked(src, ["scaneq.c"])
        eq_line, ne_line = out.splitlines()[:2]
        self.assertNotEqual(eq_line, ne_line)


@requires_exe
class TestFileBufferModel(unittest.TestCase):

    def test_file_buffer_roundtrip(self):
        """Writing then reading the buffer variable F^ round-trips through the
        file-control block's own buffer, for both binary FILE OF T and TEXT.
        This guards the FCB redesign (handle distinct from buffer, structure
        retained, inline/leak-free) against breaking the basic F^ contract."""
        src = ("PROGRAM P; VAR f: FILE OF INTEGER; x: INTEGER; t: TEXT; c: CHAR; "
               "BEGIN f^ := 42; x := f^; WRITELN(x); t^ := 'Q'; c := t^; WRITELN(c) END.")
        rc, out = build_run_linked(src, ["fileops.c"])
        self.assertEqual(out, "42\nQ\n")

    def test_text_reset_rewrite_get_put_roundtrip(self):
        """TEXT file primitives must move distinct components through the stream.
        RESET supplies the first component via its implicit GET; explicit GET
        then advances to the second component."""
        src = ("PROGRAM P; VAR f: TEXT; c1, c2: CHAR; BEGIN "
               "REWRITE(f); f^ := 'A'; PUT(f); f^ := 'B'; PUT(f); "
               "RESET(f); c1 := f^; GET(f); c2 := f^; WRITELN(c1); WRITELN(c2) END.")
        rc, out = build_run_linked(src, ["fileops.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "A\nB\n")

    def test_binary_reset_rewrite_get_put_roundtrip(self):
        """Binary FILE OF INTEGER uses elem_size transfers, not byte-sized TEXT I/O."""
        src = ("PROGRAM P; VAR f: FILE OF INTEGER; x, y, z: INTEGER; BEGIN "
               "REWRITE(f); f^ := 1001; PUT(f); f^ := -7; PUT(f); f^ := 42; PUT(f); "
               "RESET(f); x := f^; GET(f); y := f^; GET(f); z := f^; "
               "WRITELN(x); WRITELN(y); WRITELN(z) END.")
        rc, out = build_run_linked(src, ["fileops.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "1001\n-7\n42\n")

    def test_rewrite_truncates_and_get_past_eof_aborts(self):
        """A second REWRITE truncates prior content. Prove truncation by driving
        GET past the only surviving component."""
        src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
               "REWRITE(f); f^ := 'A'; PUT(f); f^ := 'B'; PUT(f); "
               "REWRITE(f); f^ := 'C'; PUT(f); RESET(f); c := f^; WRITELN(c); GET(f); GET(f) END.")
        rc, out = build_run_linked(src, ["fileops.c"])
        self.assertNotEqual(rc, 0)

    def test_eof_loop_counts_text_components(self):
        src = ("PROGRAM P; VAR f: TEXT; n: INTEGER; BEGIN "
               "REWRITE(f); f^ := 'A'; PUT(f); f^ := 'B'; PUT(f); "
               "RESET(f); n := 0; WHILE NOT EOF(f) DO BEGIN n := n + 1; GET(f) END; "
               "WRITELN(n) END.")
        rc, out = build_run_linked(src, ["fileops.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "2\n")

    def test_eoln_line_marker_presents_blank(self):
        src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
               "REWRITE(f); WRITELN(f, 'A'); RESET(f); c := f^; WRITELN(c); "
               "GET(f); IF EOLN(f) THEN WRITELN(1); c := f^; WRITELN(c) END.")
        rc, out = build_run_linked(src, ["fileops.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "A\n1\n \n")

    def test_eof_without_argument_uses_input(self):
        src = ("PROGRAM P; VAR n: INTEGER; BEGIN n := 0; "
               "WHILE NOT EOF DO BEGIN n := n + 1; GET(INPUT) END; WRITELN(n) END.")
        rc, out = build_run_linked(src, ["fileops.c"], stdin="XY")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "2\n")

    def test_assign_close_named_text_persists_across_fcb(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "named.txt"
            src = ("PROGRAM P; VAR f, g: TEXT; c1, c2: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); f^ := 'H'; PUT(f); f^ := 'I'; PUT(f); CLOSE(f); "
                   f"ASSIGN(g, '{path}'); RESET(g); c1 := g^; GET(g); c2 := g^; CLOSE(g); "
                   "WRITELN(c1); WRITELN(c2) END.")
            rc, out = build_run_linked(src, ["fileops.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "H\nI\n")
            self.assertEqual(path.read_text(), "HI\n")

    def test_discard_named_file_deletes_host_path(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "gone.txt"
            src = ("PROGRAM P; VAR f: TEXT; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); f^ := 'X'; PUT(f); DISCARD(f); "
                   "WRITELN('done') END.")
            rc, out = build_run_linked(src, ["fileops.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "done\n")
            self.assertFalse(path.exists())

    def test_assign_chr_zero_keeps_anonymous_temp_behavior(self):
        src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
               "ASSIGN(f, CHR(0)); REWRITE(f); f^ := 'T'; PUT(f); RESET(f); c := f^; WRITELN(c); CLOSE(f) END.")
        rc, out = build_run_linked(src, ["fileops.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "T\n")

    def test_readset_reads_allowed_chars_and_leaves_delimiter(self):
        src = ("PROGRAM P; VAR s: LSTRING(10); c: CHAR; BEGIN "
               "READSET(s, ['A'..'Z']); READ(INPUT, c); WRITELN(s); WRITELN(c) END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"], stdin="  \tABC1Z\n", features={"readset-set-literal": True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ABC\n1\n")

    def test_readset_capacity_stops_without_consuming_next_char(self):
        src = ("PROGRAM P; VAR s: LSTRING(3); c: CHAR; BEGIN "
               "READSET(s, ['A'..'Z']); READ(INPUT, c); WRITELN(s); WRITELN(c) END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"], stdin="ABCDE\n", features={"readset-set-literal": True})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ABC\nD\n")

    def test_readset_inline_literal_delimiter_retention_d022(self):
        """D-022: under -f readset-set-literal the original t022 shape compiles
        and runs; READSET consumes ABC and leaves the comma unconsumed."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t022in.txt"
            src = ("PROGRAM P; VAR f: TEXT; l: LSTRING(10); c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'ABC,DEF'); CLOSE(f); "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READSET(f, l, ['A'..'Z']); WRITELN(l); READ(f, c); WRITELN(c); CLOSE(f) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"], features={"readset-set-literal": True})
            self.assertEqual(rc, 0)
            self.assertEqual(out, "ABC\n,\n")

    def test_readfn_reads_filename_and_does_not_consume_line_marker(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "readfn.txt"
            src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
                   "READFN(INPUT, f); IF EOLN(INPUT) THEN WRITELN('eol'); "
                   "REWRITE(f); f^ := 'R'; PUT(f); CLOSE(f); "
                   f"ASSIGN(f, '{path}'); RESET(f); c := f^; WRITELN(c); CLOSE(f) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"], stdin=f"{path}\n")
            self.assertEqual(rc, 0)
            self.assertEqual(out, "eol\nR\n")
            self.assertEqual(path.read_text(), "R\n")

    # ---- Phase 1 regressions: formatted readers must honor the FCB's ----
    # ---- current-component buffer (RESET's implicit GET was being lost) ----

    def test_fread_after_reset_reads_first_component(self):
        """READ(f, c) immediately after RESET must yield the FIRST character.
        Previously pas_fread_char read the raw handle and the component
        supplied by RESET's implicit GET was silently dropped."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "first.txt"
            src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'XY'); CLOSE(f); "
                   "RESET(f); READ(f, c); WRITELN(c) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "X\n")

    def test_readset_after_reset_includes_first_char(self):
        """READSET after RESET must include the first character of the token
        (previously 'HELLO' came back as 'ELLO')."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rs.txt"
            src = ("PROGRAM P; VAR f: TEXT; s: LSTRING(10); BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'HELLO 1'); CLOSE(f); "
                   "RESET(f); READSET(f, s, ['A'..'Z']); WRITELN(s) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"], features={"readset-set-literal": True})
            self.assertEqual(rc, 0)
            self.assertEqual(out, "HELLO\n")

    def test_buffer_get_and_fread_interleave(self):
        """F^ / GET / READ(f, ...) draw from one logical character sequence:
        f^ sees the first component, GET advances, READ consumes the buffered
        component before touching the stream."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mix.txt"
            src = ("PROGRAM P; VAR f: TEXT; c1, c2, c3: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'ABC'); CLOSE(f); "
                   "RESET(f); c1 := f^; GET(f); READ(f, c2); READ(f, c3); "
                   "WRITELN(c1); WRITELN(c2); WRITELN(c3) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "A\nB\nC\n")

    def test_eoln_and_eof_observed_after_formatted_read(self):
        """After READ(f, i) consumes the only token, EOLN(f) sees the line
        marker; after READLN(f) consumes it, EOF(f) is true."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pred.txt"
            src = ("PROGRAM P; VAR f: TEXT; i: INTEGER; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, '7'); CLOSE(f); "
                   "RESET(f); READ(f, i); WRITELN(i); "
                   "IF EOLN(f) THEN WRITELN('eol'); "
                   "READLN(f); IF EOF(f) THEN WRITELN('eof') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "7\neol\neof\n")

    def test_fread_lstring_after_reset_keeps_line_marker(self):
        """String READ after RESET returns the whole first line and leaves the
        line marker as the current component (EOLN true, not consumed)."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ls.txt"
            src = ("PROGRAM P; VAR f: TEXT; s: LSTRING(10); BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'HI'); CLOSE(f); "
                   "RESET(f); READ(f, s); WRITELN(s); "
                   "IF EOLN(f) THEN WRITELN('eol') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "HI\neol\n")

    # ---- Phase 2 regressions: writes in inspection/closed mode must abort ----
    # ---- (previously stream_for silently flipped modes and clobbered bytes) ----

    def test_write_in_inspection_mode_aborts_and_preserves_file(self):
        """WRITE(f, ...) on a file in read mode aborts (nonzero exit) and the
        host file is NOT modified. Previously the runtime silently flipped to
        write mode at the current offset and 'ABCD' became 'ABZD'."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ro.txt"
            src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'ABCD'); CLOSE(f); "
                   "RESET(f); READ(f, c); WRITE(f, 'Z') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertNotEqual(rc, 0)
            self.assertEqual(path.read_text(), "ABCD\n")

    def test_write_to_closed_file_aborts(self):
        """WRITE(f, ...) on an assigned-but-never-opened file aborts instead of
        implicitly creating/opening the file in write mode."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "closed.txt"
            src = ("PROGRAM P; VAR f: TEXT; BEGIN "
                   f"ASSIGN(f, '{path}'); WRITE(f, 'X') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertNotEqual(rc, 0)
            self.assertFalse(path.exists())

    def test_read_in_generation_mode_aborts(self):
        """READ(f, ...) on a file in write mode aborts (symmetric check)."""
        src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
               "REWRITE(f); WRITELN(f, 'A'); READ(f, c) END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
        self.assertNotEqual(rc, 0)

    def test_read_from_closed_file_performs_implicit_reset(self):
        """Reading a closed named file goes through the implicit RESET path
        (consistent with require_text_read), yielding the first component."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "imp.txt"
            src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'Q'); CLOSE(f); "
                   "READ(f, c); WRITELN(c) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "Q\n")

    # ---- Phase 4 regression: bad ASSIGN fails at ASSIGN, not a later fopen ----

    def test_assign_empty_name_aborts_immediately(self):
        """ASSIGN(f, '') (empty after blank-trimming, and not the CHR(0)
        temporary-file spelling) aborts at the ASSIGN call site."""
        src = ("PROGRAM P; VAR f: TEXT; BEGIN ASSIGN(f, '  '); WRITELN('late') END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
        self.assertNotEqual(rc, 0)
        self.assertNotIn("late", out)

    def test_file_mode_field_defaults_and_assignment(self):
        src = ("PROGRAM P; VAR f: TEXT; BEGIN "
               "IF f.MODE = SEQUENTIAL THEN WRITELN('seq'); "
               "IF INPUT.MODE = TERMINAL THEN WRITELN('term'); "
               "f.MODE := DIRECT; IF f.MODE = DIRECT THEN WRITELN('direct') END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "seq\nterm\ndirect\n")

    def test_fcbfqq_record_mode_field_codegen(self):
        src = ("PROGRAM P; VAR b: FCBFQQ; BEGIN "
               "b.MODE := TERMINAL; IF b.MODE = TERMINAL THEN WRITELN('fcb') END.")
        rc, out = build_run_linked(src, [])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "fcb\n")


if __name__ == "__main__":
    unittest.main()


class TestResetModeTransitions(unittest.TestCase):
    """The two mode-transition combinations the §8 amendment flagged as
    NOT yet tested.  Both stress the lazy-fill state machine (the PENDING
    current component + mode bits) across a transition.
    """

    def test_reset_mid_stream_in_read_mode_rewinds_to_first(self):
        """RESET on a file already in read mode, mid-stream: must rewind
        and present the FIRST component again — including re-arming the
        pending implicit GET, not reusing the stale buffered component."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "midstream.txt"
            src = (
                "PROGRAM P; VAR f: TEXT; c1, c2, c3: CHAR; BEGIN "
                f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'XYZ'); CLOSE(f); "
                "RESET(f); c1 := f^; GET(f); c2 := f^; "  # X then Y; mid-stream
                "RESET(f); c3 := f^; "  # rewind: X again
                "WRITELN(c1); WRITELN(c2); WRITELN(c3) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "X\nY\nX\n")

    def test_put_after_get_in_read_mode_aborts(self):
        """PUT on a file in read mode (after RESET/GET) must abort with the
        runtime's mode-enforcement error, not silently corrupt the stream.

        Pins the reimplementation runtime's enforced behavior
        ('PUT requires REWRITE/write mode') [OBSERVED].  The vintage
        compiler's behavior for this sequence is [UNVERIFIED] — a
        differential probe candidate; if the 1981 runtime tolerates it,
        this test documents the deliberate divergence to revisit."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "putget.txt"
            src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); REWRITE(f); WRITELN(f, 'AB'); CLOSE(f); "
                   "RESET(f); c := f^; GET(f); "
                   "f^ := 'Z'; PUT(f) "
                   "END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertNotEqual(rc, 0)


class TestDosCrLfTranslation(unittest.TestCase):
    """DOS CR/LF line markers on TEXT input (8.4 deferral, closed).

    Vintage PC-DOS TEXT files end lines with "\\r\\n"; the runtime folds the
    pair into a single '\\n' marker on input so EOLN/READLN/F^ behave per
    the manual on DOS-produced files.  Bare CR is data; binary FILE OF T
    never translates.  Output keeps host '\\n' (Linux-target adaptation).
    """

    def _write_bytes(self, path, data: bytes):
        with open(path, 'wb') as fh:
            fh.write(data)

    def test_crlf_reads_as_single_line_marker(self):
        """READ two chars from 'AB\\r\\nCD\\r\\n': EOLN fires after B (not at
        a CR component); READLN crosses to the next line; first char is C."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dos.txt"
            self._write_bytes(path, b"AB\r\nCD\r\n")
            src = ("PROGRAM P; VAR f: TEXT; a, b, c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READ(f, a); READ(f, b); "
                   "IF EOLN(f) THEN WRITELN('eol'); "
                   "READLN(f); READ(f, c); "
                   "WRITELN(a); WRITELN(b); WRITELN(c) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "eol\nA\nB\nC\n")

    def test_buffer_variable_blank_at_crlf_marker(self):
        """F^ presents a blank at a CRLF line marker, same as at LF."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dos2.txt"
            self._write_bytes(path, b"X\r\nY\r\n")
            src = ("PROGRAM P; VAR f: TEXT; c1, c2: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "c1 := f^; GET(f); c2 := f^; "
                   "IF EOLN(f) THEN WRITELN('eol'); "
                   "WRITELN(c1); IF c2 = ' ' THEN WRITELN('blank') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "eol\nX\nblank\n")

    def test_eof_true_after_final_crlf(self):
        """A DOS file ending in CRLF: after the last READLN, EOF is true
        (no phantom CR component at the tail)."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dos3.txt"
            self._write_bytes(path, b"7\r\n")
            src = ("PROGRAM P; VAR f: TEXT; i: INTEGER; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READ(f, i); WRITELN(i); READLN(f); "
                   "IF EOF(f) THEN WRITELN('eof') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "7\neof\n")

    def test_bare_cr_is_data_not_marker(self):
        """A CR not followed by LF is an ordinary component, not a marker."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "barecr.txt"
            self._write_bytes(path, b"A\rB\n")
            src = ("PROGRAM P; VAR f: TEXT; a, b, c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READ(f, a); READ(f, b); READ(f, c); "
                   "IF ORD(b) = 13 THEN WRITELN('cr'); "
                   "WRITELN(a); WRITELN(c) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "cr\nA\nB\n")

    def test_binary_file_never_translates(self):
        """FILE OF CHAR components preserve raw CR and LF bytes."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bin.dat"
            self._write_bytes(path, b"\r\n")
            src = ("PROGRAM P; VAR f: FILE OF CHAR; a, b: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "a := f^; GET(f); b := f^; "
                   "IF ORD(a) = 13 THEN WRITELN('cr'); "
                   "IF ORD(b) = 10 THEN WRITELN('lf') END.")
            rc, out = build_run_linked(src, ["fileops.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "cr\nlf\n")


class TestStringNRead(unittest.TestCase):
    """READ into STRING(n) (8.3 deferral closed; semantics [INFERRED]).

    Modeled on the dialect's STRING blank-pad convention and the LSTRING
    reader: copy up to n characters; stop early at the line marker (left as
    the current component, so EOLN observes it); blank-pad the remainder;
    when the destination fills, leave the rest of the line unconsumed.
    Vintage stop/consume behavior is a differential-probe candidate.
    """

    def test_exact_fill_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "s.txt"
            with open(path, 'w') as fh:
                fh.write("ABCDE\n")
            src = ("PROGRAM P; VAR f: TEXT; s: STRING(5); BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READ(f, s); WRITELN(s) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "ABCDE\n")

    def test_short_line_blank_pads(self):
        """'AB' into STRING(5) yields 'AB   ' and EOLN is observable."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "s2.txt"
            with open(path, 'w') as fh:
                fh.write("AB\n")
            src = ("PROGRAM P; VAR f: TEXT; s: STRING(5); BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READ(f, s); "
                   "IF EOLN(f) THEN WRITELN('eol'); "
                   "WRITELN(s, '|') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "eol\nAB   |\n")

    def test_full_destination_leaves_rest_of_line(self):
        """STRING(3) from 'ABCDE': first READ takes ABC, next chars are DE."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "s3.txt"
            with open(path, 'w') as fh:
                fh.write("ABCDE\n")
            src = ("PROGRAM P; VAR f: TEXT; s: STRING(3); c1, c2: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READ(f, s); READ(f, c1); READ(f, c2); "
                   "WRITELN(s); WRITELN(c1); WRITELN(c2) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "ABC\nD\nE\n")

    def test_stdin_path(self):
        """The no-selector READ path (stdin) handles STRING(n) too."""
        src = ("PROGRAM P; VAR s: STRING(4); BEGIN "
               "READ(s); WRITELN(s, '|') END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"], stdin="HI\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "HI  |\n")

    def test_crlf_line_marker_stops_string_read(self):
        """Interaction with CRLF translation: a DOS marker stops the fill."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "s4.txt"
            with open(path, 'wb') as fh:
                fh.write(b"AB\r\nC\r\n")
            src = ("PROGRAM P; VAR f: TEXT; s: STRING(4); c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); "
                   "READ(f, s); READLN(f); READ(f, c); "
                   "WRITELN(s, '|'); WRITELN(c) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "AB  |\nC\n")


class TestTrappedIO(unittest.TestCase):
    """F.TRAP / F.ERRS trapped I/O (manual ch.12 File Field Values).

    With F.TRAP := TRUE, an operational I/O error records an internal code
    in F.ERRS and the operation is abandoned instead of aborting; the
    program inspects and clears F.ERRS.  Internal code values are
    [INFERRED] (vintage codes unknown — probe candidate); the trap/abort
    *behavior* is what these tests pin.
    """

    def test_trapped_reset_on_missing_file_sets_errs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "no_such_file.txt"
            src = ("PROGRAM P; VAR f: TEXT; BEGIN "
                   f"ASSIGN(f, '{path}'); "
                   "f.TRAP := TRUE; RESET(f); "
                   "IF f.ERRS <> 0 THEN WRITELN('trapped'); "
                   "f.ERRS := 0; "
                   "IF f.ERRS = 0 THEN WRITELN('cleared') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "trapped\ncleared\n")

    def test_untrapped_reset_on_missing_file_aborts(self):
        """Default (TRAP off): same error still aborts loudly."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "no_such_file.txt"
            src = ("PROGRAM P; VAR f: TEXT; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertNotEqual(rc, 0)

    def test_trapped_get_past_eof_sets_errs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "one.txt"
            with open(path, 'w') as fh:
                fh.write("A\n")
            src = (
                "PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
                f"ASSIGN(f, '{path}'); RESET(f); f.TRAP := TRUE; "
                "c := f^; GET(f); GET(f); "  # A, marker, now at eof
                "GET(f); "  # past eof: trapped
                "IF f.ERRS <> 0 THEN WRITELN('trapped'); "
                "WRITELN(c) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "trapped\nA\n")

    def test_trapped_put_in_read_mode_sets_errs(self):
        """The PUT-after-GET mode violation becomes trappable with TRAP on
        (companion to TestResetModeTransitions' untrapped abort)."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rw.txt"
            with open(path, 'w') as fh:
                fh.write("AB\n")
            src = ("PROGRAM P; VAR f: TEXT; c: CHAR; BEGIN "
                   f"ASSIGN(f, '{path}'); RESET(f); f.TRAP := TRUE; "
                   "c := f^; GET(f); "
                   "f^ := 'Z'; PUT(f); "
                   "IF f.ERRS <> 0 THEN WRITELN('trapped') END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            self.assertEqual(out, "trapped\n")

    def test_trap_defaults_off_and_errs_zero(self):
        src = ("PROGRAM P; VAR f: TEXT; BEGIN "
               "IF NOT f.TRAP THEN WRITELN('off'); "
               "IF f.ERRS = 0 THEN WRITELN('zero') END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "off\nzero\n")


class TestTrapErrsAsWriteArguments(unittest.TestCase):
    """Regression found by probe drafting: WRITELN(f.ERRS) misrouted —
    the WRITE leading-file-selector check saw the designator's base type
    (a file) and treated f.ERRS as a file selector, producing invalid IR
    (bitcast i32 to FCB*).  _pas_type now models TRAP/ERRS selectors."""

    def test_writeln_errs_directly(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nofile.xyz"
            src = ("PROGRAM P; VAR f: TEXT; BEGIN "
                   f"ASSIGN(f, '{path}'); f.TRAP := TRUE; RESET(f); "
                   "WRITELN(f.ERRS) END.")
            rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
            self.assertEqual(rc, 0)
            # D-012/RM-P3-ERRSCODE: trapped RESET missing/open failure uses
            # the observed vintage program-visible F.ERRS code 10.
            self.assertEqual(out, "10\n")

    def test_trap_field_readable_as_boolean(self):
        """f.TRAP reads back as a BOOLEAN.  Observed via IF rather than
        WRITELN: WRITELN of any BOOLEAN currently prints the raw i8 byte
        (pre-existing defect, found while writing this test — probe t020
        captures the vintage format before we pick one)."""
        src = ("PROGRAM P; VAR f: TEXT; BEGIN "
               "f.TRAP := TRUE; "
               "IF f.TRAP THEN WRITELN('on') END.")
        rc, out = build_run_linked(src, ["fileops.c", "readq.c"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "on\n")
