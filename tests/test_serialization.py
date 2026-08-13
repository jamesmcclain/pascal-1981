import json

import pytest

from pascal1981.ast_nodes import (BinOp, Block, BuiltinType, Identifier, IntLiteral, ProgramUnit, VarDecl)
from pascal1981.lexer import Lexer, Token
from pascal1981.parser import Parser, parse_file
from pascal1981.serialization import (ast_from_dict, ast_from_json, ast_to_dict, ast_to_json, tokens_from_json, tokens_to_json)


def test_token_serialization():
    lexer = Lexer("PROGRAM Test; BEGIN x := 42; END.")
    tokens = lexer.tokenize()
    json_str = tokens_to_json(tokens)
    reconstructed = tokens_from_json(json_str)

    assert len(tokens) == len(reconstructed)
    for original, rec in zip(tokens, reconstructed):
        assert original.kind == rec.kind
        assert original.code == rec.code
        assert original.lexeme == rec.lexeme
        assert original.value == rec.value
        assert original.line == rec.line
        assert original.column == rec.column
        assert original.flags == rec.flags


def test_ast_serialization_basic():
    ast = ProgramUnit(name="Hello", params=["input", "output"], uses=[], block=Block(decls=[VarDecl(names=["x"], type_expr=BuiltinType("INTEGER"), attributes=[])], body=[]))
    json_str = ast_to_json(ast)
    reconstructed = ast_from_json(json_str)

    assert isinstance(reconstructed, ProgramUnit)
    assert reconstructed.name == "Hello"
    assert reconstructed.params == ["input", "output"]
    assert len(reconstructed.block.decls) == 1
    assert reconstructed.block.decls[0].names == ["x"]
    assert reconstructed.block.decls[0].type_expr.name == "INTEGER"


def test_ast_serialization_parsed_file(tmp_path):
    pas_file = tmp_path / "simple.pas"
    pas_file.write_text("PROGRAM Simple; BEGIN END.")

    tokens = Lexer(pas_file.read_text()).tokenize()
    ast = Parser(tokens).parse()

    json_str = ast_to_json(ast)
    reconstructed = ast_from_json(json_str)

    assert type(reconstructed) is type(ast)
    assert reconstructed.name == ast.name
