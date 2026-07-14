"""Argument checking for the built-in procedures: file primitives, WRITE/READ
families, string intrinsics (CONCAT/COPYLST/COPYSTR/INSERT/DELETE/POSITN),
PACK/UNPACK, ENCODE/DECODE, and NEW/DISPOSE.

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""


from ..ast_nodes import ArrayType as ASTArrayType
from ..ast_nodes import (
    Designator,
    Identifier,
    IntLiteral,
    NamedType,
    ProcCallStmt,
    SetConstructor,
    WriteArg,
)
from ..ast_nodes import PointerType as ASTPointerType
from ..type_system import (
    BOOLEAN_TYPE,
    CHAR_TYPE,
    INTEGER8_TYPE,
    INTEGER32_TYPE,
    INTEGER64_TYPE,
    INTEGER_TYPE,
    REAL32_TYPE,
    REAL_TYPE,
    WORD8_TYPE,
    WORD32_TYPE,
    WORD64_TYPE,
    WORD_TYPE,
    ArrayType,
    EnumType,
    FileType,
    LStringType,
    PointerType,
    SetType,
    StringType,
    Type,
    can_assign,
    is_fixed_char_array,
)


class BuiltinArgsMixin:

    def _check_file_primitive_args(self, stmt: ProcCallStmt, name: str) -> None:
        if len(stmt.args) != 1:
            self.error(f"Procedure '{stmt.name}' expects 1 argument, got {len(stmt.args)}", stmt)
            return
        arg = stmt.args[0]
        arg_type = self.infer_expression_type(arg)
        if not isinstance(arg_type, FileType):
            self.error(f"Argument 1 type mismatch: {name} expects a file variable, got {arg_type}", stmt)

    def _check_assign_file_args(self, stmt: ProcCallStmt) -> None:
        if len(stmt.args) != 2:
            self.error(f"ASSIGN expects 2 arguments, got {len(stmt.args)}", stmt)
            return
        f_arg, name_arg = stmt.args
        f_type = self.infer_expression_type(f_arg)
        if not isinstance(f_type, FileType):
            self.error(f"ASSIGN argument 1 expects a file variable, got {f_type}", stmt)
        if not isinstance(f_arg, (Identifier, Designator)):
            self.error("ASSIGN argument 1 must be a file variable designator", stmt)
        name_type = self.infer_expression_type(name_arg)
        if name_type is None or (not isinstance(name_type, (StringType, LStringType)) and not name_type.equivalent_to(CHAR_TYPE)):
            self.error(f"ASSIGN argument 2 must be STRING/LSTRING/CHAR, got {name_type}", stmt)

    def _is_text_file_type(self, t: Type) -> bool:
        return isinstance(t, FileType) and t.structure == 'ASCII' and t.element_type.equivalent_to(CHAR_TYPE)

    def _check_write_args(self, stmt: ProcCallStmt) -> None:
        """Type check WRITE/WRITELN arguments."""
        start = 0
        if stmt.args:
            first_arg = stmt.args[0]
            first_value = first_arg.expr if isinstance(first_arg, WriteArg) else first_arg
            first_type = self.infer_expression_type(first_value)
            if isinstance(first_type, FileType):
                formatted_selector = isinstance(first_arg, WriteArg) and (first_arg.width is not None or first_arg.precision is not None)
                if formatted_selector:
                    self.error("WRITE/WRITELN file selector must be an unformatted leading TEXT file", stmt)
                    start = 1
                elif self._is_text_file_type(first_type):
                    start = 1
                else:
                    self.error("WRITE/WRITELN file selector must be TEXT, not binary FILE", stmt)
                    start = 1

        for i, arg in enumerate(stmt.args[start:], start=start):
            value_arg = arg.expr if isinstance(arg, WriteArg) else arg
            value_type = self.infer_expression_type(value_arg)
            if isinstance(arg, WriteArg):
                if arg.width is not None:
                    width_type = self.infer_expression_type(arg.width)
                    if width_type and not can_assign(width_type, INTEGER_TYPE):
                        self.error(f"WRITE width {i+1} must be INTEGER-compatible, got {width_type}", stmt)
                if arg.precision is not None:
                    precision_type = self.infer_expression_type(arg.precision)
                    if precision_type and not can_assign(precision_type, INTEGER_TYPE):
                        self.error(f"WRITE precision {i+1} must be INTEGER-compatible, got {precision_type}", stmt)
                    if value_type in (INTEGER_TYPE, WORD_TYPE, INTEGER32_TYPE, INTEGER64_TYPE):
                        self.error("WRITE precision (::N) is not valid for INTEGER-compatible values", stmt)
            if value_type is None:
                continue
            if isinstance(value_type, FileType):
                self.error("WRITE/WRITELN do not accept whole file variables as data arguments", stmt)
                continue
            if not self._is_writable_type(value_type):
                self.error(f"WRITE argument {i+1} has unwritable type {value_type}", stmt)

    def _check_read_args(self, stmt: ProcCallStmt, is_readln: bool) -> None:
        """Type check READ/READLN arguments."""
        if not stmt.args:
            return
        start = 0
        first_type = self.infer_expression_type(stmt.args[0])
        if isinstance(first_type, FileType):
            if self._is_text_file_type(first_type):
                start = 1
            else:
                self.error("READ/READLN file selector must be TEXT, not binary FILE", stmt)
                start = 1
        for i, arg in enumerate(stmt.args[start:], start=start):
            if not isinstance(arg, (Identifier, Designator)):
                self.error(f"READ argument {i+1} must be a designator", stmt)
                continue
            sym = self.symbol_table.lookup(arg.name) or self.symbol_table.lookup(arg.name.upper())
            if not sym:
                self.error(f"READ argument {i+1} refers to undefined variable '{arg.name}'", stmt)
                continue
            if sym.kind == 'const' or not sym.is_mutable:
                self.error(f"READ argument {i+1} must be assignable", stmt)
                continue
            target_type = self.infer_designator_type(arg) if isinstance(arg, Designator) else sym.type
            if target_type is None:
                self.error(f"READ argument {i+1} has unresolvable type", stmt)
                continue
            if not self._is_readable_type(target_type):
                self.error(f"READ argument {i+1} has unreadable type {target_type}", stmt)

    def _check_readset_args(self, stmt: ProcCallStmt) -> None:
        if len(stmt.args) not in {2, 3}:
            self.error(f"READSET expects 2 or 3 arguments, got {len(stmt.args)}", stmt)
            return
        start = 0
        if len(stmt.args) == 3:
            f_type = self.infer_expression_type(stmt.args[0])
            if not self._is_text_file_type(f_type):
                self.error(f"READSET file parameter must be TEXT, got {f_type}", stmt)
            start = 1
        dest = stmt.args[start]
        if not isinstance(dest, (Identifier, Designator)):
            self.error("READSET destination must be a mutable LSTRING designator", stmt)
        else:
            sym = self.symbol_table.lookup(dest.name) or self.symbol_table.lookup(dest.name.upper())
            dest_type = self.infer_expression_type(dest)
            if not sym or not sym.is_mutable:
                self.error("READSET destination must be mutable", stmt)
            if not isinstance(dest_type, LStringType):
                self.error(f"READSET destination must be LSTRING, got {dest_type}", stmt)
        set_arg = stmt.args[start + 1]
        # Faithful 1981 default (D-022): the READSET set argument must be a
        # declared SET OF CHAR value (a set variable, or a type-prefixed
        # constructor such as CHARSET['A'..'Z']). The vintage pas1 rejects an
        # inline, untyped set-constructor literal here with "Character Set
        # Expected". Allow it only under -f readset-set-literal.
        if (isinstance(set_arg, SetConstructor) and set_arg.type_name is None and not self.feature_enabled('readset-set-literal')):
            self.error(
                "Character Set Expected: READSET set argument must be a declared "
                "SET OF CHAR value (a set variable or a type-prefixed constructor "
                "like CHARSET['A'..'Z']), not an inline set literal "
                "(enable -f readset-set-literal to accept inline set constructors)", stmt)
            return
        set_type = self.infer_expression_type(set_arg)
        if not isinstance(set_type, SetType) or not set_type.element_type.equivalent_to(CHAR_TYPE):
            self.error(f"READSET set argument must be SET OF CHAR, got {set_type}", stmt)

    def _check_readfn_args(self, stmt: ProcCallStmt) -> None:
        if not stmt.args:
            self.error("READFN expects at least one argument", stmt)
            return
        start = 0
        first_type = self.infer_expression_type(stmt.args[0])
        if self._is_text_file_type(first_type):
            start = 1
        elif isinstance(first_type, FileType):
            self.error("READFN source file parameter must be TEXT", stmt)
            start = 1
        for i, arg in enumerate(stmt.args[start:], start=start + 1):
            if not isinstance(arg, (Identifier, Designator)):
                self.error(f"READFN argument {i} must be a designator", stmt)
                continue
            sym = self.symbol_table.lookup(arg.name) or self.symbol_table.lookup(arg.name.upper())
            if not sym or not sym.is_mutable:
                self.error(f"READFN argument {i} must be assignable", stmt)
                continue
            target_type = self.infer_expression_type(arg)
            if isinstance(target_type, FileType):
                continue
            if target_type is None or not self._is_readable_type(target_type):
                self.error(f"READFN argument {i} has unreadable type {target_type}", stmt)

    def _is_writable_type(self, t: Type) -> bool:
        # WRITE supports printable BOOLEAN and enum values.  User enums are
        # ordinal by default and symbolic under -f symbolic-enum-io; BOOLEAN is
        # always name-based on output.
        # INTEGER32/INTEGER64 are always writable when a value of that type
        # exists — the feature flag gates whether you can NAME the type in host
        # source, not whether a live typed value (e.g. returned from an imported
        # device function) can be passed to WRITE.
        wide_real = (type(REAL32_TYPE), ) if (self.feature_enabled('wide-reals') or self.in_device_module) else ()
        return isinstance(t, (type(BOOLEAN_TYPE), type(CHAR_TYPE), type(INTEGER_TYPE), type(REAL_TYPE), type(WORD_TYPE), type(WORD8_TYPE), type(WORD32_TYPE), type(WORD64_TYPE),
                              type(INTEGER8_TYPE), type(INTEGER32_TYPE), type(INTEGER64_TYPE), EnumType, StringType, LStringType) + wide_real) or is_fixed_char_array(t)

    def _is_readable_type(self, t: Type) -> bool:
        # READ remains narrower than WRITE: BOOLEAN input is unsupported, but
        # user enums are readable in both modes (numeric by default, symbolic
        # under -f symbolic-enum-io).
        # INTEGER32/INTEGER64 are always readable for the same reason they are
        # always writable: the type object is valid regardless of how it arrived.
        return isinstance(t, (type(CHAR_TYPE), type(INTEGER_TYPE), type(REAL_TYPE), type(WORD_TYPE), type(WORD32_TYPE), type(WORD64_TYPE), type(INTEGER32_TYPE),
                              type(INTEGER64_TYPE), EnumType, StringType, LStringType))

    def _check_concat_args(self, stmt: ProcCallStmt) -> None:
        """Type check CONCAT(VAR D: LSTRING; CONST S: STRING).
        
        D (destination) must be an LSTRING variable (mutable).
        S (source) must be a STRING or LSTRING (readable).
        Error if upper(D) < length(D) + upper(S) (capacity check).
        """
        if len(stmt.args) != 2:
            self.error(f"CONCAT expects 2 arguments, got {len(stmt.args)}", stmt)
            return

        # Argument 1: destination (VAR LSTRING)
        dest_arg = stmt.args[0]
        if not isinstance(dest_arg, Identifier) and not isinstance(dest_arg, Designator):
            self.error("CONCAT: first argument must be a designator (variable)", stmt)
            return

        dest_name = dest_arg.name if isinstance(dest_arg, Identifier) else dest_arg.name
        dest_sym = self.symbol_table.lookup(dest_name) or self.symbol_table.lookup(dest_name.upper())
        if not dest_sym:
            self.error(f"CONCAT: undefined variable '{dest_name}'", stmt)
            return

        dest_type = dest_sym.type if dest_sym else None
        if not isinstance(dest_type, LStringType):
            self.error(f"CONCAT: first argument must be LSTRING, got {dest_type}", stmt)
            return

        if not dest_sym.is_mutable:
            self.error("CONCAT: first argument must be mutable (VAR parameter)", stmt)
            return

        # Argument 2: source (CONST STRING or LSTRING)
        src_arg = stmt.args[1]
        src_type = self.infer_expression_type(src_arg)
        if not isinstance(src_type, (StringType, LStringType)):
            self.error(f"CONCAT: second argument must be STRING or LSTRING, got {src_type}", stmt)
            return

    def _check_pack_args(self, stmt: ProcCallStmt) -> None:
        """Type check PACK(CONST A: unpacked-array; I: index; VAR Z: packed-array)."""
        if len(stmt.args) != 3:
            self.error(f"PACK expects 3 arguments, got {len(stmt.args)}", stmt)
            return

        a_arg = stmt.args[0]
        i_arg = stmt.args[1]
        z_arg = stmt.args[2]

        a_type = self.infer_expression_type(a_arg)
        i_type = self.infer_expression_type(i_arg)
        z_type = self.infer_expression_type(z_arg)

        if not a_type or not i_type or not z_type:
            return

        if not isinstance(a_type, ArrayType) or a_type.packed:
            self.error(f"PACK: first argument must be an unpacked array, got {a_type}", stmt)
            return

        if not i_type.equivalent_to(INTEGER_TYPE):
            self.error(f"PACK: second argument must be an index (INTEGER), got {i_type}", stmt)
            return

        if not isinstance(z_type, ArrayType) or not z_type.packed:
            self.error(f"PACK: third argument must be a packed array, got {z_type}", stmt)
            return

        if not a_type.element_type.equivalent_to(z_type.element_type):
            self.error(f"PACK: element types of arrays must be equivalent, got {a_type.element_type} and {z_type.element_type}", stmt)
            return

        # Check mutability of Z
        if isinstance(z_arg, (Identifier, Designator)):
            z_name = z_arg.name
            z_sym = self.symbol_table.lookup(z_name) or self.symbol_table.lookup(z_name.upper())
            if z_sym and not z_sym.is_mutable:
                self.error(f"PACK: third argument '{z_name}' must be mutable (VAR parameter)", stmt)

        # Compile-time bounds validation if I is constant
        if isinstance(i_arg, IntLiteral):
            i_val = i_arg.value
            a_len = a_type.upper_bound - i_val + 1
            z_len = z_type.upper_bound - z_type.lower_bound + 1
            if a_len < z_len:
                self.error(f"PACK: unpacked array slice from index {i_val} (length {a_len}) is too small for packed array (length {z_len})", stmt)

    def _check_unpack_args(self, stmt: ProcCallStmt) -> None:
        """Type check UNPACK(CONST Z: packed-array; VAR A: unpacked-array; I: index)."""
        if len(stmt.args) != 3:
            self.error(f"UNPACK expects 3 arguments, got {len(stmt.args)}", stmt)
            return

        z_arg = stmt.args[0]
        a_arg = stmt.args[1]
        i_arg = stmt.args[2]

        z_type = self.infer_expression_type(z_arg)
        a_type = self.infer_expression_type(a_arg)
        i_type = self.infer_expression_type(i_arg)

        if not z_type or not a_type or not i_type:
            return

        if not isinstance(z_type, ArrayType) or not z_type.packed:
            self.error(f"UNPACK: first argument must be a packed array, got {z_type}", stmt)
            return

        if not isinstance(a_type, ArrayType) or a_type.packed:
            self.error(f"UNPACK: second argument must be an unpacked array, got {a_type}", stmt)
            return

        if not i_type.equivalent_to(INTEGER_TYPE):
            self.error(f"UNPACK: third argument must be an index (INTEGER), got {i_type}", stmt)
            return

        if not z_type.element_type.equivalent_to(a_type.element_type):
            self.error(f"UNPACK: element types of arrays must be equivalent, got {z_type.element_type} and {a_type.element_type}", stmt)
            return

        # Check mutability of A
        if isinstance(a_arg, (Identifier, Designator)):
            a_name = a_arg.name
            a_sym = self.symbol_table.lookup(a_name) or self.symbol_table.lookup(a_name.upper())
            if a_sym and not a_sym.is_mutable:
                self.error(f"UNPACK: second argument '{a_name}' must be mutable (VAR parameter)", stmt)

        # Compile-time bounds validation if I is constant
        if isinstance(i_arg, IntLiteral):
            i_val = i_arg.value
            a_len = a_type.upper_bound - i_val + 1
            z_len = z_type.upper_bound - z_type.lower_bound + 1
            if a_len < z_len:
                self.error(f"UNPACK: unpacked array slice from index {i_val} (length {a_len}) is too small for packed array (length {z_len})", stmt)

    def _check_copylst_args(self, stmt: ProcCallStmt) -> None:
        """Type check COPYLST(CONST S: STRING; VAR D: LSTRING).
        
        S (source) must be a STRING or LSTRING (readable).
        D (destination) must be an LSTRING variable (mutable).
        Error if upper(D) < upper(S) (capacity check).
        """
        if len(stmt.args) != 2:
            self.error(f"COPYLST expects 2 arguments, got {len(stmt.args)}", stmt)
            return

        # Argument 1: source (CONST STRING or LSTRING)
        src_arg = stmt.args[0]
        src_type = self.infer_expression_type(src_arg)
        if not isinstance(src_type, (StringType, LStringType)):
            self.error(f"COPYLST: first argument must be STRING or LSTRING, got {src_type}", stmt)
            return

        # Argument 2: destination (VAR LSTRING)
        dest_arg = stmt.args[1]
        if not isinstance(dest_arg, Identifier) and not isinstance(dest_arg, Designator):
            self.error("COPYLST: second argument must be a designator (variable)", stmt)
            return

        dest_name = dest_arg.name if isinstance(dest_arg, Identifier) else dest_arg.name
        dest_sym = self.symbol_table.lookup(dest_name) or self.symbol_table.lookup(dest_name.upper())
        if not dest_sym:
            self.error(f"COPYLST: undefined variable '{dest_name}'", stmt)
            return

        dest_type = dest_sym.type if dest_sym else None
        if not isinstance(dest_type, LStringType):
            self.error(f"COPYLST: second argument must be LSTRING, got {dest_type}", stmt)
            return

        if not dest_sym.is_mutable:
            self.error("COPYLST: second argument must be mutable (VAR parameter)", stmt)
            return

    def _check_copystr_args(self, stmt: ProcCallStmt) -> None:
        """Type check COPYSTR(CONST S: STRING; VAR D: STRING).
        
        S (source) must be a STRING or LSTRING (readable).
        D (destination) must be a STRING variable (mutable).
        Error if upper(D) < upper(S) (capacity check).
        """
        if len(stmt.args) != 2:
            self.error(f"COPYSTR expects 2 arguments, got {len(stmt.args)}", stmt)
            return

        # Argument 1: source (CONST STRING or LSTRING)
        src_arg = stmt.args[0]
        src_type = self.infer_expression_type(src_arg)
        if not isinstance(src_type, (StringType, LStringType)):
            self.error(f"COPYSTR: first argument must be STRING or LSTRING, got {src_type}", stmt)
            return

        # Argument 2: destination (VAR STRING)
        dest_arg = stmt.args[1]
        if not isinstance(dest_arg, Identifier) and not isinstance(dest_arg, Designator):
            self.error("COPYSTR: second argument must be a designator (variable)", stmt)
            return

        dest_name = dest_arg.name if isinstance(dest_arg, Identifier) else dest_arg.name
        dest_sym = self.symbol_table.lookup(dest_name) or self.symbol_table.lookup(dest_name.upper())
        if not dest_sym:
            self.error(f"COPYSTR: undefined variable '{dest_name}'", stmt)
            return

        dest_type = dest_sym.type if dest_sym else None
        if not isinstance(dest_type, StringType):
            self.error(f"COPYSTR: second argument must be STRING, got {dest_type}", stmt)
            return

        if not dest_sym.is_mutable:
            self.error("COPYSTR: second argument must be mutable (VAR parameter)", stmt)
            return

    def _check_insert_args(self, stmt: ProcCallStmt) -> None:
        if len(stmt.args) != 3:
            self.error(f"INSERT expects 3 arguments, got {len(stmt.args)}", stmt)
            return
        src_type = self.infer_expression_type(stmt.args[0])
        dst_type = self.infer_expression_type(stmt.args[1])
        pos_type = self.infer_expression_type(stmt.args[2])
        if not isinstance(src_type, (StringType, LStringType)):
            self.error(f"INSERT: first argument must be STRING or LSTRING, got {src_type}", stmt)
            return
        if not isinstance(dst_type, (StringType, LStringType)):
            self.error(f"INSERT: second argument must be STRING or LSTRING, got {dst_type}", stmt)
            return
        if isinstance(stmt.args[1], (Identifier, Designator)):
            sym = self.symbol_table.lookup(stmt.args[1].name) or self.symbol_table.lookup(stmt.args[1].name.upper())
            if sym and not sym.is_mutable:
                self.error("INSERT: second argument must be mutable (VAR parameter)", stmt)
        if not pos_type or not pos_type.equivalent_to(INTEGER_TYPE):
            self.error(f"INSERT: third argument must be INTEGER, got {pos_type}", stmt)
            return

    def _check_delete_args(self, stmt: ProcCallStmt) -> None:
        if len(stmt.args) != 3:
            self.error(f"DELETE expects 3 arguments, got {len(stmt.args)}", stmt)
            return
        dst_type = self.infer_expression_type(stmt.args[0])
        pos_type = self.infer_expression_type(stmt.args[1])
        count_type = self.infer_expression_type(stmt.args[2])
        if not isinstance(dst_type, (StringType, LStringType)):
            self.error(f"DELETE: first argument must be STRING or LSTRING, got {dst_type}", stmt)
            return
        if isinstance(stmt.args[0], (Identifier, Designator)):
            sym = self.symbol_table.lookup(stmt.args[0].name) or self.symbol_table.lookup(stmt.args[0].name.upper())
            if sym and not sym.is_mutable:
                self.error("DELETE: first argument must be mutable (VAR parameter)", stmt)
        if not pos_type or not pos_type.equivalent_to(INTEGER_TYPE):
            self.error(f"DELETE: second argument must be INTEGER, got {pos_type}", stmt)
            return
        if not count_type or not count_type.equivalent_to(INTEGER_TYPE):
            self.error(f"DELETE: third argument must be INTEGER, got {count_type}", stmt)
            return

    def _check_format_arg(self, arg, node, opname: str) -> None:
        if isinstance(arg, WriteArg):
            self.infer_expression_type(arg.expr)
            if arg.width is not None:
                self.infer_expression_type(arg.width)
            if arg.precision is not None:
                self.infer_expression_type(arg.precision)
            return
        self.infer_expression_type(arg)

    def _check_decode_dest(self, arg, node) -> None:
        if isinstance(arg, WriteArg):
            self._check_format_arg(arg, node, 'DECODE')
            return
        if not isinstance(arg, (Identifier, Designator)):
            self.error('DECODE: second argument must be a designator', node)
            return
        sym = self.symbol_table.lookup(arg.name) or self.symbol_table.lookup(arg.name.upper())
        if not sym or not sym.is_mutable:
            self.error('DECODE: second argument must be mutable', node)

    def _check_positn_args(self, stmt: ProcCallStmt) -> None:
        if len(stmt.args) != 2:
            self.error(f"POSITN expects 2 arguments, got {len(stmt.args)}", stmt)
            return
        if not isinstance(self.infer_expression_type(stmt.args[0]), (StringType, LStringType)):
            self.error("POSITN: first argument must be STRING or LSTRING", stmt)
            return
        if not isinstance(self.infer_expression_type(stmt.args[1]), (StringType, LStringType)):
            self.error("POSITN: second argument must be STRING or LSTRING", stmt)
            return

    def _resolve_ast_type_alias(self, type_expr):
        seen = set()
        while isinstance(type_expr, NamedType):
            key = type_expr.name.upper()
            sym = self.symbol_table.lookup(type_expr.name) or self.symbol_table.lookup(key)
            aliased = getattr(sym, 'type_expr', None) if sym and sym.kind == 'type' else None
            if aliased is None or key in seen:
                break
            seen.add(key)
            type_expr = aliased
        return type_expr

    def _is_super_array_type_expr(self, type_expr) -> bool:
        type_expr = self._resolve_ast_type_alias(type_expr)
        return isinstance(type_expr, ASTArrayType) and bool(getattr(type_expr, 'super', False))

    def _check_new_args(self, stmt: ProcCallStmt) -> None:
        """Type check NEW(VAR P: ^T) and long-form NEW for super arrays."""
        if len(stmt.args) < 1:
            self.error(f"NEW expects at least 1 argument, got {len(stmt.args)}", stmt)
            return
        arg = stmt.args[0]
        if not isinstance(arg, (Identifier, Designator)):
            self.error("NEW: argument must be a designator (variable)", stmt)
            return
        sym = self.symbol_table.lookup(arg.name) or self.symbol_table.lookup(arg.name.upper())
        if not sym:
            self.error(f"NEW: undefined variable '{arg.name}'", stmt)
            return
        if not isinstance(sym.type, PointerType):
            self.error(f"NEW: argument must be a pointer type, got {sym.type}", stmt)
            return
        if not sym.is_mutable:
            self.error("NEW: argument must be mutable (VAR parameter)", stmt)
            return

        type_expr = getattr(sym, 'type_expr', None)
        ptr_expr = self._resolve_ast_type_alias(type_expr)
        pointee_expr = ptr_expr.base if isinstance(ptr_expr, ASTPointerType) else None
        is_super_array = self._is_super_array_type_expr(pointee_expr) if pointee_expr is not None else False

        if len(stmt.args) == 1:
            if is_super_array:
                self.error("NEW: super array allocation requires upper bound arguments", stmt)
            return

        if not is_super_array:
            self.error(f"NEW expects 1 argument, got {len(stmt.args)}", stmt)
            return

        # Current implementation supports the observed one-dimensional SUPER ARRAY form.
        if len(stmt.args) != 2:
            self.error(f"NEW: super array allocation expects 1 upper bound, got {len(stmt.args) - 1}", stmt)
            return
        _wide_gate = self.feature_enabled('wide-integers') or self.in_device_module
        bound_type = self.infer_expression_type(stmt.args[1], INTEGER32_TYPE if _wide_gate else INTEGER_TYPE)
        # A heap super array's dynamic bound is stored as an i64 header and the
        # allocation math is 64-bit (docs/super-array-bounds-abi.md), so under
        # wide-integers the bound expression may itself be a wide signed
        # integer -- that is what lets a buffer exceed 32767 elements.  The
        # vintage dialect keeps the INTEGER-only rule.
        wide_bound_ok = _wide_gate and \
            bound_type in (INTEGER32_TYPE, INTEGER64_TYPE)
        if bound_type and not wide_bound_ok and not can_assign(bound_type, INTEGER_TYPE):
            self.error(f"NEW: super array upper bound must be INTEGER, got {bound_type}", stmt)
            return

    def _check_dispose_args(self, stmt: ProcCallStmt) -> None:
        """Type check DISPOSE(VAR P: ^T)."""
        if len(stmt.args) != 1:
            self.error(f"DISPOSE expects 1 argument, got {len(stmt.args)}", stmt)
            return
        arg = stmt.args[0]
        if not isinstance(arg, (Identifier, Designator)):
            self.error("DISPOSE: argument must be a designator (variable)", stmt)
            return
        sym = self.symbol_table.lookup(arg.name) or self.symbol_table.lookup(arg.name.upper())
        if not sym:
            self.error(f"DISPOSE: undefined variable '{arg.name}'", stmt)
            return
        if not isinstance(sym.type, PointerType):
            self.error(f"DISPOSE: argument must be a pointer type, got {sym.type}", stmt)
            return
        if not sym.is_mutable:
            self.error("DISPOSE: argument must be mutable (VAR parameter)", stmt)
            return

    def _decode_pascal_string(self, value: str) -> str:
        """Return the runtime contents represented by a Pascal string token."""
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        return value.replace("''", "'")
