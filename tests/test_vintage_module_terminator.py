from pascal1981.ast_nodes import ModuleUnit
from tests.support import ParserError, parse_source


def test_plain_module_accepts_vintage_end_dot_terminator():
    unit = parse_source(
        "MODULE kernel;\n"
        "VAR flags: ARRAY [0..99] OF INTEGER;\n"
        "PROCEDURE build_primes;\n"
        "BEGIN\n"
        "END;\n"
        "END.\n"
    )

    assert isinstance(unit, ModuleUnit)
    assert unit.name == "kernel"
    assert not unit.is_device


def test_plain_module_still_accepts_bare_dot_terminator():
    unit = parse_source("MODULE kernel;\nVAR x: INTEGER;\n.\n")

    assert isinstance(unit, ModuleUnit)
    assert unit.name == "kernel"


def test_module_body_is_still_rejected():
    try:
        parse_source("MODULE M;\nBEGIN\nEND.\n")
    except ParserError as exc:
        assert "expected DOT" in str(exc)
    else:
        raise AssertionError("MODULE with compound statement body should be rejected")
