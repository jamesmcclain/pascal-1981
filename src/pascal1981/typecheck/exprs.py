"""Expression type inference: expressions, built-in function calls, set
constructors, and designator (variable/field/index/deref) types.

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""

from typing import Optional

from ..ast_nodes import (
    AdrExpr,
    AdsExpr,
    BinOp,
    BoolLiteral,
    CharLiteral,
    Designator,
    Expression,
    FuncCall,
    Identifier,
    IntLiteral,
    LowerExpr,
    NamedType,
    NilLiteral,
    RangeExpr,
    RealLiteral,
    RetypeExpr,
    SetConstructor,
    SizeofExpr,
    StringLiteral,
    UnaryOp,
    UpperExpr,
    WriteArg,
)
from ..ast_nodes import PointerType as ASTPointerType
from ..builtins_registry import DEVICE_INDEX_BUILTIN_FUNCTIONS
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
    FunctionType,
    LStringType,
    PointerType,
    RecordType,
    SetType,
    StringType,
    Type,
    binary_op_result_type,
    can_assign,
    unary_op_result_type,
)


class ExprInferMixin:

    def infer_expression_type(self, expr: Expression, context_type: Optional[Type] = None) -> Optional[Type]:
        """Infer the type of an expression."""
        if isinstance(expr, IntLiteral):
            self._check_integer_literal_range(expr, context_type)
            resolved = context_type if context_type in (INTEGER_TYPE, WORD_TYPE, WORD8_TYPE, WORD32_TYPE, WORD64_TYPE, INTEGER8_TYPE, INTEGER32_TYPE,
                                                        INTEGER64_TYPE) else INTEGER_TYPE
            setattr(expr, 'resolved_type', resolved)
            return resolved
        elif isinstance(expr, RealLiteral):
            # A real literal adopts a REAL32 context (the analog of a C ``f``
            # suffix), so ``x := 0.0`` and ``x*x <= 4.0`` stay single-precision
            # when x is REAL32. Otherwise it is REAL (f64).
            resolved = REAL32_TYPE if context_type is REAL32_TYPE else REAL_TYPE
            setattr(expr, 'resolved_type', resolved)
            return resolved
        elif isinstance(expr, BoolLiteral):
            return BOOLEAN_TYPE
        elif isinstance(expr, CharLiteral):
            return CHAR_TYPE
        elif isinstance(expr, NilLiteral):
            return PointerType(CHAR_TYPE)
        elif isinstance(expr, StringLiteral):
            return LStringType(len(self._decode_pascal_string(expr.value)))
        elif isinstance(expr, SetConstructor):
            declared_set_type: Optional[SetType] = None
            if expr.type_name:
                sym = self.symbol_table.lookup(expr.type_name)
                if not sym or sym.kind != 'type':
                    self.error(f"Unknown set type: {expr.type_name}", expr)
                    return None
                if not isinstance(sym.type, SetType):
                    self.error(f"Typed set constructor prefix must name a set type, got {sym.type}", expr)
                    return None
                declared_set_type = sym.type
                if not all(self.is_constant_set_element(el) for el in expr.elements):
                    self.error("Typed set constructors require constant elements", expr)
                    return None
            if not expr.elements:
                if declared_set_type:
                    return declared_set_type
                if isinstance(context_type, SetType):
                    return context_type
                return SetType(INTEGER_TYPE)
            element_type: Optional[Type] = None
            for el in expr.elements:
                if isinstance(el, RangeExpr):
                    low_type = self.infer_expression_type(el.low)
                    high_type = self.infer_expression_type(el.high)
                    if not low_type or not high_type:
                        return None
                    if not low_type.equivalent_to(high_type):
                        self.error(f"Set range bounds must have the same ordinal type, got {low_type} and {high_type}", el)
                        return None
                    # Device-code recission: a set range with a non-constant bound
                    # ('A'..x) needs a runtime loop to set the bits -- banned in a
                    # DEVICE MODULE. The static bitvector core (constant ranges,
                    # union/intersect/membership) stays; a dynamic *singleton* [x]
                    # is fine (a single shift) and is not affected here.
                    if self.in_device_module and not self.is_constant_set_element(el):
                        self.error("dynamic set-range construction (a set range with a "
                                   "non-constant bound) is not available in device code", el)
                        return None
                    cur_type = low_type
                else:
                    cur_type = self.infer_expression_type(el)
                if not cur_type:
                    return None
                if declared_set_type and not can_assign(cur_type, declared_set_type.element_type) and not can_assign(declared_set_type.element_type, cur_type):
                    self.error(f"Set element type mismatch: expected {declared_set_type.element_type}, got {cur_type}", el)
                    return None
                if element_type is None:
                    element_type = cur_type
                elif not cur_type.equivalent_to(element_type):
                    self.error(f"Set element type mismatch: expected {element_type}, got {cur_type}", el)
                    return None
            return declared_set_type or SetType(element_type or INTEGER_TYPE)
        elif isinstance(expr, AdrExpr):
            # Address-of operator (adr var_name)
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            return PointerType(sym.type, flavor='ADR')
        elif isinstance(expr, AdsExpr):
            # Segmented address-of operator (ads var_name). The result pointee
            # space is the operand's storage residence, defaulting to HOST/0
            # (design S4.4).
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            return PointerType(sym.type, flavor='ADS', space=getattr(sym, 'space', None))
        elif isinstance(expr, SizeofExpr):
            # Sizeof operator (sizeof var_name or type)
            return INTEGER_TYPE
        elif isinstance(expr, UpperExpr) or isinstance(expr, LowerExpr):
            intrinsic = type(expr).__name__[:-4].upper()
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            if getattr(expr, 'deref', False):
                # UPPER(p^) / LOWER(p^): bound of the pointee. For a heap super
                # array UPPER(p^) is the dynamic upper bound recorded by long-form
                # NEW (docs/super-array-bounds-abi.md); LOWER(p^) and fixed-array
                # bounds stay static.
                if not isinstance(sym.type, PointerType):
                    self.error(f"Function '{intrinsic}': '{expr.name}^' requires a pointer variable, got {sym.type}", expr)
                    return None
                pointee = sym.type.target_type
                if not isinstance(pointee, (ArrayType, StringType, LStringType)):
                    self.error(f"Function '{intrinsic}' expects an array pointee, got {pointee}", expr)
                    return None
                type_expr = getattr(sym, 'type_expr', None)
                ptr_expr = self._resolve_ast_type_alias(type_expr)
                pointee_expr = ptr_expr.base if isinstance(ptr_expr, ASTPointerType) else None
                is_super = self._is_super_array_type_expr(pointee_expr) if pointee_expr is not None else False
                if is_super and self.in_device_module:
                    # Device code has no heap (NEW/DISPOSE are rescinded), so no
                    # bound header ever exists to read. Buffers arrive from the
                    # host with explicit bound/length parameters instead.
                    self.error(f"Function '{intrinsic}': dynamic super array bounds are not available in device code; pass bounds explicitly", expr)
                    return None
                return INTEGER_TYPE
            ty = sym.type
            if isinstance(ty, (ArrayType, StringType, LStringType)):
                return INTEGER_TYPE
            self.error(f"Function '{intrinsic}' expects an array variable", expr)
            return None
        elif isinstance(expr, RetypeExpr):
            # 1. Resolve target type
            target_type = self.resolve_type(NamedType(expr.type_id, None))
            if not target_type:
                self.error(f"First parameter of RETYPE must be a type identifier, got {expr.type_id}", expr)
                return None

            # 2. Check inner expression type
            expr_type = self.infer_expression_type(expr.expr)
            if expr_type:
                # 3. Check and warn if sizes are not identical
                target_size = self.get_resolved_type_size(target_type)
                expr_size = self.get_resolved_type_size(expr_type)
                if target_size != expr_size:
                    self.warning(f"Size Not Identical: RETYPE from {expr_type} ({expr_size} bytes) to {target_type} ({target_size} bytes)", expr)

            # 4. Handle any selectors on the target type
            current_type = target_type
            if expr.selectors:
                for selector in expr.selectors:
                    if selector.kind == 'INDEX':
                        # STRING/LSTRING are character-indexable (manual:
                        # S[I] is the Ith character; LSTRING index 0 is the
                        # length byte, viewed as a CHAR).
                        if isinstance(current_type, (StringType, LStringType)):
                            if selector.index_or_field:
                                idx_t = self.infer_expression_type(selector.index_or_field)
                                if idx_t and not self._valid_array_index_type(idx_t, INTEGER_TYPE):
                                    self.error(f"String index must be INTEGER, got {idx_t}", expr)
                            current_type = CHAR_TYPE
                            continue
                        if not isinstance(current_type, ArrayType):
                            self.error(f"Cannot index non-array type {current_type}", expr)
                            return None
                        if selector.index_or_field:
                            index_type = self.infer_expression_type(selector.index_or_field)
                            expected = current_type.effective_index_type
                            if index_type and not self._valid_array_index_type(index_type, expected):
                                self.error(f"Array index must be {expected}, got {index_type}", expr)
                        current_type = current_type.element_type
                    elif selector.kind == 'FIELD':
                        field_name = str(selector.index_or_field).upper()
                        if isinstance(current_type, FileType):
                            if field_name == 'MODE':
                                current_type = EnumType(['SEQUENTIAL', 'TERMINAL', 'DIRECT'], name='FILEMODES')
                            elif field_name == 'TRAP':
                                # Trapped I/O (manual ch.12 File Field Values):
                                # F.TRAP is a BOOLEAN the program sets to make
                                # I/O errors record into F.ERRS instead of
                                # aborting.
                                current_type = BOOLEAN_TYPE
                            elif field_name == 'ERRS':
                                current_type = INTEGER_TYPE
                            else:
                                self.error(f"File control block has no field '{selector.index_or_field}'", expr)
                                return None
                        elif isinstance(current_type, LStringType):
                            if field_name == 'LEN':
                                current_type = CHAR_TYPE
                            else:
                                self.error(f"LSTRING has no field '{selector.index_or_field}'", expr)
                                return None
                        else:
                            if not isinstance(current_type, RecordType):
                                self.error(f"Cannot access field on non-record type {current_type}", expr)
                                return None
                            field_name_orig = selector.index_or_field
                            field_type = current_type.get_field_type(field_name_orig)
                            if field_type is None:
                                self.error(f"Record has no field '{field_name_orig}'", expr)
                                return None
                            current_type = field_type
                    elif selector.kind == 'DEREF':
                        if not isinstance(current_type, PointerType):
                            self.error(f"Cannot dereference non-pointer type {current_type}", expr)
                            return None
                        self._check_deref_space(current_type, expr)
                        current_type = current_type.target_type
            return current_type
        elif isinstance(expr, Identifier):
            lookup_name = expr.name.upper()
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            if lookup_name in DEVICE_INDEX_BUILTIN_FUNCTIONS:
                if not self.in_device_module:
                    self.error(f"{lookup_name} is only available in DEVICE code", expr)
                    return None
                return INTEGER32_TYPE
            if isinstance(sym.type, FunctionType) and not sym.type.params:
                return sym.type.return_type
            return sym.type
        elif isinstance(expr, BinOp):
            literal_context = context_type if context_type in (INTEGER_TYPE, WORD_TYPE, WORD8_TYPE, WORD32_TYPE, WORD64_TYPE, INTEGER8_TYPE, INTEGER32_TYPE,
                                                               INTEGER64_TYPE) else None
            left_context = literal_context if isinstance(expr.left, (IntLiteral, UnaryOp)) else None
            right_context = literal_context if isinstance(expr.right, (IntLiteral, UnaryOp)) else None
            # A REAL32 result context flows into real-literal operands so that a
            # nested literal (e.g. the 0.5 in ``a*0.5``) stays single-precision.
            if context_type is REAL32_TYPE:
                if isinstance(expr.left, RealLiteral):
                    left_context = REAL32_TYPE
                if isinstance(expr.right, RealLiteral):
                    right_context = REAL32_TYPE

            # Empty set constructors are context-dependent. For binary set
            # operators/comparisons, infer the non-empty side first and use it
            # as the contextual type for the empty side.
            if isinstance(expr.left, SetConstructor) and not expr.left.elements:
                right_type = self.infer_expression_type(expr.right, right_context)
                left_type = self.infer_expression_type(expr.left, right_type if isinstance(right_type, SetType) else left_context)
            elif isinstance(expr.right, SetConstructor) and not expr.right.elements:
                left_type = self.infer_expression_type(expr.left, left_context)
                right_type = self.infer_expression_type(expr.right, left_type if isinstance(left_type, SetType) else right_context)
            else:
                left_type = self.infer_expression_type(expr.left, left_context)
                right_type = self.infer_expression_type(expr.right, right_context)
                # If exactly one side is REAL32 and the other is a bare real
                # literal that defaulted to REAL, re-resolve the literal as
                # REAL32 so e.g. ``r32 <= 4.0`` compares in single precision
                # rather than promoting the REAL32 side to double.
                if left_type is REAL32_TYPE and right_type is REAL_TYPE and isinstance(expr.right, RealLiteral):
                    right_type = self.infer_expression_type(expr.right, REAL32_TYPE)
                elif right_type is REAL32_TYPE and left_type is REAL_TYPE and isinstance(expr.left, RealLiteral):
                    left_type = self.infer_expression_type(expr.left, REAL32_TYPE)
            if left_type and right_type:
                result = binary_op_result_type(left_type, expr.op, right_type)
                if result is None:
                    self.error(f"Operator '{expr.op}' cannot be applied to operands of type {left_type} and {right_type}", expr)
                else:
                    self._check_word_int_mix(left_type, right_type, expr.left, expr.right, expr.op, expr)
                return result
            return None
        elif isinstance(expr, UnaryOp):
            self._check_integer_literal_range(expr, context_type)
            if expr.op in ('PLUS', 'MINUS') and isinstance(expr.operand, IntLiteral):
                operand_type = context_type if context_type in (INTEGER_TYPE, WORD_TYPE, WORD8_TYPE, WORD32_TYPE, WORD64_TYPE, INTEGER8_TYPE, INTEGER32_TYPE,
                                                                INTEGER64_TYPE) else INTEGER_TYPE
                setattr(expr, 'resolved_type', operand_type)
                setattr(expr.operand, 'resolved_type', operand_type)
            else:
                operand_type = self.infer_expression_type(expr.operand, context_type)
            if operand_type:
                result = unary_op_result_type(operand_type, expr.op)
                if result is None:
                    self.error(f"Operator '{expr.op}' cannot be applied to operand of type {operand_type}", expr)
                return result
            return None
        elif isinstance(expr, FuncCall):
            lookup_name = expr.name.upper()
            self._check_device_recission(lookup_name, expr)
            sym = self.symbol_table.lookup(lookup_name) or self.symbol_table.lookup(expr.name)
            is_builtin = sym is None or getattr(sym, 'is_builtin', False)

            if not is_builtin:
                if not sym:
                    self.error(f"Undefined function: {expr.name}", expr)
                    return None
                if isinstance(sym.type, FunctionType):
                    # Check argument count.  Variadic functions accept any number
                    # of args >= the fixed parameter count.
                    expected_args = len(sym.type.params)
                    actual_args = len(expr.args) if expr.args else 0
                    _is_variadic_fn = getattr(sym.type, 'is_variadic', False)
                    if _is_variadic_fn:
                        if actual_args < expected_args:
                            self.error(f"Function '{expr.name}' expects at least {expected_args} arguments, got {actual_args}", expr)
                    elif actual_args != expected_args:
                        self.error(f"Function '{expr.name}' expects {expected_args} arguments, got {actual_args}", expr)
                    # Check argument types (fixed params only)
                    if expr.args:
                        for i, (arg, (_param_name, param_type)) in enumerate(zip(expr.args, sym.type.params)):
                            # Parameter type as literal context (see the
                            # procedure-call site): constant arguments adopt
                            # the parameter's integer type and range.
                            arg_type = self.infer_expression_type(arg, param_type)
                            if arg_type and not self._can_pass_value_argument(arg_type, param_type) \
                                    and not self._const_adapts_to_int_target(arg_type, param_type, arg):
                                self.error(f"Argument {i+1} type mismatch: expected {param_type}, got {arg_type}", expr)
                            elif arg_type:
                                self._check_word_int_assign(arg_type, param_type, arg, expr)
                        # Type-check variadic tail args too (just for expression validity)
                        for arg in (expr.args[expected_args:] if _is_variadic_fn else []):
                            self.infer_expression_type(arg)
                    return sym.type.return_type
                return None

            if lookup_name in DEVICE_INDEX_BUILTIN_FUNCTIONS:
                argc = len(expr.args) if expr.args else 0
                if not self.in_device_module:
                    self.error(f"{lookup_name} is only available in DEVICE code", expr)
                    return None
                if argc != 0:
                    self.error(f"Function '{lookup_name}' expects 0 arguments, got {argc}", expr)
                    return None
                return INTEGER32_TYPE
            if lookup_name == 'DEVALLOC':
                # Host-only device allocation (Milestone D). Returns an opaque
                # ADRMEM handle that the host passes to DEVCOPYTO/LAUNCH/DEVFREE.
                if self.in_device_module:
                    self.error("DEVALLOC is host-only and cannot appear in DEVICE code", expr)
                    return None
                argc = len(expr.args) if expr.args else 0
                if argc != 1:
                    self.error(f"DEVALLOC expects 1 argument (byte count), got {argc}", expr)
                    return None
                nbytes_type = self.infer_expression_type(expr.args[0])
                if nbytes_type is not None and not self._is_integer_type(nbytes_type):
                    self.error(f"DEVALLOC byte count must be an integer, got {nbytes_type}", expr)
                return PointerType(CHAR_TYPE)
            if lookup_name in {'EOF', 'EOLN'}:
                argc = len(expr.args) if expr.args else 0
                if argc > 1:
                    self.error(f"Function '{lookup_name}' expects 0 or 1 arguments, got {argc}", expr)
                    return None
                if argc == 1:
                    arg_type = self.infer_expression_type(expr.args[0])
                    if not isinstance(arg_type, FileType):
                        self.error(f"Argument 1 type mismatch: {lookup_name} expects a file variable, got {arg_type}", expr)
                        return None
                    if lookup_name == 'EOLN' and not self._is_text_file_type(arg_type):
                        self.error("EOLN expects a TEXT file", expr)
                        return None
                return BOOLEAN_TYPE
            if lookup_name == 'ABS':
                if len(expr.args) != 1:
                    self.error(f"Function 'ABS' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, REAL_TYPE):
                    return arg_type
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or REAL, got {arg_type}", expr)
                return None
            if lookup_name in {'SQRT', 'SIN', 'COS', 'LN', 'EXP', 'ARCTAN'}:
                if len(expr.args) != 1:
                    self.error(f"Function '{lookup_name}' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, REAL_TYPE):
                    return REAL_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or REAL, got {arg_type}", expr)
                return None
            if lookup_name == 'SQR':
                if len(expr.args) != 1:
                    self.error(f"Function 'SQR' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, REAL_TYPE):
                    return arg_type
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or REAL, got {arg_type}", expr)
                return None
            if lookup_name in {'SUCC', 'PRED'}:
                if len(expr.args) != 1:
                    self.error(f"Function '{lookup_name}' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type is None:
                    return None
                # SUCC/PRED are defined on any ordinal type and yield the same
                # type (enums included).
                if isinstance(arg_type, EnumType) or arg_type in (INTEGER_TYPE, WORD_TYPE, CHAR_TYPE, BOOLEAN_TYPE):
                    return arg_type
                self.error(f"Argument 1 type mismatch: {lookup_name} expects an ordinal type, got {arg_type}", expr)
                return None
            if lookup_name == 'ORD':
                if len(expr.args) != 1:
                    self.error(f"Function 'ORD' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type is None:
                    return None
                # ORD maps any ordinal value to its INTEGER ordinal position
                # (enums included).
                if isinstance(arg_type, EnumType) or arg_type in (INTEGER_TYPE, WORD_TYPE, CHAR_TYPE, BOOLEAN_TYPE, INTEGER8_TYPE, WORD8_TYPE):
                    return INTEGER_TYPE
                self.error(f"Argument 1 type mismatch: ORD expects an ordinal type, got {arg_type}", expr)
                return None
            if lookup_name == 'ODD':
                # Manual (Elementary Types, BOOLEAN, p.6-6): "the ODD function
                # for INTEGER and WORD values".  ODD only tests the low bit, so
                # it is signedness-independent; accept INTEGER and WORD (the
                # faithful-dialect pair, matching HIBYTE/LOBYTE above).  Because
                # this is a custom branch (not the generic builtin path), no
                # WORD/INTEGER mix warning fires -- correct, as ODD does no
                # signed arithmetic.  Codegen already lowers ODD as `val & 1`
                # then `icmp != 0`, which is width/signedness-agnostic.
                if len(expr.args) != 1:
                    self.error(f"Function 'ODD' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, WORD_TYPE):
                    return BOOLEAN_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or WORD, got {arg_type}", expr)
                return None
            if lookup_name in {'HIBYTE', 'LOBYTE'}:
                if len(expr.args) != 1:
                    self.error(f"Function '{lookup_name}' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, WORD_TYPE):
                    return CHAR_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or WORD, got {arg_type}", expr)
                return None
            if lookup_name == 'POSITN':
                if len(expr.args) != 2:
                    self.error(f"POSITN expects 2 arguments, got {len(expr.args)}", expr)
                    return None
                if not isinstance(self.infer_expression_type(expr.args[0]), (StringType, LStringType)):
                    self.error("POSITN: first argument must be STRING or LSTRING", expr)
                    return None
                if not isinstance(self.infer_expression_type(expr.args[1]), (StringType, LStringType)):
                    self.error("POSITN: second argument must be STRING or LSTRING", expr)
                    return None
                return INTEGER_TYPE
            if lookup_name == 'ENCODE':
                if len(expr.args) != 2:
                    self.error(f"ENCODE expects 2 arguments, got {len(expr.args)}", expr)
                    return None
                dest = expr.args[0].expr if isinstance(expr.args[0], WriteArg) else expr.args[0]
                if not isinstance(self.infer_expression_type(dest), LStringType):
                    self.error("ENCODE: first argument must be LSTRING", expr)
                    return None
                self._check_format_arg(expr.args[1], expr, 'ENCODE')
                return BOOLEAN_TYPE
            if lookup_name == 'DECODE':
                if len(expr.args) != 2:
                    self.error(f"DECODE expects 2 arguments, got {len(expr.args)}", expr)
                    return None
                src = expr.args[0].expr if isinstance(expr.args[0], WriteArg) else expr.args[0]
                if not isinstance(self.infer_expression_type(src), (StringType, LStringType)):
                    self.error("DECODE: first argument must be STRING or LSTRING", expr)
                    return None
                self._check_decode_dest(expr.args[1], expr)
                return BOOLEAN_TYPE
            if lookup_name in {'SCANEQ', 'SCANNE'}:
                if len(expr.args) != 4:
                    self.error(f"{lookup_name} expects 4 arguments, got {len(expr.args)}", expr)
                    return None
                l_type = self.infer_expression_type(expr.args[0])
                if l_type not in (INTEGER_TYPE, WORD_TYPE):
                    self.error(f"{lookup_name}: first argument must be INTEGER or WORD, got {l_type}", expr)
                    return None
                if self.infer_expression_type(expr.args[1]) != CHAR_TYPE:
                    self.error(f"{lookup_name}: second argument must be CHAR", expr)
                    return None
                if not isinstance(self.infer_expression_type(expr.args[2]), (StringType, LStringType)):
                    self.error(f"{lookup_name}: third argument must be STRING or LSTRING", expr)
                    return None
                i_type = self.infer_expression_type(expr.args[3])
                if i_type not in (INTEGER_TYPE, WORD_TYPE):
                    self.error(f"{lookup_name}: fourth argument must be INTEGER or WORD, got {i_type}", expr)
                    return None
                return INTEGER_TYPE
            if lookup_name == 'WRD8' and (self.feature_enabled('wide-integers') or self.in_device_module):
                # WRD8 is the 8-bit sibling of WRD: an explicit retyping
                # conversion that truncates its argument to the low 8 bits and
                # returns WORD8.  This is the sanctioned way to narrow a
                # computed integer into a byte (e.g. filling a WORD8 pixel
                # buffer); implicit narrowing assignments stay rejected.
                if len(expr.args) != 1:
                    self.error(f"WRD8 expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if isinstance(arg_type, EnumType):
                    return WORD8_TYPE
                if arg_type in (INTEGER8_TYPE, INTEGER_TYPE, INTEGER32_TYPE, INTEGER64_TYPE, WORD8_TYPE, WORD_TYPE, WORD32_TYPE, WORD64_TYPE, CHAR_TYPE, BOOLEAN_TYPE):
                    return WORD8_TYPE
                if arg_type == REAL_TYPE:
                    self.error("WRD8: REAL argument not supported (argument must be an ordinal type)", expr)
                    return None
                if arg_type:
                    self.error(f"WRD8: unsupported argument type {arg_type}", expr)
                return None
            if lookup_name == 'WRD':
                if len(expr.args) != 1:
                    self.error(f"WRD expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if isinstance(arg_type, PointerType):
                    return WORD_TYPE
                if isinstance(arg_type, EnumType):
                    return WORD_TYPE
                if arg_type in (INTEGER_TYPE, WORD_TYPE, CHAR_TYPE, BOOLEAN_TYPE, INTEGER8_TYPE, WORD8_TYPE):
                    return WORD_TYPE
                if arg_type == REAL_TYPE:
                    self.error("WRD: REAL argument not supported (argument must be an ordinal type or pointer)", expr)
                    return None
                if arg_type:
                    self.error(f"WRD: unsupported argument type {arg_type}", expr)
                return None
            if lookup_name == 'BYWORD':
                if len(expr.args) != 2:
                    self.error(f"BYWORD expects 2 arguments, got {len(expr.args)}", expr)
                    return None
                for i, arg in enumerate(expr.args):
                    arg_type = self.infer_expression_type(arg)
                    if arg_type == REAL_TYPE:
                        self.error(f"BYWORD: argument {i+1} must be a byte-sized ordinal type, got REAL", expr)
                        return None
                    if arg_type and not isinstance(arg_type, (EnumType, )) and arg_type not in (INTEGER_TYPE, WORD_TYPE, CHAR_TYPE, BOOLEAN_TYPE):
                        self.error(f"BYWORD: argument {i+1} must be an ordinal type, got {arg_type}", expr)
                        return None
                return WORD_TYPE
            if lookup_name in {'TRUNC', 'ROUND'}:
                if len(expr.args) != 1:
                    self.error(f"Function '{lookup_name}' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type == REAL_TYPE:
                    return INTEGER_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected REAL, got {arg_type}", expr)
                return None
            if lookup_name == 'FLOAT':
                if len(expr.args) != 1:
                    self.error(f"Function 'FLOAT' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type == INTEGER_TYPE:
                    return REAL_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER, got {arg_type}", expr)
                return None

            if not sym:
                self.error(f"Undefined function: {expr.name}", expr)
                return None
            if isinstance(sym.type, FunctionType):
                # Check argument count.  Variadic functions accept any number
                # of args >= the fixed parameter count.
                expected_args = len(sym.type.params)
                actual_args = len(expr.args) if expr.args else 0
                _is_variadic_fn2 = getattr(sym.type, 'is_variadic', False)
                if _is_variadic_fn2:
                    if actual_args < expected_args:
                        self.error(f"Function '{expr.name}' expects at least {expected_args} arguments, got {actual_args}", expr)
                elif actual_args != expected_args:
                    self.error(f"Function '{expr.name}' expects {expected_args} arguments, got {actual_args}", expr)
                # Check argument types (fixed params only)
                if expr.args:
                    for i, (arg, (_param_name, param_type)) in enumerate(zip(expr.args, sym.type.params)):
                        # Parameter type as literal context (see the
                        # procedure-call site): constant arguments adopt the
                        # parameter's integer type and range.
                        arg_type = self.infer_expression_type(arg, param_type)
                        if arg_type and not self._can_pass_value_argument(arg_type, param_type) \
                                and not self._const_adapts_to_int_target(arg_type, param_type, arg):
                            self.error(f"Argument {i+1} type mismatch: expected {param_type}, got {arg_type}", expr)
                    # Type-check variadic tail args too (just for expression validity)
                    for arg in (expr.args[expected_args:] if _is_variadic_fn2 else []):
                        self.infer_expression_type(arg)
                return sym.type.return_type
            return None
        elif isinstance(expr, Designator):
            return self.infer_designator_type(expr)
        else:
            # Unknown expression type
            return None

    def is_constant_set_element(self, expr: Expression) -> bool:
        """Return True when a set element/range endpoint is compile-time constant."""
        if isinstance(expr, RangeExpr):
            return self.is_constant_set_element(expr.low) and self.is_constant_set_element(expr.high)
        if isinstance(expr, SetConstructor):
            return all(self.is_constant_set_element(el) for el in expr.elements)
        if isinstance(expr, (IntLiteral, RealLiteral, BoolLiteral, CharLiteral, StringLiteral)):
            return True
        if isinstance(expr, Identifier):
            sym = self.symbol_table.lookup(expr.name)
            return bool(sym and sym.kind == 'const')
        if isinstance(expr, UnaryOp):
            return self.is_constant_set_element(expr.operand)
        if isinstance(expr, BinOp):
            return self.is_constant_set_element(expr.left) and self.is_constant_set_element(expr.right)
        return False

    def _designator_as_typed_set_constructor(self, designator: Designator) -> Optional[SetConstructor]:
        """Return a typed set constructor for TypeName[...] designators.

        The parser cannot reliably distinguish array indexing from IBM
        Pascal's type-prefixed set constructor without symbol information.
        At semantic time, reinterpret only bracket-only designators whose base
        name is a declared set type.
        """
        if not designator.selectors or not all(sel.kind == 'INDEX' for sel in designator.selectors):
            return None
        sym = self.symbol_table.lookup(designator.name)
        if not sym or sym.kind != 'type' or not isinstance(sym.type, SetType):
            return None
        return SetConstructor([sel.index_or_field for sel in designator.selectors], designator.name)

    def _valid_array_index_type(self, index_type: Optional[Type], expected: Type) -> bool:
        """Return whether ``index_type`` is valid for an array selector.

        DEVICE code uses INTEGER32 thread/block indices.  Permit those as array
        indices in DEVICE source while preserving vintage host behavior.

        Host code gets the same INTEGER32 allowance under ``wide-integers``:
        heap super arrays can exceed 32767 elements (the NEW super-array
        bound is itself dynamic), so a program that allocates a large buffer
        with long-form NEW must be able to index it with a wide integer.
        This is extension surface, gated exactly like the type it needs.
        """
        if index_type is None:
            return False
        if index_type.equivalent_to(expected):
            return True
        wide_ok = self.in_device_module or self.feature_enabled('wide-integers')
        return wide_ok and index_type.equivalent_to(INTEGER32_TYPE) and expected.equivalent_to(INTEGER_TYPE)

    def infer_designator_type(self, designator: Designator) -> Optional[Type]:
        """Infer the type of a designator (with selectors for array/record access)."""
        typed_set = self._designator_as_typed_set_constructor(designator)
        if typed_set is not None:
            return self.infer_expression_type(typed_set)

        # Special case: inside a function, referencing the function name gets the return type
        if self.current_function and designator.name == self.current_function.name:
            current_type = self.current_function_return_type
            if not current_type:
                return None
        else:
            # Look up the base name
            sym = self.symbol_table.lookup(designator.name)
            if not sym:
                self.error(f"Undefined variable: {designator.name}", designator)
                return None
            current_type = sym.type
            if isinstance(current_type, FunctionType) and not current_type.params:
                current_type = current_type.return_type

        # Process selectors (array indexing, field access, pointer dereference)
        if designator.selectors:
            for selector in designator.selectors:
                if selector.kind == 'INDEX':
                    # STRING/LSTRING are character-indexable (manual: S[I] is
                    # the Ith character; LSTRING index 0 is the length byte,
                    # viewed as a CHAR).  Codegen already lowers this (the
                    # length-prefix convention -- see array_lower_bound's
                    # deliberate string exclusion); this makes the checker
                    # agree.
                    if isinstance(current_type, (StringType, LStringType)):
                        if selector.index_or_field:
                            idx_t = self.infer_expression_type(selector.index_or_field)
                            if idx_t and not self._valid_array_index_type(idx_t, INTEGER_TYPE):
                                self.error(f"String index must be INTEGER, got {idx_t}", designator)
                        current_type = CHAR_TYPE
                        continue
                    # Array indexing
                    if not isinstance(current_type, ArrayType):
                        self.error(f"Cannot index non-array type {current_type}", designator)
                        return None
                    # Check that the index matches the array's index type
                    if selector.index_or_field:
                        index_type = self.infer_expression_type(selector.index_or_field)
                        expected = current_type.effective_index_type
                        if index_type and not self._valid_array_index_type(index_type, expected):
                            self.error(f"Array index must be {expected}, got {index_type}", designator)
                    current_type = current_type.element_type

                elif selector.kind == 'FIELD':
                    field_name = str(selector.index_or_field).upper()
                    if isinstance(current_type, FileType):
                        if field_name == 'MODE':
                            current_type = EnumType(['SEQUENTIAL', 'TERMINAL', 'DIRECT'], name='FILEMODES')
                        elif field_name == 'TRAP':
                            # Trapped I/O: assignable BOOLEAN (see expression side).
                            current_type = BOOLEAN_TYPE
                        elif field_name == 'ERRS':
                            current_type = INTEGER_TYPE
                        else:
                            self.error(f"File control block has no field '{selector.index_or_field}'", designator)
                            return None
                    elif isinstance(current_type, LStringType):
                        if field_name == 'LEN':
                            current_type = CHAR_TYPE
                        else:
                            self.error(f"LSTRING has no field '{selector.index_or_field}'", designator)
                            return None
                    else:
                        # Record field access
                        if not isinstance(current_type, RecordType):
                            self.error(f"Cannot access field on non-record type {current_type}", designator)
                            return None
                        field_name_orig = selector.index_or_field
                        field_type = current_type.get_field_type(field_name_orig)
                        if not field_type:
                            self.error(f"Record has no field '{field_name_orig}'", designator)
                            return None
                        current_type = field_type

                elif selector.kind == 'DEREF':
                    # Pointer dereference, or Pascal file buffer variable F^.
                    if isinstance(current_type, FileType):
                        current_type = current_type.element_type
                    elif isinstance(current_type, PointerType):
                        self._check_deref_space(current_type, designator)
                        current_type = current_type.target_type
                    else:
                        self.error(f"Cannot dereference non-pointer/non-file type {current_type}", designator)
                        return None

        return current_type
