"""Type-check result and diagnostic record types.

Shared by the checker mixins and re-exported from pascal1981.type_checker
for backward compatibility.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from ..ast_nodes import ASTNode
from ..symbol_table import SourceLocation, SymbolTable


@dataclass
class TypeCheckError:
    """A type checking error or warning."""

    message: str
    location: Optional[SourceLocation] = None
    severity: str = 'error'  # 'error' or 'warning'

    def __str__(self) -> str:
        if self.location:
            return f"{self.severity.upper()} at {self.location}: {self.message}"
        return f"{self.severity.upper()}: {self.message}"


@dataclass
class TypeCheckResult:
    """Result of type checking."""

    success: bool
    symbol_table: SymbolTable
    errors: List[TypeCheckError]
    warnings: List[TypeCheckError]
    annotated_ast: Optional[ASTNode] = None


class TypeChecker(ABC):
    """Abstract base class for type checkers."""

    @abstractmethod
    def check(self, ast: ASTNode) -> TypeCheckResult:
        pass
