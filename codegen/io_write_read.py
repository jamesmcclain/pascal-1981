"""
IO_WRITE_READ mixin for Codegen.

I/O operations: WRITE, WRITELN, READ, READLN
"""

from __future__ import annotations

from typing import List, Optional, Union

import llvmlite.ir as ir

from ast_nodes import EnumType as ASTEnumType
from ast_nodes import LStringType as ASTLStringType
from ast_nodes import *
from codegen.base import CodegenError
from type_system import CHAR_TYPE, INTEGER_TYPE, REAL_TYPE, WORD_TYPE
from type_system import EnumType as ResolvedEnumType
from type_system import FileType as ResolvedFileType
from type_system import LStringType, StringType


class IoWriteReadMixin:

    def printf_func(self) -> ir.Function:
        if 'printf' not in [f.name for f in self.module.functions]:
            ty = ir.FunctionType(ir.IntType(32), [ir.PointerType(ir.IntType(8))], var_arg=True)
            ir.Function(self.module, ty, name='printf')
        return next(f for f in self.module.functions if f.name == 'printf')

    def _scanf_like_func(self, name: str, ret_ty=ir.IntType(32)) -> ir.Function:
        for f in self.module.functions:
            if f.name == name:
                return f
        fn_ty = ir.FunctionType(ret_ty, [ir.PointerType(ir.IntType(8))], var_arg=True)
        return ir.Function(self.module, fn_ty, name=name)

    def _read_helper(self, name: str, llvm_ptr_ty: ir.Type, extra: Optional[List[ir.Type]] = None) -> ir.Function:
        for f in self.module.functions:
            if f.name == name:
                return f
        extra = extra or []
        fn_ty = ir.FunctionType(ir.IntType(32), [llvm_ptr_ty] + extra)
        fn = ir.Function(self.module, fn_ty, name=name)
        fn.linkage = 'external'
        return fn

    def _pas_type(self, expr) -> Optional[object]:
        if isinstance(expr, (Identifier, Designator)):
            sym = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            ty = getattr(sym, 'type_expr', None) if sym else None
            if isinstance(expr, Designator) and expr.selectors and ty is not None:
                cur = ty
                for sel in expr.selectors:
                    if sel.kind == 'FIELD' and isinstance(self.resolve_type_alias(cur), (ResolvedFileType, FileType)):
                        if str(sel.index_or_field).upper() == 'MODE':
                            return NamedType('FILEMODES', None)
                    # Fall back to the base type for complex selectors this helper does not model.
            return ty
        return self.infer_expression_type(expr) if hasattr(self, 'infer_expression_type') else None

    def _file_selector_fcb(self, expr) -> ir.Value:
        target = expr if isinstance(expr, Designator) else Designator(expr.name, [])
        slot = self.resolve_designator_ptr(target)
        handle = self.builder.load(slot)
        fcb = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
        if getattr(expr, 'name', '').upper() in {'INPUT', 'OUTPUT'}:
            in_sym = self.scope.lookup('INPUT')
            out_sym = self.scope.lookup('OUTPUT')
            in_fcb = self.builder.bitcast(self.builder.load(in_sym.llvm_value), self.file_fcb_type().as_pointer())
            out_fcb = self.builder.bitcast(self.builder.load(out_sym.llvm_value), self.file_fcb_type().as_pointer())
            self.builder.call(self.scope.lookup('pas_file_attach_std').llvm_value, [in_fcb, out_fcb])
        return fcb

    def build_write_format_and_args(self, args: List[Union[Expression, WriteArg]]) -> tuple[str, List[ir.Value], Optional[ir.Value]]:
        fmt_parts: List[str] = []
        printf_args: List[ir.Value] = []
        start = 0
        file_fcb = None
        if args:
            first = args[0]
            first_expr = first.expr if isinstance(first, WriteArg) else first
            first_ty = self.resolve_type_alias(self._pas_type(first_expr)) if self._pas_type(first_expr) is not None else None
            if isinstance(first_ty, (ResolvedFileType, FileType)) and getattr(first_ty, 'structure', None) == 'ASCII':
                file_fcb = self._file_selector_fcb(first_expr)
                start = 1
        for arg in args[start:]:
            expr = arg.expr if isinstance(arg, WriteArg) else arg
            width = arg.width if isinstance(arg, WriteArg) else None
            precision = arg.precision if isinstance(arg, WriteArg) else None
            val = self.codegen_expr(expr)
            pas_ty = self._pas_type(expr)

            enum_names = self.write_enum_names(expr)
            if enum_names is not None:
                table = self.enum_name_table(enum_names)
                zero = ir.Constant(ir.IntType(32), 0)
                val = self.builder.load(self.builder.gep(table, [zero, val]))
                pas_ty = None

            if isinstance(pas_ty, (StringType, LStringType, ASTLStringType)):
                zero = ir.Constant(ir.IntType(32), 0)
                if isinstance(pas_ty, (LStringType, ASTLStringType)):
                    length = self.builder.zext(self.builder.load(self.builder.gep(val, [zero, zero])), ir.IntType(32))
                    val = self.builder.gep(val, [zero, ir.Constant(ir.IntType(32), 1)])
                else:
                    length = ir.Constant(ir.IntType(32), getattr(pas_ty, 'max_len', 256))
                    val = self.builder.gep(val, [zero, zero])
                if width is None and precision is None:
                    fmt_parts.append('%.*s')
                    printf_args.extend([length, val])
                elif width is not None and precision is None:
                    fmt_parts.append('%*.*s')
                    printf_args.extend([self.coerce_printf_int(self.codegen_expr(width)), length, val])
                else:
                    fmt_parts.append('%*.*s')
                    printf_args.extend([
                        self.coerce_printf_int(self.codegen_expr(width)) if width is not None else ir.Constant(ir.IntType(32), 0),
                        self.coerce_printf_int(self.codegen_expr(precision)) if precision is not None else length, val
                    ])
                continue

            if isinstance(pas_ty, type(REAL_TYPE)) or str(val.type) in {'double', 'float'}:
                if width is None and precision is None:
                    fmt_parts.append('%14.7E')
                    printf_args.append(self.builder.sitofp(val, ir.DoubleType()) if str(val.type) != 'double' else val)
                elif width is not None and precision is None:
                    fmt_parts.append('%*E')
                    printf_args.extend([self.coerce_printf_int(self.codegen_expr(width)), self.builder.sitofp(val, ir.DoubleType()) if str(val.type) != 'double' else val])
                else:
                    fmt_parts.append('%*.*f')
                    printf_args.extend([
                        # P::N — width omitted means the default 14-char field
                        # (vintage D-002 output: '        123.46' for ::2).
                        self.coerce_printf_int(self.codegen_expr(width)) if width is not None else ir.Constant(ir.IntType(32), 14),
                        self.coerce_printf_int(self.codegen_expr(precision)) if precision is not None else ir.Constant(ir.IntType(32), 0),
                        self.builder.sitofp(val, ir.DoubleType()) if str(val.type) != 'double' else val
                    ])
                continue

            if str(val.type) == 'i8':
                conv = 'c'
            elif str(val.type) == 'i1':
                conv = 'd'
                val = self.builder.zext(val, ir.IntType(32))
            elif str(val.type) == 'i16':
                conv = 'u'
                val = self.builder.zext(val, ir.IntType(32))
            elif str(val.type) == 'i32':
                conv = 'd'
            else:
                conv = 's'

            if width is not None:
                fmt_parts.append(f'%*{conv}')
                printf_args.append(self.coerce_printf_int(self.codegen_expr(width)))
            else:
                fmt_parts.append(f'%{conv}')
            printf_args.append(val)
        return ''.join(fmt_parts), printf_args, file_fcb

    def builtin_write(self, args):
        fmt_str, printf_args, file_fcb = self.build_write_format_and_args(args)
        fmt_str = fmt_str or ''
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode() + b'\0'))
        g = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        g.initializer = fmt_const
        g.global_constant = True
        ptr = self.builder.gep(g, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
        if file_fcb is None:
            self.builder.call(self.printf_func(), [ptr] + printf_args)
        else:
            self.builder.call(self.scope.lookup('pas_write_fmt').llvm_value, [file_fcb, ptr] + printf_args)

    def builtin_writeln(self, args):
        fmt_str, printf_args, file_fcb = self.build_write_format_and_args(args)
        fmt_str += "\n"
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode() + b'\0'))
        g = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        g.initializer = fmt_const
        g.global_constant = True
        ptr = self.builder.gep(g, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
        if file_fcb is None:
            self.builder.call(self.printf_func(), [ptr] + printf_args)
        else:
            self.builder.call(self.scope.lookup('pas_write_fmt').llvm_value, [file_fcb, ptr] + printf_args)

    def _read_ptr(self, arg):
        return self.resolve_designator_ptr(arg if isinstance(arg, Designator) else Designator(arg.name, []))

    def builtin_readln(self, args):
        self._builtin_read(args, True)

    def builtin_read(self, args):
        self._builtin_read(args, False)

    def _emit_read_target(self, arg, file_fcb):
        target = arg if isinstance(arg, Designator) else Designator(arg.name, [])
        ptr = self.resolve_designator_ptr(target)
        ty = self.resolve_type_alias(self._pas_type(arg)) if self._pas_type(arg) is not None else None
        if isinstance(ty, NamedType):
            ty_name = ty.name.upper()
        elif ty is not None:
            ty_name = getattr(ty, 'name', type(ty).__name__).upper()
        else:
            ty_name = ''
        if ty is INTEGER_TYPE or ty_name == 'INTEGER':
            fn = self._read_helper('pas_fread_int' if file_fcb is not None else 'pas_read_int', ptr.type)
            call_args = ([file_fcb, ptr] if file_fcb is not None else [ptr])
        elif ty is WORD_TYPE or ty_name == 'WORD':
            fn = self._read_helper('pas_fread_word' if file_fcb is not None else 'pas_read_word', ptr.type)
            call_args = ([file_fcb, ptr] if file_fcb is not None else [ptr])
        elif ty is REAL_TYPE or ty_name == 'REAL':
            fn = self._read_helper('pas_fread_real' if file_fcb is not None else 'pas_read_real', ptr.type)
            call_args = ([file_fcb, ptr] if file_fcb is not None else [ptr])
        elif ty is CHAR_TYPE or ty_name == 'CHAR':
            fn = self._read_helper('pas_fread_char' if file_fcb is not None else 'pas_read_char', ptr.type)
            call_args = ([file_fcb, ptr] if file_fcb is not None else [ptr])
        else:
            is_str, max_len, is_lstring = self.get_string_type_info(ty)
            if not is_str or not is_lstring:
                type_label = ty_name or (getattr(ty, 'name', type(ty).__name__) if ty is not None else 'UNKNOWN')
                raise CodegenError(f"READ/READLN cannot read a value of type {type_label}")
            if file_fcb is not None:
                fn = self.scope.lookup('pas_fread_lstring').llvm_value
                call_args = [file_fcb, self.builder.bitcast(ptr, ir.IntType(8).as_pointer()), ir.Constant(ir.IntType(32), max_len)]
            else:
                fn = self._read_helper('pas_read_lstring', ir.IntType(8).as_pointer(), [ir.IntType(32)])
                call_args = [self.builder.bitcast(ptr, ir.IntType(8).as_pointer()), ir.Constant(ir.IntType(32), max_len)]
        self.builder.call(fn, call_args)

    def _default_input_fcb(self) -> ir.Value:
        return self._file_selector_fcb(Identifier('INPUT'))

    def builtin_readset(self, args):
        file_fcb = self._default_input_fcb()
        start = 0
        if len(args) == 3:
            file_fcb = self._file_selector_fcb(args[0])
            start = 1
        dest = args[start]
        set_expr = args[start + 1]
        dest_ptr = self.resolve_designator_ptr(dest if isinstance(dest, Designator) else Designator(dest.name, []))
        _is_str, max_len, is_lstring = self.get_string_type_info(self._pas_type(dest))
        if not is_lstring:
            raise CodegenError('READSET destination must be LSTRING')
        set_val = self.codegen_expr(set_expr)
        set_slot = self.builder.alloca(self.set_llvm_type(), name='readset_set')
        self.builder.store(set_val, set_slot)
        self.builder.call(self.scope.lookup('pas_freadset').llvm_value, [file_fcb, self.builder.bitcast(dest_ptr, ir.IntType(8).as_pointer()), ir.Constant(ir.IntType(32), max_len), set_slot])

    def builtin_readfn(self, args):
        file_fcb = self._default_input_fcb()
        start = 0
        if args:
            first_ty = self.resolve_type_alias(self._pas_type(args[0])) if self._pas_type(args[0]) is not None else None
            if isinstance(first_ty, (ResolvedFileType, FileType)) and getattr(first_ty, 'structure', None) == 'ASCII':
                file_fcb = self._file_selector_fcb(args[0])
                start = 1
        for arg in args[start:]:
            ty = self.resolve_type_alias(self._pas_type(arg)) if self._pas_type(arg) is not None else None
            if isinstance(ty, (ResolvedFileType, FileType)):
                target_fcb = self._file_selector_fcb(arg)
                self.builder.call(self.scope.lookup('pas_fread_filename').llvm_value, [file_fcb, target_fcb])
            else:
                self._emit_read_target(arg, file_fcb)

    def _builtin_read(self, args, consume_eol):
        file_fcb = None
        start = 0
        if args:
            first_ty = self.resolve_type_alias(self._pas_type(args[0])) if self._pas_type(args[0]) is not None else None
            if isinstance(first_ty, (ResolvedFileType, FileType)) and getattr(first_ty, 'structure', None) == 'ASCII':
                file_fcb = self._file_selector_fcb(args[0])
                start = 1
        for arg in args[start:]:
            self._emit_read_target(arg, file_fcb)
        if consume_eol:
            if file_fcb is not None:
                self.builder.call(self.scope.lookup('pas_freadln_skip').llvm_value, [file_fcb])
            else:
                self.builder.call(self._read_helper('pas_readln_skip', ir.VoidType()), [])

    def enum_value_list(self, type_expr) -> Optional[List[str]]:
        t = self.resolve_type_alias(type_expr)
        if isinstance(t, (ResolvedEnumType, ASTEnumType)):
            return list(t.values)
        return None

    def write_enum_names(self, expr) -> Optional[List[str]]:
        if isinstance(expr, (Identifier, Designator)) and not getattr(expr, 'selectors', None):
            sym = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if sym is not None and sym.type_expr is not None:
                names = self.enum_value_list(sym.type_expr)
                if names:
                    return names
            return self.enum_member_names.get(expr.name.upper())
        return None

    def enum_name_table(self, names: List[str]) -> ir.GlobalVariable:
        key = '\x00'.join(names)
        cached = self._enum_name_tables.get(key)
        if cached is not None:
            return cached
        i8 = ir.IntType(8)
        i8p = ir.PointerType(i8)
        zero = ir.Constant(ir.IntType(32), 0)
        ptrs = []
        for nm in names:
            data = bytearray(nm.encode('utf-8') + b'\0')
            const = ir.Constant(ir.ArrayType(i8, len(data)), data)
            g = ir.GlobalVariable(self.module, const.type, name=self.unique_name('enumname'))
            g.initializer = const
            g.global_constant = True
            ptrs.append(g.gep([zero, zero]))
        table_type = ir.ArrayType(i8p, len(names))
        table = ir.GlobalVariable(self.module, table_type, name=self.unique_name('enumtab'))
        table.global_constant = True
        table.initializer = ir.Constant(table_type, ptrs)
        self._enum_name_tables[key] = table
        return table
