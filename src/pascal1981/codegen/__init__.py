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
from typing import Any, Dict, List, Optional, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ..ast_nodes import *
from ..builtins_registry import register_builtins
from ..type_system import LStringType as ResolvedLStringType
from ..type_system import StringType as ResolvedStringType
# Import base classes and support classes
from .base import (_SCALAR_SIZES, CodegenBase, CodegenError, LoopContext, Scope, Symbol, _is_gpu_triple)
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

    def __init__(self,
                 verbose: bool = False,
                 source_file: Optional[str] = None,
                 force_flags: Optional[Dict[str, bool]] = None,
                 features: Optional[Dict[str, bool]] = None,
                 device_triple: str = "x86_64-pc-linux-gnu",
                 host_triple: str = "x86_64-pc-linux-gnu",
                 is_root_compiland: bool = True,
                 is_device_compiland: bool = False,
                 embed_device_ptx_text: Optional[str] = None,
                 device_backend: str = 'cpu'):
        """Initialize Codegen with all mixins."""
        super().__init__(verbose=verbose,
                         source_file=source_file,
                         force_flags=force_flags,
                         features=features,
                         device_triple=device_triple,
                         host_triple=host_triple,
                         is_root_compiland=is_root_compiland,
                         is_device_compiland=is_device_compiland,
                         embed_device_ptx_text=embed_device_ptx_text,
                         device_backend=device_backend)

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
        return self.runtime_extern(name)


def compile_to_llvm(
        ast: Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit],
        verbose: bool = False,
        source_file: Optional[str] = None,
        force_flags: Optional[Dict[str, bool]] = None,
        features: Optional[Dict[str, bool]] = None,
        device_triple: str = "x86_64-pc-linux-gnu",
        host_triple: str = "x86_64-pc-linux-gnu",
        embed_device_ptx_text: Optional[str] = None,
        device_backend: str = 'cpu',
        # Legacy compat: force_rangeck=True/False is equivalent to
        # force_flags={'RANGECK': True/False}.
        force_rangeck: Optional[bool] = None) -> str:
    """Compile AST to LLVM IR string."""
    merged: Dict[str, bool] = {}
    if force_rangeck is not None:
        merged['RANGECK'] = force_rangeck
    if force_flags:
        merged.update(force_flags)
    # Only PROGRAM owns the process-wide @input/@output
    # definitions. MODULE and UNIT compilands are library-like objects and emit
    # external declarations so linking them with a PROGRAM cannot collide.
    is_root_compiland = isinstance(ast, ProgramUnit)
    # A DEVICE unit/module (DEVICE MODULE / DEVICE INTERFACE / DEVICE
    # IMPLEMENTATION) carries no host I/O, so the predeclared INPUT/OUTPUT
    # host-stream globals must not be emitted -- they would otherwise surface
    # as dead `.extern .global input/output` in the device PTX (followups.md
    # item 2). The root AST node's is_device flag is the authoritative source.
    is_device_compiland = bool(getattr(ast, 'is_device', False))
    codegen = Codegen(verbose=verbose,
                      source_file=source_file,
                      force_flags=merged or None,
                      features=features,
                      device_triple=device_triple,
                      host_triple=host_triple,
                      is_root_compiland=is_root_compiland,
                      is_device_compiland=is_device_compiland,
                      embed_device_ptx_text=embed_device_ptx_text,
                      device_backend=device_backend)
    module = codegen.codegen(ast)
    return str(module)


__all__ = ['Codegen', 'CodegenError', 'Symbol', 'LoopContext', 'Scope', 'compile_to_llvm']
