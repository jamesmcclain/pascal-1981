"""
Symbol Table and Scope Management

Manages identifiers, their types, and nested scopes.
Used by both the type checker and code generator.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .type_system import Type


@dataclass
class SourceLocation:
    """Source code location for error reporting."""

    filename: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}"


@dataclass
class Symbol:
    """A symbol (variable, function, procedure, constant, etc.)."""

    name: str
    type: Type
    kind: str  # 'var', 'const', 'function', 'procedure', 'parameter'
    location: Optional[SourceLocation] = None
    is_mutable: bool = True  # False for constants and parameters
    value: Any = None  # For codegen: LLVM value
    is_builtin: bool = False  # True for predeclared standard symbols
    # Storage residence: the SPACE ordinal this variable lives in, from a
    # [SPACE(s)] attribute. None means unspecified (implicitly HOST/0). Only
    # meaningful inside a DEVICE MODULE (ads-memory-spaces-design.md S4.4).
    space: Optional[int] = None

    def __repr__(self) -> str:
        return f"Symbol({self.name}: {self.type}, kind={self.kind})"


class Scope:
    """A scope (local block, function, procedure, etc.)."""

    def __init__(self, parent: Optional['Scope'] = None):
        self.parent = parent
        self.symbols: Dict[str, Symbol] = {}

    def define(self, name: str, symbol: Symbol) -> None:
        """Define a symbol in this scope (not parent scopes)."""
        self.symbols[name] = symbol

    def lookup(self, name: str) -> Optional[Symbol]:
        """Look up a symbol, searching parent scopes if needed."""
        if name in self.symbols:
            return self.symbols[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def lookup_local(self, name: str) -> Optional[Symbol]:
        """Look up a symbol only in this scope (not parents)."""
        return self.symbols.get(name)

    def all_symbols(self) -> Dict[str, Symbol]:
        """Return all symbols in this scope."""
        return dict(self.symbols)


class SymbolTable:
    """Manages nested scopes and symbol lookup."""

    def __init__(self):
        self.global_scope = Scope()
        self.current_scope = self.global_scope

    def enter_scope(self) -> None:
        """Enter a new nested scope."""
        new_scope = Scope(parent=self.current_scope)
        self.current_scope = new_scope

    def exit_scope(self) -> None:
        """Exit the current scope, return to parent."""
        if self.current_scope.parent:
            self.current_scope = self.current_scope.parent

    def define(self, name: str, symbol: Symbol) -> None:
        """Define a symbol in the current scope."""
        self.current_scope.define(name, symbol)

    def lookup(self, name: str) -> Optional[Symbol]:
        """Look up a symbol (searches parent scopes)."""
        return self.current_scope.lookup(name)

    def lookup_local(self, name: str) -> Optional[Symbol]:
        """Look up a symbol only in current scope."""
        return self.current_scope.lookup_local(name)

    def get_scope(self) -> Scope:
        """Get the current scope."""
        return self.current_scope

    def get_global_scope(self) -> Scope:
        """Get the global scope."""
        return self.global_scope
