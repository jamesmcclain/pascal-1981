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

from tests.support import (parse_source, requires_exe, requires_llvm, typecheck_source)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNTIME = os.path.join(_REPO_ROOT, "runtime")


def compile_to_ir(src: str) -> str:
    from codegen_llvm import compile_to_llvm
    result = typecheck_source(src)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    return compile_to_llvm(parse_source(src))


def build_run_linked(src: str, runtime_files, stdin: str = "") -> tuple:
    """Like the codegen harness, but also links the named runtime C files so
    that ENCODE/DECODE/SCANEQ/SCANNE resolve at link time."""
    from codegen_llvm import compile_to_llvm
    result = typecheck_source(src)
    if not result.success:
        raise RuntimeError(f"Type check failed: {result.errors}")
    ir = compile_to_llvm(parse_source(src))
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
        self.assertIn('call i8* @"malloc"(i64 12)', ir)
        self.assertNotIn('call i8* @"malloc"(i64 8)', ir)

    def test_new_sizes_scalar_pointee(self):
        """A pointer to INTEGER still allocates exactly 4 bytes."""
        src = "PROGRAM P; VAR p: ^INTEGER; BEGIN NEW(p) END."
        ir = compile_to_ir(src)
        self.assertIn('call i8* @"malloc"(i64 4)', ir)


@requires_llvm
class TestEncodeDecodeArgs(unittest.TestCase):

    def test_encode_passes_capacity_and_width(self):
        """ENCODE bounds by declared capacity and threads the field width."""
        src = ("PROGRAM P; VAR l: LSTRING(20); ok: BOOLEAN; "
               "BEGIN ok := ENCODE(l, 42:6) END.")
        line = _call_line(compile_to_ir(src), "encode_value")
        # capacity 20 (not the current length), value 42, width 6
        self.assertIn("i32 20", line)
        self.assertIn("i32 42", line)
        self.assertIn("i32 6", line)

    def test_decode_passes_destination_width(self):
        """DECODE tells the runtime the destination width so it can write back."""
        for decl, size in (("n: INTEGER", 4), ("w: WORD", 2), ("c: CHAR", 1)):
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
        rc, out = build_run_linked(src, [])
        self.assertEqual(out, "42\nQ\n")


if __name__ == "__main__":
    unittest.main()
