"""Native compiler stages must agree with the Python reference pipeline.

Set all four variables below to executable native stage binaries to enable this
suite.  It deliberately does not build them: selecting a bootstrap artifact is
a caller/CI concern, and an ordinary test run must remain self-contained.

    NATIVE_LEXER, NATIVE_PARSER, NATIVE_TYPECHECKER, NATIVE_CODEGEN

The stages use the stdin/stdout JSON protocol.  JSON is compared as decoded
objects because the native cJSON serializer intentionally has different
whitespace.  ``resolved_type`` is stripped from typed ASTs because it is an
output-only annotation with an intentionally different native policy.  LLVM
text is not compared: independent lowerers legitimately choose different
names and orderings, so both outputs are assembled by clang instead.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
NATIVE_ENV = {
    "lexer": "NATIVE_LEXER",
    "parser": "NATIVE_PARSER",
    "typechecker": "NATIVE_TYPECHECKER",
    "codegen": "NATIVE_CODEGEN",
}
NATIVE = {stage: os.environ.get(env) for stage, env in NATIVE_ENV.items()}
HAS_NATIVE_PIPELINE = all(path and os.access(path, os.X_OK) for path in NATIVE.values())


def _run(command, stdin=""):
    """Run one pipeline stage and retain stderr for useful parity failures."""
    return subprocess.run(
        command,
        input=stdin,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _python_pipeline(source, stages):
    """Run the reference stages with precisely the stdin/stdout stage protocol."""
    result = _run([sys.executable, "-m", "pascal1981.cli_lex", str(source)])
    if result.returncode or stages == 1:
        return result
    result = _run([
        sys.executable,
        "-m",
        "pascal1981.cli_parse",
        "--source-file",
        str(source),
        "--dialect",
        "extended",
    ], result.stdout)
    if result.returncode or stages == 2:
        return result
    result = _run([
        sys.executable,
        "-m",
        "pascal1981.cli_typecheck",
        "--source-file",
        str(source),
        "--dialect",
        "extended",
    ], result.stdout)
    if result.returncode or stages == 3:
        return result
    return _run([
        sys.executable,
        "-m",
        "pascal1981.cli_codegen",
        "--source-file",
        str(source),
        "--dialect",
        "extended",
    ], result.stdout)


def _native_pipeline(source, stages):
    """Run native stages, feeding each stage's stdout into the next one."""
    result = _run([NATIVE["lexer"]], source.read_text())
    for stage in ("parser", "typechecker", "codegen")[:stages - 1]:
        if result.returncode:
            return result
        result = _run([NATIVE[stage]], result.stdout)
    return result


def _without_resolved_type(value):
    """Remove the known non-semantic typed-AST annotation recursively."""
    if isinstance(value, list):
        return [_without_resolved_type(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_resolved_type(item) for key, item in value.items() if key != "resolved_type"}
    return value


@unittest.skipUnless(
    HAS_NATIVE_PIPELINE,
    "requires executable NATIVE_LEXER, NATIVE_PARSER, NATIVE_TYPECHECKER, and NATIVE_CODEGEN",
)
class TestNativeFixtureParity(unittest.TestCase):
    """Exercise every single-file parser/typecheck fixture in both pipelines."""

    def _assert_json_equal(self, source, stages, normalizer=lambda value: value):
        python = _python_pipeline(source, stages)
        native = _native_pipeline(source, stages)
        self.assertEqual(python.returncode, 0, f"Python rejected {source}:\n{python.stderr}")
        self.assertEqual(native.returncode, 0, f"Native rejected {source}:\n{native.stderr}")
        try:
            expected = normalizer(json.loads(python.stdout))
            actual = normalizer(json.loads(native.stdout))
        except json.JSONDecodeError as exc:
            self.fail(f"{source}: stage {stages} did not emit JSON: {exc}\nNative stderr:\n{native.stderr}")
        self.assertEqual(actual, expected, f"native/Python JSON mismatch for {source}")

    def _assert_same_acceptance(self, source, stages):
        python = _python_pipeline(source, stages)
        native = _native_pipeline(source, stages)
        self.assertEqual(
            native.returncode == 0,
            python.returncode == 0,
            f"native/Python acceptance differs for {source}\n"
            f"Python stderr:\n{python.stderr}\nNative stderr:\n{native.stderr}",
        )

    def test_parser_success_fixtures_have_equal_ast(self):
        for source in sorted((FIXTURES / "parser" / "should_pass").glob("*.pas")):
            with self.subTest(source=source.name):
                self._assert_json_equal(source, stages=2)

    def test_parser_failure_fixtures_have_same_acceptance(self):
        for source in sorted((FIXTURES / "parser" / "should_fail").glob("*.pas")):
            with self.subTest(source=source.name):
                self._assert_same_acceptance(source, stages=2)

    def test_parser_judgment_call_fixtures_have_expected_parity(self):
        fixtures = FIXTURES / "parser" / "judgment_calls"
        self._assert_json_equal(fixtures / "A_write_field_width.pas", stages=2)
        self._assert_same_acceptance(fixtures / "B_colon_args_any_call.pas", stages=2)

    def test_typecheck_success_fixtures_have_equal_typed_ast(self):
        for source in sorted((FIXTURES / "typecheck" / "should_pass").glob("*.pas")):
            with self.subTest(source=source.name):
                self._assert_json_equal(source, stages=3, normalizer=_without_resolved_type)

    def test_typecheck_failure_fixtures_have_same_acceptance(self):
        for source in sorted((FIXTURES / "typecheck" / "should_fail").glob("*.pas")):
            with self.subTest(source=source.name):
                self._assert_same_acceptance(source, stages=3)

    @unittest.skipUnless(shutil.which("clang"), "requires clang to verify LLVM assembly")
    def test_typecheck_success_fixtures_emit_valid_llvm(self):
        for source in sorted((FIXTURES / "typecheck" / "should_pass").glob("*.pas")):
            with self.subTest(source=source.name):
                python = _python_pipeline(source, stages=4)
                native = _native_pipeline(source, stages=4)
                self.assertEqual(python.returncode, 0, f"Python codegen rejected {source}:\n{python.stderr}")
                self.assertEqual(native.returncode, 0, f"Native codegen rejected {source}:\n{native.stderr}")
                for label, result in (("Python", python), ("native", native)):
                    assembled = _run(["clang", "-x", "ir", "-c", "-o", os.devnull, "-"], result.stdout)
                    self.assertEqual(assembled.returncode, 0, f"{label} emitted invalid LLVM for {source}:\n{assembled.stderr}")


if __name__ == "__main__":
    unittest.main()
