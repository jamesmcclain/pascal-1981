"""
DECLS mixin for Codegen.

Declaration code generation

Part of Plan 1 refactoring (mixin-based architecture).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *

from .base import Scope


class DeclsMixin:
    """Mixin for decls functionality."""

    def codegen(self, unit: Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit]) -> ir.Module:
        """Generate LLVM IR from AST root."""
        if isinstance(unit, ProgramUnit):
            return self.codegen_program(unit)
        elif isinstance(unit, ModuleUnit):
            return self.codegen_module(unit)
        elif isinstance(unit, InterfaceUnit):
            return self.codegen_interface(unit)
        elif isinstance(unit, ImplementationUnit):
            return self.codegen_implementation(unit)
        else:
            raise CodegenError(f'Unknown unit type: {type(unit).__name__}')

    def codegen_program(self, unit: ProgramUnit) -> ir.Module:
        """Codegen for PROGRAM unit."""
        # Import any modules referenced by USES before we codegen the body.
        for use_clause in unit.uses:
            self.codegen_use_clause(use_clause)

        # Codegen all declarations
        for decl in unit.block.decls:
            self.codegen_decl(decl)

        # Create main function if not already defined
        if 'main' not in [f.name for f in self.module.functions]:
            main_type = ir.FunctionType(ir.IntType(32), [])
            main_func = ir.Function(self.module, main_type, name='main')
            entry_block = main_func.append_basic_block(name='entry')
            self.builder = IRBuilder(entry_block)
            self.current_function = main_func

            # Predeclared TEXT files exist even when not listed in the PROGRAM
            # heading. IBM Pascal automatically initializes INPUT/OUTPUT; the
            # later I/O primitives will attach devices, while 8.1 establishes
            # the concrete file-control block and buffer model.
            for sym in list(self.scope.symbols.values()):
                if isinstance(self.resolve_type_alias(sym.type_expr), FileType):
                    self._init_file_storage(sym.llvm_value, sym.type_expr)
            # Execute the program body
            self.codegen_stmt_list(unit.block.body)

            # Default return 0
            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(32), 0))

        return self.module

    def codegen_module(self, unit: ModuleUnit) -> ir.Module:
        """Codegen for MODULE unit."""
        for decl in unit.decls:
            self.codegen_decl(decl)
        return self.module

    def codegen_interface(self, unit: InterfaceUnit) -> ir.Module:
        """Codegen for INTERFACE unit (declarations only)."""
        for decl in unit.decls:
            self.codegen_decl(decl)
        return self.module

    def codegen_implementation(self, unit: ImplementationUnit) -> ir.Module:
        """Codegen for IMPLEMENTATION unit."""
        old_iface = self.current_interface_decls
        self.current_interface_decls = {getattr(decl, 'name', '').lower(): decl for decl in (unit.interface.decls if unit.interface else []) if getattr(decl, 'name', None)}
        try:
            for decl in unit.decls:
                self.codegen_decl(decl)
        finally:
            self.current_interface_decls = old_iface

        # Codegen init body if present
        if unit.init_body:
            init_type = ir.FunctionType(ir.IntType(32), [])
            init_name = f'pascal_init_{unit.name.lower()}'
            init_func = ir.Function(self.module, init_type, name=init_name)
            entry_block = init_func.append_basic_block(name='entry')
            self.builder = IRBuilder(entry_block)
            self.current_function = init_func

            self.codegen_stmt_list(unit.init_body)

            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(32), 0))

        return self.module

    # ========================================================================
    # Declarations
    # ========================================================================

    def codegen_use_clause(self, use_clause: UseClause) -> None:
        """Import declarations from a USES module as external symbols."""
        module_path = None
        import os
        from pathlib import Path
        search_dir = Path(self.source_file).parent if self.source_file else Path('.')
        for candidate in (use_clause.name, use_clause.name.lower(), use_clause.name.upper()):
            for suffix in ('', '.inc', '.pas'):
                path = search_dir / f'{candidate}{suffix}'
                if path.exists():
                    module_path = str(path)
                    break
            if module_path:
                break
        if not module_path:
            return
        ast = parse_file(module_path)
        decls = getattr(ast, 'decls', [])
        for decl in decls:
            if isinstance(decl, (ProcDecl, FuncDecl)) and getattr(decl, 'name', None):
                self.codegen_decl(
                    ProcDecl(decl.name, decl.params, getattr(decl, 'attributes', []), body=None
                             ) if isinstance(decl, ProcDecl) else FuncDecl(decl.name, decl.params, decl.return_type, getattr(decl, 'attributes', []), body=None))

    def codegen_decl(self, decl: Declaration) -> None:
        """Codegen a declaration."""
        names = getattr(decl, 'names', None) or getattr(decl, 'name', '')
        self._log(f'decl  {type(decl).__name__} {names}')
        if isinstance(decl, ConstDecl):
            self.codegen_const_decl(decl)
        elif isinstance(decl, VarDecl):
            self.codegen_var_decl(decl)
        elif isinstance(decl, TypeDecl):
            self.codegen_type_decl(decl)
        elif isinstance(decl, ValueDecl):
            # VALUE declarations don't generate code
            pass
        elif isinstance(decl, LabelDecl):
            # Label declarations don't generate code
            pass
        elif isinstance(decl, ProcDecl):
            self.codegen_proc_decl(decl)
        elif isinstance(decl, FuncDecl):
            self.codegen_func_decl(decl)
        else:
            raise CodegenError(f'Unknown declaration: {type(decl).__name__}')

    def codegen_const_decl(self, decl: ConstDecl) -> None:
        """Codegen for CONST declaration."""
        # Evaluate constant at compile time and remember it so that later
        # uses (array bounds, sizeof, and plain value references) can resolve it.
        value = self.eval_const_expr(decl.value)
        self.constants[decl.name.upper()] = value

    def codegen_type_decl(self, decl: TypeDecl) -> None:
        """Record a type declaration for later codegen lookups."""
        self.type_aliases[decl.name.upper()] = decl.type_expr
        # Enum members become ordinal compile-time constants (0, 1, 2, ...).
        if isinstance(decl.type_expr, EnumType):
            for ordinal, member in enumerate(decl.type_expr.values):
                self.constants[member.upper()] = ordinal
                # Remember which enum each member belongs to so WRITE can print
                # the symbolic name of a bare member literal (checklist 9.8).
                self.enum_member_names[member.upper()] = list(decl.type_expr.values)

    def codegen_var_decl(self, decl: VarDecl) -> None:
        """Codegen for VAR declaration."""
        llvm_type = self.llvm_type(decl.type_expr)
        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        is_static = 'STATIC' in attrs

        # Check if the type is a string type
        is_str, max_len, is_lstring = self.get_string_type_info(decl.type_expr)

        if self.builder and not is_static:
            # Local variable (inside a function) — allocate the aggregate inline
            for name in decl.names:
                alloca = self.builder.alloca(llvm_type, name=name)
                self.scope.define(name, alloca, decl.type_expr)
                if isinstance(decl.type_expr, FileType) or (isinstance(decl.type_expr, NamedType) and decl.type_expr.name.upper() == 'TEXT'):
                    self._init_file_storage(alloca, decl.type_expr)

                if is_str:
                    # Initialize length byte to 0 for LSTRING
                    if is_lstring:
                        zero = ir.Constant(ir.IntType(32), 0)
                        len_ptr = self.builder.gep(alloca, [zero, zero])
                        self.builder.store(ir.Constant(ir.IntType(8), 0), len_ptr)
                    # STRING: no initialization needed (chars are undefined until assigned)
        else:
            # Static or global variable — allocate aggregate with zero init
            prefix = self.current_function.name if self.current_function else 'global'
            for name in decl.names:
                gv_name = name if not self.builder else f'{prefix}.{name}'

                # Create global variable with the aggregate type
                global_var = ir.GlobalVariable(self.module, llvm_type, name=gv_name)

                if is_str:
                    if is_lstring:
                        # Initialize with zero (length 0, rest undefined)
                        global_var.initializer = ir.Constant(llvm_type, bytearray(llvm_type.count))
                    else:
                        # STRING: initialize with blanks (0x20)
                        init_bytes = bytearray([0x20] * llvm_type.count)
                        global_var.initializer = ir.Constant(llvm_type, init_bytes)
                else:
                    global_var.initializer = self.zero_initializer(llvm_type)

                self.scope.define(name, global_var, decl.type_expr)

    def codegen_proc_decl(self, decl: ProcDecl) -> None:
        """Codegen for PROCEDURE declaration."""
        effective_decl = decl
        iface_decl = self.current_interface_decls.get(decl.name.lower()) if decl.name else None
        if iface_decl and not decl.params:
            effective_decl = iface_decl

        # Flatten parameter types: reference modes are passed as LLVM pointers.
        param_types = []
        flat_modes = []
        for param in effective_decl.params:
            param_type = self.param_llvm_type(param)
            for _ in param.names:
                param_types.append(param_type)
                flat_modes.append(param.mode)
        func_type = ir.FunctionType(ir.IntType(32), param_types)

        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        existing = self.scope.lookup(decl.name)
        if existing and isinstance(existing.llvm_value, ir.Function):
            func = existing.llvm_value
            if func.function_type != func_type:
                raise CodegenError(f"Procedure '{decl.name}' already declared with a different signature")
        else:
            # Create function
            func = ir.Function(self.module, func_type, name=decl.name)
        if attrs.intersection({'PUBLIC', 'EXTERN', 'EXTERNAL'}):
            func.linkage = 'external'
        self.proc_param_modes[decl.name.lower()] = flat_modes
        self.scope.define(decl.name, func, None)

        # If no body, it's extern/forward
        if not decl.body:
            return

        # Create entry block
        entry_block = func.append_basic_block(name='entry')
        prev_builder = self.builder
        prev_func = self.current_function
        prev_scope = self.scope

        self.builder = IRBuilder(entry_block)
        self.current_function = func
        self.scope = Scope(parent=prev_scope)

        # Bind parameters to the scope
        args_iter = iter(func.args)
        for param in effective_decl.params:
            for name in param.names:
                arg = next(args_iter)
                arg.name = name
                self.scope.define(name, arg, param.type_expr, is_parameter=param.mode not in {'VAR', 'VARS', 'CONST', 'CONSTS'})

        # Codegen body
        for inner_decl in decl.body.decls:
            self.codegen_decl(inner_decl)

        for stmt in decl.body.body:
            self.codegen_stmt(stmt)

        # Default return
        self.builder.ret(ir.Constant(ir.IntType(32), 0))

        # Restore context
        self.builder = prev_builder
        self.current_function = prev_func
        self.scope = prev_scope

    def codegen_func_decl(self, decl: FuncDecl) -> None:
        """Codegen for FUNCTION declaration."""
        effective_decl = decl
        iface_decl = self.current_interface_decls.get(decl.name.lower()) if decl.name else None
        if iface_decl and not decl.params:
            effective_decl = iface_decl

        # Flatten parameter types: reference modes are passed as LLVM pointers.
        param_types = []
        flat_modes = []
        for param in effective_decl.params:
            param_type = self.param_llvm_type(param)
            for _ in param.names:
                param_types.append(param_type)
                flat_modes.append(param.mode)
        return_type = self.llvm_type(decl.return_type)
        func_type = ir.FunctionType(return_type, param_types)

        # Create function
        func = ir.Function(self.module, func_type, name=decl.name)
        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        if attrs.intersection({'PUBLIC', 'EXTERN', 'EXTERNAL'}):
            func.linkage = 'external'
        self.proc_param_modes[decl.name.lower()] = flat_modes
        self.scope.define(decl.name, func, decl.return_type)

        # If no body, it's extern/forward
        if not decl.body:
            return

        # Create entry block
        entry_block = func.append_basic_block(name='entry')
        prev_builder = self.builder
        prev_func = self.current_function
        prev_scope = self.scope

        self.builder = IRBuilder(entry_block)
        self.current_function = func
        self.scope = Scope(parent=prev_scope)

        # Bind parameters
        args_iter = iter(func.args)
        for param in effective_decl.params:
            for name in param.names:
                arg = next(args_iter)
                arg.name = name
                self.scope.define(name, arg, param.type_expr, is_parameter=param.mode not in {'VAR', 'VARS', 'CONST', 'CONSTS'})

        # Allocate space for return value
        return_alloca = self.builder.alloca(return_type, name='return_value')
        self.scope.define(decl.name, return_alloca, decl.return_type)
        self.builder.store(ir.Constant(return_type, 0.0) if isinstance(return_type, ir.DoubleType) else ir.Constant(return_type, 0), return_alloca)

        # Codegen body
        for inner_decl in decl.body.decls:
            self.codegen_decl(inner_decl)

        self.codegen_stmt_list(decl.body.body)

        # Default return / function result
        if not self.builder.block.is_terminated:
            result = self.builder.load(return_alloca)
            self.builder.ret(result)

        # Restore context
        self.builder = prev_builder
        self.current_function = prev_func
        self.scope = prev_scope

    # ========================================================================
    # Statements
    # ========================================================================
