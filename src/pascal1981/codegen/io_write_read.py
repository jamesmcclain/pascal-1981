"""
IO_WRITE_READ mixin for Codegen.

I/O operations: WRITE, WRITELN, READ, READLN
"""

from __future__ import annotations

from typing import List, Optional, Union

import llvmlite.ir as ir

from ..ast_nodes import EnumType as ASTEnumType
from ..ast_nodes import LStringType as ASTLStringType
from ..ast_nodes import *
from ..type_system import (BOOLEAN_TYPE, CHAR_TYPE, INTEGER32_TYPE, INTEGER64_TYPE, INTEGER_TYPE, REAL_TYPE, WORD_TYPE, WORD32_TYPE, WORD64_TYPE)
from ..type_system import EnumType as ResolvedEnumType
from ..type_system import FileType as ResolvedFileType
from ..type_system import LStringType, StringType
from .base import CodegenError


class IoWriteReadMixin:

    def printf_func(self) -> ir.Function:
        return self.runtime_extern('printf')

    def _scanf_like_func(self, name: str, ret_ty=ir.IntType(32)) -> ir.Function:
        return self.runtime_extern(name)

    def _read_helper(self, name: str, llvm_ptr_ty: ir.Type, extra: Optional[List[ir.Type]] = None) -> ir.Function:
        # Canonical signatures live in CodegenBase._build_extern_factories.
        # In particular, file reads include the leading FCB pointer while stdin
        # reads do not; callers supply only the call arguments.
        return self.runtime_extern(name)

    def _runtime_func(self, name: str, ret_ty: ir.Type, arg_tys: List[ir.Type]) -> ir.Function:
        return self.runtime_extern(name)

    def _pas_type(self, expr) -> Optional[object]:
        if isinstance(expr, (Identifier, Designator)):
            sym = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            ty = getattr(sym, 'type_expr', None) if sym else None
            if isinstance(expr, Designator) and expr.selectors and ty is not None:
                cur = ty
                for sel in expr.selectors:
                    if sel.kind == 'FIELD' and isinstance(self.resolve_type_alias(cur), (ResolvedFileType, FileType)):
                        field = str(sel.index_or_field).upper()
                        if field == 'MODE':
                            return NamedType('FILEMODES', None)
                        if field == 'TRAP':
                            return NamedType('BOOLEAN', None)
                        if field == 'ERRS':
                            return NamedType('INTEGER', None)
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
            self.builder.call(self.runtime_extern('pas_file_attach_std'), [in_fcb, out_fcb])
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
            if enum_names is not None and self.feature_enabled('symbolic-enum-io'):
                table = self.enum_name_table(enum_names)
                zero = ir.Constant(ir.IntType(32), 0)
                names_ptr = self.builder.gep(table, [zero, zero])
                fn = self._runtime_func('pas_enum_write_token', ir.IntType(8).as_pointer(), [ir.IntType(32), ir.IntType(8).as_pointer().as_pointer(), ir.IntType(32)])
                val = self.builder.call(fn, [self.coerce_printf_int(val), names_ptr, ir.Constant(ir.IntType(32), len(enum_names))])
                pas_ty = None

            if self._is_boolean_pas_type(pas_ty) or str(val.type) == 'i1':
                false_s, true_s = self._boolean_name_constants()
                is_true = self.builder.icmp_unsigned('!=', val, ir.Constant(val.type, 0))
                val = self.builder.select(is_true, true_s, false_s)
                pas_ty = None

            is_str_like, str_len, is_lstring_like = self.get_string_type_info(pas_ty)
            if isinstance(pas_ty, (StringType, LStringType, ASTLStringType)) or is_str_like:
                zero = ir.Constant(ir.IntType(32), 0)
                if isinstance(pas_ty, (LStringType, ASTLStringType)) or is_lstring_like:
                    length = self.builder.zext(self.builder.load(self.builder.gep(val, [zero, zero])), ir.IntType(32))
                    val = self.builder.gep(val, [zero, ir.Constant(ir.IntType(32), 1)])
                else:
                    length = ir.Constant(ir.IntType(32), str_len)
                    val = self.builder.gep(val, [zero, zero])
                if width is None and precision is None:
                    fmt_parts.append('%.*s')
                    printf_args.extend([length, val])
                elif width is not None and precision is None:
                    fmt_parts.append('%*.*s')
                    printf_args.extend([self.coerce_printf_int(self.codegen_expr(width)), length, val])
                elif precision is not None and not self.feature_enabled('string-precision'):
                    # Faithful 1981 default: ::N precision is IGNORED on
                    # STRING/LSTRING values (D-011 — vintage prints the whole
                    # string; `s::3` on 'ABCDE' -> 'ABCDE', not 'ABC'). Fall
                    # back to the same lowering as if no precision were given:
                    # P:M:N pads to width M and ignores N; P::N prints the
                    # whole value at the default width. Opt in to the
                    # truncating behavior with -f string-precision.
                    if width is not None:
                        fmt_parts.append('%*.*s')
                        printf_args.extend([self.coerce_printf_int(self.codegen_expr(width)), length, val])
                    else:
                        fmt_parts.append('%.*s')
                        printf_args.extend([length, val])
                else:
                    # -f string-precision: honor ::N by truncating to N chars
                    # (extension; not 1981 behavior).
                    fmt_parts.append('%*.*s')
                    printf_args.extend([
                        self.coerce_printf_int(self.codegen_expr(width)) if width is not None else ir.Constant(ir.IntType(32), 0),
                        self.coerce_printf_int(self.codegen_expr(precision)) if precision is not None else length, val
                    ])
                continue

            if isinstance(pas_ty, type(REAL_TYPE)) or str(val.type) in {'double', 'float'}:

                def _to_printf_double(v):
                    # printf's %E/%f variadic slot is a C double. Widen any
                    # narrower real or integer correctly: f32 via fpext, an
                    # integer via sitofp, a double passes through.
                    if isinstance(v.type, ir.FloatType):
                        return self.builder.fpext(v, ir.DoubleType())
                    if isinstance(v.type, ir.IntType):
                        return self.builder.sitofp(v, ir.DoubleType())
                    return v

                if width is None and precision is None:
                    fmt_parts.append('%14.7E')
                    printf_args.append(_to_printf_double(val))
                elif width is not None and precision is None:
                    fmt_parts.append('%*E')
                    printf_args.extend([self.coerce_printf_int(self.codegen_expr(width)), _to_printf_double(val)])
                else:
                    fmt_parts.append('%*.*f')
                    printf_args.extend([
                        # P::N — width omitted means the default 14-char field
                        # (vintage output: '        123.46' for ::2).
                        self.coerce_printf_int(self.codegen_expr(width)) if width is not None else ir.Constant(ir.IntType(32), 14),
                        self.coerce_printf_int(self.codegen_expr(precision)) if precision is not None else ir.Constant(ir.IntType(32), 0),
                        _to_printf_double(val)
                    ])
                continue

            if str(val.type) == 'i8':
                conv = 'c'
            elif str(val.type) == 'i1':
                conv = 'd'
                val = self.builder.zext(val, ir.IntType(32))
            elif str(val.type) == 'i16':
                ty_name = pas_ty.name.upper() if isinstance(pas_ty, (NamedType, BuiltinType)) else ''
                if pas_ty is WORD_TYPE or ty_name in ('WORD', 'WORD16'):
                    conv = 'u'
                    val = self.builder.zext(val, ir.IntType(32))
                else:
                    conv = 'd'
                    val = self.builder.sext(val, ir.IntType(32))
            elif str(val.type) == 'i32':
                ty_name = pas_ty.name.upper() if isinstance(pas_ty, (NamedType, BuiltinType)) else ''
                conv = 'u' if (pas_ty is WORD32_TYPE or ty_name == 'WORD32') else 'd'
            elif str(val.type) == 'i64':
                ty_name = pas_ty.name.upper() if isinstance(pas_ty, (NamedType, BuiltinType)) else ''
                conv = 'llu' if (pas_ty is WORD64_TYPE or ty_name == 'WORD64') else 'lld'
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
            self.builder.call(self.runtime_extern('pas_write_fmt'), [file_fcb, ptr] + printf_args)

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
            self.builder.call(self.runtime_extern('pas_write_fmt'), [file_fcb, ptr] + printf_args)

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
            tmp = self.builder.alloca(ir.IntType(32), name='read_int_tmp')
            fn = self._read_helper('pas_fread_int' if file_fcb is not None else 'pas_read_int', tmp.type)
            call_args = ([file_fcb, tmp] if file_fcb is not None else [tmp])
            self.builder.call(fn, call_args)
            val = self.builder.trunc(self.builder.load(tmp), ptr.type.pointee)
            self.builder.store(val, ptr)
            return
        elif ty is WORD_TYPE or ty_name == 'WORD':
            fn = self._read_helper('pas_fread_word' if file_fcb is not None else 'pas_read_word', ptr.type)
            call_args = ([file_fcb, ptr] if file_fcb is not None else [ptr])
        elif ty is REAL_TYPE or ty_name == 'REAL':
            fn = self._read_helper('pas_fread_real' if file_fcb is not None else 'pas_read_real', ptr.type)
            call_args = ([file_fcb, ptr] if file_fcb is not None else [ptr])
        elif ty is CHAR_TYPE or ty_name == 'CHAR':
            fn = self._read_helper('pas_fread_char' if file_fcb is not None else 'pas_read_char', ptr.type)
            call_args = ([file_fcb, ptr] if file_fcb is not None else [ptr])
        elif isinstance(ty, (ResolvedEnumType, ASTEnumType)):
            if self.feature_enabled('symbolic-enum-io'):
                names = self.enum_value_list(ty)
                table = self.enum_name_table(names or [])
                zero = ir.Constant(ir.IntType(32), 0)
                names_ptr = self.builder.gep(table, [zero, zero]) if names else ir.Constant(ir.IntType(8).as_pointer().as_pointer(), None)
                tmp = self.builder.alloca(ir.IntType(32), name='read_enum_tmp')
                if file_fcb is not None:
                    fn = self._runtime_func(
                        'pas_fread_enum_name', ir.IntType(32),
                        [self.file_fcb_type().as_pointer(), ir.IntType(32).as_pointer(),
                         ir.IntType(8).as_pointer().as_pointer(),
                         ir.IntType(32)])
                    call_args = [file_fcb, tmp, names_ptr, ir.Constant(ir.IntType(32), len(names or []))]
                else:
                    fn = self._runtime_func('pas_read_enum_name', ir.IntType(32), [ir.IntType(32).as_pointer(), ir.IntType(8).as_pointer().as_pointer(), ir.IntType(32)])
                    call_args = [tmp, names_ptr, ir.Constant(ir.IntType(32), len(names or []))]
                self.builder.call(fn, call_args)
                loaded = self.builder.load(tmp)
                val = loaded if loaded.type == ptr.type.pointee else self.builder.trunc(loaded, ptr.type.pointee)
                self.builder.store(val, ptr)
                return
            tmp = self.builder.alloca(ir.IntType(32), name='read_enum_tmp')
            fn = self._read_helper('pas_fread_int' if file_fcb is not None else 'pas_read_int', tmp.type)
            call_args = ([file_fcb, tmp] if file_fcb is not None else [tmp])
            self.builder.call(fn, call_args)
            loaded = self.builder.load(tmp)
            val = loaded if loaded.type == ptr.type.pointee else self.builder.trunc(loaded, ptr.type.pointee)
            self.builder.store(val, ptr)
            return
        else:
            is_str, max_len, is_lstring = self.get_string_type_info(ty)
            if not is_str:
                type_label = ty_name or (getattr(ty, 'name', type(ty).__name__) if ty is not None else 'UNKNOWN')
                raise CodegenError(f"READ/READLN cannot read a value of type {type_label}")
            if is_lstring:
                if file_fcb is not None:
                    fn = self.runtime_extern('pas_fread_lstring')
                    call_args = [file_fcb, self.builder.bitcast(ptr, ir.IntType(8).as_pointer()), ir.Constant(ir.IntType(32), max_len)]
                else:
                    fn = self._read_helper('pas_read_lstring', ir.IntType(8).as_pointer(), [ir.IntType(32)])
                    call_args = [self.builder.bitcast(ptr, ir.IntType(8).as_pointer()), ir.Constant(ir.IntType(32), max_len)]
            else:
                # STRING(n): read up to n characters, stopping at the line
                # marker (left unconsumed); the remainder is blank-padded.
                # [INFERRED] from the dialect's STRING blank-pad convention
                # and the LSTRING reader; vintage stop/consume behavior is a
                # differential-probe candidate.
                if file_fcb is not None:
                    fn = self.runtime_extern('pas_fread_string')
                    call_args = [file_fcb, self.builder.bitcast(ptr, ir.IntType(8).as_pointer()), ir.Constant(ir.IntType(32), max_len)]
                else:
                    fn = self._read_helper('pas_read_string', ir.IntType(8).as_pointer(), [ir.IntType(32)])
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
        self.builder.call(self.runtime_extern('pas_freadset'),
                          [file_fcb, self.builder.bitcast(dest_ptr,
                                                          ir.IntType(8).as_pointer()),
                           ir.Constant(ir.IntType(32), max_len), set_slot])

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
                self.builder.call(self.runtime_extern('pas_fread_filename'), [file_fcb, target_fcb])
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
                self.builder.call(self.runtime_extern('pas_freadln_skip'), [file_fcb])
            else:
                self.builder.call(self._read_helper('pas_readln_skip', ir.VoidType()), [])

    def _is_boolean_pas_type(self, pas_ty) -> bool:
        if pas_ty is BOOLEAN_TYPE:
            return True
        if isinstance(pas_ty, (NamedType, BuiltinType)):
            return pas_ty.name.upper() == 'BOOLEAN'
        return getattr(pas_ty, 'name', '').upper() == 'BOOLEAN'

    def _boolean_name_constants(self) -> tuple[ir.Value, ir.Value]:

        def const_ptr(text: str) -> ir.Value:
            data = bytearray(text.encode('utf-8') + b'\0')
            const = ir.Constant(ir.ArrayType(ir.IntType(8), len(data)), data)
            g = ir.GlobalVariable(self.module, const.type, name=self.unique_name('boolname'))
            g.initializer = const
            g.global_constant = True
            return self.builder.gep(g, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

        return const_ptr('FALSE'), const_ptr('TRUE')

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
