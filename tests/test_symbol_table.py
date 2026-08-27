"""Tests for Pascal identifier handling in the symbol table."""

from pascal1981.symbol_table import Scope, Symbol
from pascal1981.type_system import IntegerType


def test_scope_keys_are_case_insensitive_and_spelling_is_preserved():
    symbol = Symbol("Counter", IntegerType(), "var")
    scope = Scope()

    scope.define(symbol.name, symbol)

    assert scope.lookup("counter") is symbol
    assert scope.lookup_local("COUNTER") is symbol
    assert scope.all_symbols() == {"counter": symbol}
    assert symbol.name == "Counter"


def test_scope_parent_lookup_is_case_insensitive():
    symbol = Symbol("Greet", IntegerType(), "procedure")
    parent = Scope()
    child = Scope(parent=parent)
    parent.define(symbol.name, symbol)

    assert child.lookup("gREET") is symbol


def test_case_only_redefinition_uses_one_symbol_key():
    first = Symbol("Foo", IntegerType(), "var")
    second = Symbol("foo", IntegerType(), "var")
    scope = Scope()
    scope.define(first.name, first)

    assert scope.lookup_local(second.name) is first
