"""
IO_WRITE_READ mixin for Codegen.

I/O operations: WRITE, WRITELN, READ, READLN
"""

from __future__ import annotations

import llvmlite.ir as ir
from typing import List, Union, Optional

from ast_nodes import *
from type_system import EnumType as ResolvedEnumType, LStringType, StringType, REAL_TYPE, WORD_TYPE, INTEGER_TYPE, CHAR_TYPE
from ast_nodes import LStringType as ASTLStringType
from ast_nodes import EnumType as ASTEnumType


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
            return getattr(sym, 'type_expr', None) if sym else None
        return self.infer_expression_type(expr) if hasattr(self, 'infer_expression_type') else None

    def build_write_format_and_args(self, args: List[Union[Expression, WriteArg]]) -> tuple[str, List[ir.Value]]:
        fmt_parts: List[str] = []
        printf_args: List[ir.Value] = []
        for arg in args:
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
                    printf_args.extend([self.coerce_printf_int(self.codegen_expr(width)) if width is not None else ir.Constant(ir.IntType(32), 0), self.coerce_printf_int(self.codegen_expr(precision)) if precision is not None else length, val])
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
                    printf_args.extend([self.coerce_printf_int(self.codegen_expr(width)) if width is not None else ir.Constant(ir.IntType(32), 0), self.coerce_printf_int(self.codegen_expr(precision)) if precision is not None else ir.Constant(ir.IntType(32), 0), self.builder.sitofp(val, ir.DoubleType()) if str(val.type) != 'double' else val])
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
        return ''.join(fmt_parts), printf_args

    def builtin_write(self, args):
        fmt_str, printf_args = self.build_write_format_and_args(args)
        fmt_str = fmt_str or ''
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode() + b'\0'))
        g = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        g.initializer = fmt_const
        g.global_constant = True
        ptr = self.builder.gep(g, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
        self.builder.call(self.printf_func(), [ptr] + printf_args)

    def builtin_writeln(self, args):
        fmt_str, printf_args = self.build_write_format_and_args(args)
        fmt_str += "\n"
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode() + b'\0'))
        g = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        g.initializer = fmt_const
        g.global_constant = True
        ptr = self.builder.gep(g, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
        self.builder.call(self.printf_func(), [ptr] + printf_args)

    def _read_ptr(self, arg):
        return self.resolve_designator_ptr(arg if isinstance(arg, Designator) else Designator(arg.name, []))

    def builtin_readln(self, args):
        self._builtin_read(args, True)

    def builtin_read(self, args):
        self._builtin_read(args, False)

    def _builtin_read(self, args, consume_eol):
        for arg in args:
            target = arg if isinstance(arg, Designator) else Designator(arg.name, [])
            ptr = self.resolve_designator_ptr(target)
            ty = self.resolve_type_alias(self._pas_type(arg)) if self._pas_type(arg) is not None else None
            ty_name = str(ty).upper() if ty is not None else ''
            if ty is INTEGER_TYPE or ty_name == 'INTEGER':
                fn = self._read_helper('pas_read_int', ptr.type)
                call_args = [ptr]
            elif ty is WORD_TYPE or ty_name == 'WORD':
                fn = self._read_helper('pas_read_word', ptr.type)
                call_args = [ptr]
            elif ty is REAL_TYPE or ty_name == 'REAL':
                fn = self._read_helper('pas_read_real', ptr.type)
                call_args = [ptr]
            elif ty is CHAR_TYPE or ty_name == 'CHAR':
                fn = self._read_helper('pas_read_char', ptr.type)
                call_args = [ptr]
            else:
                fn = self._read_helper('pas_read_lstring', ir.IntType(8).as_pointer(), [ir.IntType(32)])
                is_str, max_len, is_lstring = self.get_string_type_info(ty)
                call_args = [self.builder.bitcast(ptr, ir.IntType(8).as_pointer()), ir.Constant(ir.IntType(32), max_len)]
            self.builder.call(fn, call_args)
        if consume_eol:
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
