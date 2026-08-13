import subprocess
import sys

import pytest

from pascal1981.serialization import ast_from_json, tokens_from_json

MINIMAL_PAS = """PROGRAM Minimal;
VAR
  x: INTEGER;
BEGIN
  x := 42;
END.
"""


def test_cli_lex_stdout(tmp_path):
    pas_file = tmp_path / "test.pas"
    pas_file.write_text(MINIMAL_PAS)

    cmd = [sys.executable, "-m", "pascal1981.cli_lex", str(pas_file)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)

    tokens = tokens_from_json(res.stdout)
    assert len(tokens) > 0
    assert any(t.kind == "PROGRAM" for t in tokens)


def test_cli_parse_stdout(tmp_path):
    pas_file = tmp_path / "test.pas"
    pas_file.write_text(MINIMAL_PAS)

    lex_res = subprocess.run([sys.executable, "-m", "pascal1981.cli_lex", str(pas_file)], capture_output=True, text=True, check=True)
    parse_res = subprocess.run([sys.executable, "-m", "pascal1981.cli_parse", "--source-file", str(pas_file)], input=lex_res.stdout, capture_output=True, text=True, check=True)

    ast = ast_from_json(parse_res.stdout)
    assert ast.__class__.__name__ == "ProgramUnit"
    assert ast.name == "Minimal"


def test_cli_codegen_stdout(tmp_path):
    pas_file = tmp_path / "test.pas"
    pas_file.write_text(MINIMAL_PAS)

    lex_res = subprocess.run([sys.executable, "-m", "pascal1981.cli_lex", str(pas_file)], capture_output=True, text=True, check=True)
    parse_res = subprocess.run([sys.executable, "-m", "pascal1981.cli_parse", "--source-file", str(pas_file)], input=lex_res.stdout, capture_output=True, text=True, check=True)
    codegen_res = subprocess.run([sys.executable, "-m", "pascal1981.cli_codegen", "--source-file", str(pas_file)],
                                 input=parse_res.stdout,
                                 capture_output=True,
                                 text=True,
                                 check=True)

    assert "define i32 @\"main\"" in codegen_res.stdout


def test_file_based_stages(tmp_path):
    pas_file = tmp_path / "test.pas"
    tok_file = tmp_path / "test.tok"
    ast_file = tmp_path / "test.ast"
    ll_file = tmp_path / "test.ll"

    pas_file.write_text(MINIMAL_PAS)

    subprocess.run([sys.executable, "-m", "pascal1981.cli_lex", str(pas_file), "-o", str(tok_file)], check=True)
    assert tok_file.exists()

    subprocess.run([sys.executable, "-m", "pascal1981.cli_parse", str(tok_file), "-o", str(ast_file), "--source-file", str(pas_file)], check=True)
    assert ast_file.exists()

    subprocess.run([sys.executable, "-m", "pascal1981.cli_codegen", str(ast_file), "-o", str(ll_file), "--source-file", str(pas_file)], check=True)
    assert ll_file.exists()

    ll_content = ll_file.read_text()
    assert "define i32 @\"main\"" in ll_content


def test_driver_split_processes_flag(tmp_path):
    pas_file = tmp_path / "test.pas"
    ll_file = tmp_path / "test.ll"
    pas_file.write_text(MINIMAL_PAS)

    subprocess.run([sys.executable, "-m", "pascal1981.compile_to_llvm", "-S", "--split-processes", str(pas_file), "-o", str(ll_file)], check=True)
    assert ll_file.exists()
    assert "define i32 @\"main\"" in ll_file.read_text()
