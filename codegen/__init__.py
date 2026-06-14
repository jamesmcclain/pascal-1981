"""
LLVM IR code generator for Pascal AST.

Walks the AST and emits LLVM IR. Supports:
- Integer and boolean variables
- Procedure and function declarations
- All statement types (IF, FOR, WHILE, REPEAT, CASE, etc.)
- Integer expressions and operators
- Built-in I/O: WRITE/WRITELN and READ/READLN
"""

from __future__ import annotations

from dataclasses import dataclass
from parser import parse_file
from typing import Any, Dict, List, Optional, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *
from builtins_registry import register_builtins
from type_system import LStringType as ResolvedLStringType
from type_system import StringType as ResolvedStringType

# Import base classes and support classes
from .base import (_SCALAR_SIZES, CodegenBase, CodegenError, LoopContext, Scope, Symbol)
from .constfold import ConstFoldMixin
from .decls import DeclsMixin
from .exprs import ExprsMixin
from .files import FilesMixin
from .io_write_read import IoWriteReadMixin
from .runtime_builtins import RuntimeBuiltinsMixin
from .sets import SetsMixin
from .stmts import StmtsMixin
from .strings import StringsMixin
# Import mixin classes
from .types_map import TypesMapMixin


class Codegen(CodegenBase, TypesMapMixin, ConstFoldMixin, RuntimeBuiltinsMixin, FilesMixin, SetsMixin, StringsMixin, IoWriteReadMixin, StmtsMixin, DeclsMixin, ExprsMixin):
    """LLVM IR code generator."""

    def __init__(self, verbose: bool = False, source_file: Optional[str] = None, force_flags: Optional[Dict[str, bool]] = None, features: Optional[Dict[str, bool]] = None):
        """Initialize Codegen with all mixins."""
        super().__init__(verbose=verbose, source_file=source_file, force_flags=force_flags, features=features)

    # ========================================================================
    # Type System
    # ========================================================================

    def null_lstring_ptr(self) -> ir.Value:
        """Return a pointer to the empty LSTRING constant."""
        if not hasattr(self, '_null_lstring_global'):
            empty = ir.Constant(ir.ArrayType(ir.IntType(8), 1), bytearray(b'\0'))
            self._null_lstring_global = ir.GlobalVariable(self.module, empty.type, name=self.unique_name('nullstr'))
            self._null_lstring_global.initializer = empty
            self._null_lstring_global.global_constant = True
        zero = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(self._null_lstring_global, [zero, zero])

    def _declare_libm_func(self, name: str, ret_type: ir.Type, arg_types: List[ir.Type]) -> ir.Function:
        if name not in [f.name for f in self.module.functions]:
            fn_type = ir.FunctionType(ret_type, arg_types)
            ir.Function(self.module, fn_type, name=name)
        return next(f for f in self.module.functions if f.name == name)


def compile_to_llvm(
        ast: Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit],
        verbose: bool = False,
        source_file: Optional[str] = None,
        force_flags: Optional[Dict[str, bool]] = None,
        features: Optional[Dict[str, bool]] = None,
        # Legacy compat: force_rangeck=True/False is equivalent to
        # force_flags={'RANGECK': True/False}.
        force_rangeck: Optional[bool] = None) -> str:
    """Compile AST to LLVM IR string."""
    merged: Dict[str, bool] = {}
    if force_rangeck is not None:
        merged['RANGECK'] = force_rangeck
    if force_flags:
        merged.update(force_flags)
    codegen = Codegen(verbose=verbose, source_file=source_file, force_flags=merged or None, features=features)
    module = codegen.codegen(ast)
    return str(module)


__all__ = ['Codegen', 'CodegenError', 'Symbol', 'LoopContext', 'Scope', 'compile_to_llvm']
