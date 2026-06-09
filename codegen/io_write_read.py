"""
IO_WRITE_READ mixin for Codegen.

I/O operations: WRITE, WRITELN, READLN

Part of Plan 1 refactoring (mixin-based architecture).
"""

from __future__ import annotations

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder
from typing import Optional, List, Union, Any, Tuple

from ast_nodes import *


class IoWriteReadMixin:
    """Mixin for io_write_read functionality."""

    def printf_func(self) -> ir.Function:
        """Declare or fetch printf."""
        if 'printf' not in [f.name for f in self.module.functions]:
            printf_type = ir.FunctionType(ir.IntType(32), [ir.PointerType(ir.IntType(8))], var_arg=True)
            ir.Function(self.module, printf_type, name='printf')
        return next(f for f in self.module.functions if f.name == 'printf')


    def build_write_format_and_args(self, args: List[Union[Expression, WriteArg]]) -> tuple[str, List[ir.Value]]:
        fmt_parts = []
        printf_args = []
        for arg in args:
            if isinstance(arg, WriteArg):
                expr = arg.expr
                width = arg.width
                precision = arg.precision
            else:
                expr = arg
                width = None
                precision = None

            val = self.codegen_expr(expr)

            # Enum value printed by symbolic name (checklist 9.8): index the
            # per-enum name table by the runtime ordinal and print the resulting
            # i8* with %s. The i8* flows through the format logic below, which
            # already maps a char pointer to %s.
            enum_names = self.write_enum_names(expr)
            if enum_names is not None:
                table = self.enum_name_table(enum_names)
                zero = ir.Constant(ir.IntType(32), 0)
                name_ptr = self.builder.gep(table, [zero, val])
                val = self.builder.load(name_ptr)

            # Check if it is a string variable (non-literal)
            is_string_var = False
            is_lstring_var = False
            lstring_len = None
            string_max_len = 0
            from ast_nodes import LStringType as ASTLStringType
            from type_system import LStringType, StringType
            if not isinstance(expr, StringLiteral) and not isinstance(expr, NilLiteral) and not (isinstance(expr, Identifier) and expr.name.upper() == 'NULL'):
                t = None
                if isinstance(expr, Identifier):
                    symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
                    if symbol:
                        t = symbol.type_expr
                elif isinstance(expr, Designator):
                    symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
                    if symbol:
                        t = symbol.type_expr

                if isinstance(t, (LStringType, ASTLStringType)):
                    is_string_var = True
                    is_lstring_var = True
                    string_max_len = t.max_len if hasattr(t, 'max_len') else 256
                elif isinstance(t, StringType):
                    is_string_var = True
                    is_lstring_var = False
                    string_max_len = t.max_len if hasattr(t, 'max_len') else 256
                elif isinstance(t, NamedType):
                    name_up = t.name.upper()
                    if name_up == 'LSTRING':
                        is_string_var = True
                        is_lstring_var = True
                        string_max_len = int(t.param) if t.param else 256
                    elif name_up == 'STRING':
                        is_string_var = True
                        is_lstring_var = False
                        string_max_len = int(t.param) if t.param else 256
                    elif name_up in self.type_aliases:
                        aliased = self.type_aliases[name_up]
                        if isinstance(aliased, (LStringType, ASTLStringType)):
                            is_string_var = True
                            is_lstring_var = True
                            string_max_len = aliased.max_len if hasattr(aliased, 'max_len') else 256
                        elif isinstance(aliased, StringType):
                            is_string_var = True
                            is_lstring_var = False
                            string_max_len = aliased.max_len if hasattr(aliased, 'max_len') else 256

            if is_string_var:
                # val is aggregate pointer [n+1 x i8]* or [n x i8]*
                # Extract chars pointer: skip length byte for LSTRING, start at 0 for STRING
                zero = ir.Constant(ir.IntType(32), 0)
                one = ir.Constant(ir.IntType(32), 1)
                if is_lstring_var:
                    # LSTRING (manual 6-19): WRITE emits the *current length*
                    # string. Length is byte [0]; characters are [1..]. No null
                    # terminator exists, so use %.*s with the runtime length.
                    len_byte = self.builder.load(self.builder.gep(val, [zero, zero]))
                    lstring_len = self.builder.zext(len_byte, ir.IntType(32))
                    val = self.builder.gep(val, [zero, one])
                else:
                    # STRING: chars at [0, 0], not null-terminated, use %.*s with length
                    val = self.builder.gep(val, [zero, zero])

            prefix = '%'
            if width is not None:
                prefix += '*'
                printf_args.append(self.coerce_printf_int(self.codegen_expr(width)))
            if precision is not None:
                prefix += '.*'
                printf_args.append(self.coerce_printf_int(self.codegen_expr(precision)))

            # Determine format based on LLVM type
            val_type_str = str(val.type)
            if is_string_var:
                # Both STRING and LSTRING write with %.*s. STRING uses its
                # (blank-padded) compile-time max length; LSTRING uses the
                # runtime length read from byte [0] above.
                length_arg = (lstring_len if is_lstring_var else ir.Constant(ir.IntType(32), string_max_len))
                if not precision:  # Only add length if not already in precision
                    prefix += '.*'
                    # printf consumes dynamic args as width, then precision,
                    # then value. The implicit length IS the precision, so it
                    # must follow any width arg already appended for this item
                    # (appending puts it immediately before the value below).
                    printf_args.append(length_arg)
                suffix = 's'
            elif 'i32' in val_type_str:
                suffix = 'd'
            elif 'i16' in val_type_str:
                suffix = 'u'
            elif 'i8*' in val_type_str:
                suffix = 's'
            elif 'i8' in val_type_str:
                suffix = 'c'
            elif 'i1' in val_type_str:
                # Booleans as integers for printf
                suffix = 'd'
            elif 'double' in val_type_str or 'float' in val_type_str:
                suffix = 'f'
            else:
                suffix = 's'

            fmt_parts.append(prefix + suffix)
            printf_args.append(val)

        return "".join(fmt_parts), printf_args


    def builtin_write(self, args: List[Union[Expression, WriteArg]]) -> None:
        """Implement WRITE for any combination of string/integer/boolean types (no newline)."""
        printf_func = self.printf_func()
        fmt_str, printf_args = self.build_write_format_and_args(args)
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode('utf-8') + b'\0'))
        fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        fmt_global.initializer = fmt_const
        fmt_global.global_constant = True
        fmt_ptr = self.builder.gep(fmt_global, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

        self.builder.call(printf_func, [fmt_ptr] + printf_args)


    def builtin_writeln(self, args: List[Union[Expression, WriteArg]]) -> None:
        """Implement WRITELN for any combination of string/integer/boolean types."""
        printf_func = self.printf_func()
        fmt_str, printf_args = self.build_write_format_and_args(args)
        fmt_str += "\n"
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode('utf-8') + b'\0'))
        fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        fmt_global.initializer = fmt_const
        fmt_global.global_constant = True
        fmt_ptr = self.builder.gep(fmt_global, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

        self.builder.call(printf_func, [fmt_ptr] + printf_args)


    def builtin_readln(self, args: List[Expression]) -> None:
        """Implement READLN for integers."""
        # Declare scanf if not already declared
        if 'scanf' not in [f.name for f in self.module.functions]:
            scanf_type = ir.FunctionType(ir.IntType(32), [ir.PointerType(ir.IntType(8))], var_arg=True)
            ir.Function(self.module, scanf_type, name='scanf')

        # Get scanf function
        scanf_func = None
        for func in self.module.functions:
            if func.name == 'scanf':
                scanf_func = func
                break

        for arg in args:
            if isinstance(arg, Identifier):
                symbol = self.scope.lookup(arg.name)
                if not symbol:
                    raise CodegenError(f'Undefined variable: {arg.name}')

                # Can't read into parameters
                if symbol.is_parameter:
                    raise CodegenError(f'Cannot read into parameter: {arg.name}')

                fmt_str = "%d"
                fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode('utf-8') + b'\0'))
                fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
                fmt_global.initializer = fmt_const
                fmt_ptr = self.builder.bitcast(fmt_global, ir.PointerType(ir.IntType(8)))

                self.builder.call(scanf_func, [fmt_ptr, symbol.llvm_value])


    def enum_value_list(self, type_expr) -> Optional[List[str]]:
        """Return the ordered member names if ``type_expr`` is (or aliases) an
        enum type, else ``None``. Enums lower to i32 ordinals; this recovers the
        symbolic names for WRITE."""
        t = self.resolve_type_alias(type_expr)
        if isinstance(t, EnumType):  # AST EnumType carries `.values`
            return list(t.values)
        return None


    def write_enum_names(self, expr) -> Optional[List[str]]:
        """If a WRITE argument denotes an enum value (an enum variable, an enum
        designator without further selectors, or a bare enum member literal),
        return the member-name list so it can be printed by name. Returns
        ``None`` for anything else — notably arbitrary enum-typed *expressions*
        (e.g. ``SUCC(c)``), which still print as their ordinal because codegen
        does not carry per-expression Pascal types."""
        if isinstance(expr, (Identifier, Designator)) and not getattr(expr, 'selectors', None):
            sym = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if sym is not None and sym.type_expr is not None:
                names = self.enum_value_list(sym.type_expr)
                if names:
                    return names
            # Bare enum member literal, e.g. WRITE(Red).
            return self.enum_member_names.get(expr.name.upper())
        return None


    def enum_name_table(self, names: List[str]) -> ir.GlobalVariable:
        """Get (or build once) a constant ``[n x i8*]`` table of pointers to the
        null-terminated member-name strings for an enum, indexable by ordinal."""
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


