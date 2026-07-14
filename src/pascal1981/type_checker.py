"""
Static Type Checker for Pascal-1981 Compiler

Performs semantic analysis on the AST:
- Builds symbol tables
- Resolves identifier references
- Checks type compatibility
- Validates control flow types
"""

from typing import Any, Dict, List, Optional

from .ast_nodes import (ASTNode, FuncDecl, ImplementationUnit, InterfaceUnit, ModuleUnit, ProcDecl, ProgramUnit)
from .builtins_registry import register_builtins
from .symbol_table import SymbolTable
from .type_system import Type
from .typecheck import (BuiltinArgsMixin, ConstFoldMixin, DeclsMixin, DeviceCheckMixin, DiagnosticsMixin, ExprInferMixin, StmtsMixin, TypeCheckError, TypeCheckResult,
                        TypeChecker, TypeResolveMixin, UnitsMixin)

__all__ = ['PascalTypeChecker', 'TypeCheckError', 'TypeCheckResult', 'TypeChecker']


class PascalTypeChecker(UnitsMixin, DeclsMixin, StmtsMixin, BuiltinArgsMixin, DeviceCheckMixin, ConstFoldMixin, ExprInferMixin, TypeResolveMixin, DiagnosticsMixin,
                        TypeChecker):
    """Type checker for Pascal-1981."""

    def __init__(self, source_file: Optional[str] = None, features: Optional[Dict[str, bool]] = None):
        self.symbol_table = SymbolTable()
        self.errors: List[TypeCheckError] = []
        self.warnings: List[TypeCheckError] = []
        self.current_function: Optional[FuncDecl] = None
        self.current_function_return_type: Optional[Type] = None
        self.current_procedure: Optional[ProcDecl] = None
        self.current_interface_decls: Dict[str, Any] = {}
        self.source_file = source_file  # Path to the source file being compiled
        self.features: Dict[str, bool] = features if features is not None else {}
        # Historical name: now true while checking any device compiland body
        # (DEVICE MODULE / DEVICE INTERFACE / DEVICE IMPLEMENTATION). Drives the
        # dereferenceability invariant and gates the address-space surface
        # (ads-memory-spaces-design.md S3.3). Programs and plain host units => False.
        self.in_device_module: bool = False
        # caller(upper) -> list of (callee(upper), call_node), collected only while
        # checking a DEVICE MODULE body; consumed by _detect_device_recursion at
        # module end to flag direct AND mutual recursion as call-graph cycles.
        self._device_callgraph: dict = {}
        # Names of record types pre-declared in the current declaration block so
        # that forward and self pointer references (linked lists) resolve to a
        # stable object instead of falling back to ^CHAR.
        self._predeclared_types: set = set()
        self._setup_builtins()

    def feature_enabled(self, name: str) -> bool:
        """Return whether a named compile-time extension feature is enabled."""
        return self.features.get(name, False)

    def _setup_builtins(self) -> None:
        """Define built-in procedures and functions in the global scope."""
        register_builtins(self.symbol_table, self.features)

    def check(self, ast: ASTNode) -> TypeCheckResult:
        """Main entry point for type checking."""
        self.errors = []
        self.warnings = []
        self._predeclared_types = set()
        # Reset symbol table but keep builtins
        self.symbol_table = SymbolTable()
        self._setup_builtins()

        try:
            if isinstance(ast, ProgramUnit):
                self.check_program_unit(ast)
            elif isinstance(ast, InterfaceUnit):
                self.check_interface_unit(ast)
            elif isinstance(ast, ImplementationUnit):
                self.check_implementation_unit(ast)
            elif isinstance(ast, ModuleUnit):
                self.check_module_unit(ast)
            else:
                self.error(f"Unknown root node type: {type(ast).__name__}", ast.location)
        except Exception as e:
            self.error(f"Internal error during type checking: {e}", None)

        return TypeCheckResult(success=len(self.errors) == 0, symbol_table=self.symbol_table, errors=self.errors, warnings=self.warnings, annotated_ast=ast)
