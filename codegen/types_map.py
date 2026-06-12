"""
Types Map mixin for Codegen.

Type system operations: conversion, coercion, sizing.
Handles llvm_type conversion, parameter handling, type size computation.

Part of Plan 1 refactoring (mixin-based architecture).
Checklist: 4.1 (type system), 4.4 (RETYPE intrinsic)
"""

from __future__ import annotations

from typing import Any, List, Optional, Union

import llvmlite.ir as ir

from ast_nodes import *
from codegen.base import CodegenError
from type_system import LStringType as ResolvedLStringType
from type_system import StringType as ResolvedStringType


class TypesMapMixin:
    """Mixin for type system operations."""

    def llvm_type(self, type_expr: Type) -> ir.Type:
        """Convert a Pascal type to LLVM type."""
        if isinstance(type_expr, BuiltinType):
            if type_expr.name == 'INTEGER':
                return ir.IntType(32)
            elif type_expr.name == 'BOOLEAN':
                return ir.IntType(8)  # one byte, so adr/sizeof/fillc agree on layout
            elif type_expr.name == 'WORD':
                return ir.IntType(16)
            elif type_expr.name == 'CHAR':
                return ir.IntType(8)
            elif type_expr.name == 'REAL':
                return ir.DoubleType()
            elif type_expr.name == 'ADRMEM':
                return ir.PointerType(ir.IntType(8))  # pointer/address
            elif type_expr.name == 'ADSMEM':
                return ir.LiteralStructType([ir.PointerType(ir.IntType(8)), ir.IntType(16)])
            else:
                raise CodegenError(f'Unknown built-in type: {type_expr.name}')
        elif isinstance(type_expr, NamedType):
            name_up = type_expr.name.upper()
            if name_up == 'LSTRING':
                # LSTRING without explicit param: use default 256
                param_val = int(type_expr.param) if type_expr.param else 256
                return ir.ArrayType(ir.IntType(8), param_val + 1)
            elif name_up == 'STRING':
                # STRING without explicit param: use default 256
                param_val = int(type_expr.param) if type_expr.param else 256
                return ir.ArrayType(ir.IntType(8), param_val)
            if name_up == 'ADRMEM':
                return ir.PointerType(ir.IntType(8))
            elif name_up == 'ADSMEM':
                # Segmented address: {flat pointer, segment word}, matching how
                # ADS pointers (ADS OF CHAR) lower.
                return ir.LiteralStructType([ir.PointerType(ir.IntType(8)), ir.IntType(16)])
            elif name_up == 'INTEGER':
                return ir.IntType(32)
            elif name_up == 'BOOLEAN':
                return ir.IntType(8)
            elif name_up == 'WORD':
                return ir.IntType(16)
            elif name_up == 'REAL':
                return ir.DoubleType()
            elif name_up == 'CHAR':
                return ir.IntType(8)
            elif name_up == 'TEXT':
                return ir.PointerType(ir.IntType(8))
            elif name_up == 'FILEMODES':
                return ir.IntType(32)
            elif name_up == 'FCBFQQ':
                return self.llvm_type(self.resolve_type_alias(type_expr))
            if name_up in self.type_aliases:
                return self.llvm_type(self.type_aliases[name_up])
            return ir.IntType(32)
        elif isinstance(type_expr, EnumType):
            return ir.IntType(32)
        elif isinstance(type_expr, SetType):
            return self.set_llvm_type()
        elif isinstance(type_expr, FileType):
            # File variables are opaque runtime handles. Their element type and
            # TEXT-vs-binary structure remain in the Pascal type metadata; the
            # runtime owns the actual file-control block and current buffer.
            return ir.PointerType(ir.IntType(8))
        elif isinstance(type_expr, LStringType):
            # LSTRING(n) is PACKED ARRAY [0..n] OF CHAR = [n+1 x i8]
            return ir.ArrayType(ir.IntType(8), type_expr.max_len + 1)
        elif isinstance(type_expr, ResolvedLStringType):
            # LSTRING(n) is PACKED ARRAY [0..n] OF CHAR = [n+1 x i8]
            return ir.ArrayType(ir.IntType(8), type_expr.max_len + 1)
        elif isinstance(type_expr, ResolvedStringType):
            # STRING(n) is PACKED ARRAY [1..n] OF CHAR = [n x i8]
            return ir.ArrayType(ir.IntType(8), type_expr.max_len)
        elif isinstance(type_expr, SubrangeType):
            if type_expr.host:
                return self.llvm_type(NamedType(type_expr.host, None))
            return ir.IntType(32)
        elif isinstance(type_expr, PointerType):
            base_type = self.llvm_type(type_expr.base)
            if getattr(type_expr, 'flavor', 'POINTER') == 'ADS':
                return ir.LiteralStructType([ir.PointerType(base_type), ir.IntType(16)])
            return ir.PointerType(base_type)
        elif isinstance(type_expr, ArrayType):
            elem_type = self.llvm_type(type_expr.element_type)
            # Compute actual array size
            try:
                low_val = self.eval_const_expr(type_expr.index_range.low)
                high_val = self.eval_const_expr(type_expr.index_range.high) if type_expr.index_range.high else low_val + 99
                size = high_val - low_val + 1
            except Exception:
                size = 100
            return ir.ArrayType(elem_type, size)
        elif isinstance(type_expr, RecordType):
            # AST RecordType.fields is a list of (name_list, field_type) pairs.
            # Lay the record out as an LLVM struct in declaration order, one
            # struct element per field name (so `x, y: INTEGER` -> two i32s).
            # record_field_index() uses this same ordering to address fields.
            elem_types: List[ir.Type] = []
            for names, ftype in type_expr.fields:
                lt = self.llvm_type(ftype)
                for _ in names:
                    elem_types.append(lt)
            return ir.LiteralStructType(elem_types)
        else:
            raise CodegenError(f'Type {type(type_expr).__name__} not yet supported')

    def param_llvm_type(self, param: Param) -> ir.Type:
        base = self.llvm_type(param.type_expr)
        if param.mode in {'VAR', 'VARS', 'CONST', 'CONSTS'}:
            # LLVM lowering: near and far reference parameters both use ordinary
            # pointers on this target. Far modes preserve source-level mode
            # metadata; the segment component is degenerate, as with ADS.
            return ir.PointerType(base)
        return base

    # ========================================================================
    # Main Entry Point
    # ========================================================================

    def get_type_size(self, t: Type) -> int:
        """Size in bytes of an AST type node (consults constants for bounds)."""
        if isinstance(t, BuiltinType):
            return self._scalar_size(t.name)
        elif isinstance(t, NamedType):
            if t.name.upper() in {'STRING', 'LSTRING'}:
                return (int(t.param) if isinstance(t.param, int) else 256) + 1
            return self._scalar_size(t.name)
        elif isinstance(t, SetType):
            return 32
        elif isinstance(t, (LStringType, ResolvedStringType, ResolvedLStringType)):
            return max(1, getattr(t, 'max_len', 256)) + 1
        elif isinstance(t, SubrangeType):
            return self._scalar_size(t.host) if t.host else 4
        elif isinstance(t, ArrayType):
            low = self.eval_const_expr(t.index_range.low)
            high = self.eval_const_expr(t.index_range.high) if t.index_range.high else low
            count = high - low + 1
            return count * self.get_type_size(t.element_type)
        elif isinstance(t, PointerType):
            return 8  # 64-bit pointer
        elif isinstance(t, RecordType):
            # AST RecordType.fields is a list of (name_list, type) pairs
            total = 0
            for names, ftype in t.fields:
                total += len(names) * self.get_type_size(ftype)
            return total
        else:
            return 4  # fallback

    def zero_initializer(self, llvm_type: ir.Type) -> ir.Value:
        """Produce a valid zero initializer for any LLVM type.

        Aggregates (arrays/structs) and pointers cannot be initialized with a
        scalar 0 -- llvmlite would try to iterate the int. ``None`` renders as
        ``zeroinitializer`` (and ``null`` for pointers), which is valid for any
        type; scalars keep an explicit 0 for readable IR.
        """
        if isinstance(llvm_type, ir.IntType):
            return ir.Constant(llvm_type, 0)
        return ir.Constant(llvm_type, None)

    def coerce_arg(self, value: ir.Value, target_type: ir.Type) -> ir.Value:
        """Coerce a call argument to the callee's declared parameter type.

        Handles the two cases the vintage benchmark needs: any-pointer-to-any
        -pointer (adrmem) via bitcast, and integer width adjustment (e.g. an
        i32 expression into a WORD/i16 parameter).
        """
        vt = value.type
        if vt == target_type:
            return value

        def _is_seg(t):
            return (isinstance(t, ir.LiteralStructType) and len(t.elements) == 2 and isinstance(t.elements[0], ir.PointerType) and isinstance(t.elements[1], ir.IntType))

        # Segmented address (ADS) reconciliation. ADS values lower to a
        # {pointer, segment} pair whose pointer field is typed to the pointee
        # (e.g. {[4 x i8]*, i16} for `ADS` of an array), which may not match a
        # segmented parameter's {i8*, i16} or a flat pointer parameter.
        if _is_seg(vt) and _is_seg(target_type):
            ptr = self.builder.bitcast(self.builder.extract_value(value, 0), target_type.elements[0])
            seg = self.builder.extract_value(value, 1)
            out = self.builder.insert_value(ir.Constant(target_type, ir.Undefined), ptr, 0)
            return self.builder.insert_value(out, seg, 1)
        if _is_seg(vt) and isinstance(target_type, ir.PointerType):
            # Segmented value into a flat pointer parameter: drop the segment.
            return self.builder.bitcast(self.builder.extract_value(value, 0), target_type)
        if isinstance(vt, ir.PointerType) and _is_seg(target_type):
            # Flat pointer into a segmented parameter: segment zero.
            ptr = self.builder.bitcast(value, target_type.elements[0])
            out = self.builder.insert_value(ir.Constant(target_type, ir.Undefined), ptr, 0)
            return self.builder.insert_value(out, ir.Constant(target_type.elements[1], 0), 1)

        if isinstance(target_type, ir.PointerType) and isinstance(vt, ir.PointerType):
            return self.builder.bitcast(value, target_type)
        if isinstance(target_type, ir.IntType) and isinstance(vt, ir.IntType):
            if vt.width > target_type.width:
                return self.builder.trunc(value, target_type)
            elif vt.width < target_type.width:
                return self.builder.zext(value, target_type)
        if isinstance(target_type, ir.DoubleType) and isinstance(vt, ir.IntType):
            return self.builder.sitofp(value, target_type)
        if isinstance(target_type, ir.IntType) and isinstance(vt, ir.DoubleType):
            return self.builder.fptosi(value, target_type)
        return value

    def to_bool(self, cond: ir.Value) -> ir.Value:
        """Reduce a condition value to an i1 for a branch.

        An already-i1 value is used directly; wider integers (e.g. an i8
        BOOLEAN load or an i32) are compared against zero.
        """
        if isinstance(cond.type, ir.IntType):
            if cond.type.width == 1:
                return cond
            return self.builder.icmp_signed('!=', cond, ir.Constant(cond.type, 0))
        return cond

    # ========================================================================
    # Expressions
    # ========================================================================

    def get_string_type_info(self, t: Type) -> tuple[bool, int, bool]:
        """Returns (is_str, max_len, is_lstring) for any AST Type or Resolved Type."""
        from type_system import LStringType as ResolvedLStringType
        from type_system import StringType as ResolvedStringType

        if isinstance(t, (ResolvedLStringType, ResolvedStringType)):
            return True, t.max_len, isinstance(t, ResolvedLStringType)

        # Check AST LStringType
        if isinstance(t, LStringType):
            return True, t.max_len, True

        # Check NamedType
        if isinstance(t, NamedType):
            name_up = t.name.upper()
            if name_up == 'LSTRING':
                return True, (int(t.param) if t.param is not None else 256), True
            elif name_up == 'STRING':
                return True, (int(t.param) if t.param is not None else 256), False
            elif name_up in self.type_aliases:
                return self.get_string_type_info(self.type_aliases[name_up])

        return False, 256, False

    def _array_bounds_or_none(self, type_expr) -> tuple:
        """(low, high) for a genuine array type with constant-resolvable
        bounds, else (None, None).  Unlike get_array_bounds, never guesses
        — INDEXCK checks are emitted only against known declared bounds.
        STRING/LSTRING are excluded (length-prefix convention, not array
        bounds; their capacity is guarded by the RANGECK string gates)."""
        t = self.resolve_type_alias(type_expr)
        if hasattr(t, 'max_len'):
            return None, None
        if hasattr(t, 'index_range') and getattr(t, 'index_range', None) is not None:
            try:
                low = self.eval_const_expr(t.index_range.low)
                high = self.eval_const_expr(t.index_range.high) if t.index_range.high is not None else low
                if isinstance(low, int) and isinstance(high, int):
                    return low, high
            except Exception:
                pass
            return None, None
        if hasattr(t, 'lower_bound') and hasattr(t, 'upper_bound'):
            lo, hi = t.lower_bound, t.upper_bound
            if isinstance(lo, int) and isinstance(hi, int):
                return lo, hi
        return None, None

    def resolve_designator_ptr(self, designator: Designator) -> ir.Value:
        """Resolve a designator to its LLVM pointer (handles arrays/selectors)."""
        symbol = self.scope.lookup(designator.name)
        if not symbol:
            symbol = self.scope.lookup(designator.name.upper())
            if not symbol:
                raise CodegenError(f'Undefined variable: {designator.name}')

        ptr = symbol.llvm_value
        cur_type = symbol.type_expr

        if designator.selectors:
            for selector in designator.selectors:
                if selector.kind == 'INDEX':
                    index = self.codegen_expr(selector.index_or_field)
                    # $INDEXCK (manual: default +, "bounds checking is
                    # separate from other subrange checking"): emit
                    # low <= idx <= high against the declared bounds.
                    # Constant indices provably in range skip the check;
                    # checks are emitted only when both bounds are known.
                    low_b, high_b = self._array_bounds_or_none(cur_type)
                    if (low_b is not None and high_b is not None
                            and isinstance(index.type, ir.IntType)
                            and self.check_enabled('INDEXCK')):
                        const_idx = None
                        try:
                            const_idx = self.eval_const_expr(selector.index_or_field)
                        except Exception:
                            const_idx = None
                        if not (isinstance(const_idx, int) and low_b <= const_idx <= high_b):
                            ge = self.builder.icmp_signed('>=', index, ir.Constant(index.type, low_b))
                            le = self.builder.icmp_signed('<=', index, ir.Constant(index.type, high_b))
                            self._emit_runtime_check(self.builder.and_(ge, le), 'indexck')
                    # Pascal array indices are relative to the declared lower
                    # bound, but storage is allocated as [high-low+1 x elem]
                    # (0-based). Translate the index to a 0-based slot so that
                    # e.g. ARRAY[5..7] indexed by 5 lands on slot 0, not slot 5
                    # (which would read/write outside the allocation).
                    low, elem_type = self.array_lower_bound(cur_type)
                    if low is not None and low != 0 and isinstance(index.type, ir.IntType):
                        index = self.builder.sub(index, ir.Constant(index.type, low))
                    # GEP requires [0, index] for pointers to arrays, or [index] for flat pointers
                    if isinstance(ptr.type.pointee, ir.ArrayType):
                        ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), index])
                    else:
                        ptr = self.builder.gep(ptr, [index])
                    cur_type = elem_type
                elif selector.kind == 'FIELD':
                    base = self.resolve_type_alias(cur_type) if cur_type is not None else None
                    if isinstance(base, FileType):
                        field = str(selector.index_or_field).upper()
                        handle = self.builder.load(ptr)
                        fcb = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
                        if field == 'MODE':
                            ptr = self.builder.gep(fcb, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 7)])
                            cur_type = NamedType('FILEMODES', None)
                        elif field == 'TRAP':
                            # Trapped I/O (manual ch.12): BOOLEAN slot 8.
                            ptr = self.builder.gep(fcb, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 8)])
                            cur_type = NamedType('BOOLEAN', None)
                        elif field == 'ERRS':
                            ptr = self.builder.gep(fcb, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 9)])
                            cur_type = NamedType('INTEGER', None)
                        else:
                            raise CodegenError(f"Cannot access FCB field '{selector.index_or_field}' on type {cur_type}")
                    else:
                        # Record field access: GEP to the field's struct slot.
                        fidx, ftype = self.record_field_index(cur_type, selector.index_or_field)
                        if fidx is None:
                            raise CodegenError(f"Cannot access field '{selector.index_or_field}' on type {cur_type}")
                        ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), fidx)])
                        cur_type = ftype
                elif selector.kind == 'DEREF':
                    base = self.resolve_type_alias(cur_type) if cur_type is not None else None
                    if isinstance(base, FileType):
                        # File buffer variable F^: access the runtime-owned
                        # current-component buffer. TEXT buffer loads go
                        # through a lazy-touch hook, per the manual model.
                        ptr = self._file_buffer_ptr(ptr, base.element_type, getattr(base, 'structure', 'BINARY') == 'ASCII')
                        cur_type = base.element_type
                    else:
                        # Pointer dereference
                        ptr = self.builder.load(ptr)
                        # $NILCK (manual: default +): error on dereferencing
                        # NIL (0) or — only with $INITCK — the uninitialized
                        # sentinel (1).  The manual's odd-pointer and
                        # free-block checks are 8086 heap-model artifacts
                        # with no analog here (byte-aligned data makes odd
                        # pointers legal); documented adaptation.
                        if self.check_enabled('NILCK'):
                            ptr_int = self.builder.ptrtoint(ptr, ir.IntType(64))
                            ok = self.builder.icmp_unsigned('!=', ptr_int, ir.Constant(ir.IntType(64), 0))
                            if self.check_enabled('INITCK'):
                                not_sentinel = self.builder.icmp_unsigned('!=', ptr_int, ir.Constant(ir.IntType(64), 1))
                                ok = self.builder.and_(ok, not_sentinel)
                            self._emit_runtime_check(ok, 'nilck')
                        cur_type = getattr(base, 'base', None) or getattr(base, 'target_type', None)
        return ptr

    # ========================================================================
    # Type-size, argument coercion, and boolean helpers
    # ========================================================================

    def resolve_type_alias(self, type_expr):
        """Unwrap NamedType aliases (e.g. ``TYPE arr = ARRAY[..]``) to the
        underlying declared type. Built-in names and unknown names are returned
        unchanged. Cycle-safe."""
        seen = set()
        while isinstance(type_expr, NamedType):
            key = type_expr.name.upper()
            if key == 'TEXT':
                return FileType(NamedType('CHAR', None), structure='ASCII')
            if key == 'FILEMODES':
                return EnumType(['SEQUENTIAL', 'TERMINAL', 'DIRECT'])
            if key == 'FCBFQQ':
                return RecordType([(['MODE'], NamedType('FILEMODES', None)), (['TRAP'], NamedType('BOOLEAN', None)), (['ERRS'], NamedType('INTEGER', None))], False)
            if key in seen or key not in self.type_aliases:
                break
            seen.add(key)
            type_expr = self.type_aliases[key]
        return type_expr

    # ------------------------------------------------------------------
    # Enum support (checklist 9.8)
    # ------------------------------------------------------------------

    def coerce_printf_int(self, val: ir.Value) -> ir.Value:
        """printf dynamic width/precision arguments must be C int-sized."""
        if isinstance(val.type, ir.IntType):
            if val.type.width < 32:
                return self.builder.zext(val, ir.IntType(32))
            if val.type.width > 32:
                return self.builder.trunc(val, ir.IntType(32))
        return val

    def retype_source_is_pointer_value(self, expr) -> Optional[bool]:
        """Classify the inner expression of a RETYPE for the pointer-vs-aggregate
        conflation documented in checklist item 9.9.

        ``codegen_expr`` returns an LLVM pointer for two unrelated reasons:

        * the value *is* an aggregate (STRING/LSTRING/ARRAY/RECORD) and the
          pointer is merely the address of those bytes — RETYPE should
          reinterpret the *pointee* (load through the bitcast);
        * the value is a genuine Pascal pointer scalar (a ``^T`` variable, an
          ``ADR``/``ADS`` factor, ``NIL``) — RETYPE should reinterpret the
          *address bits*, not dereference them.

        Returns ``True`` if the inner expression is a genuine pointer value,
        ``False`` if it is an aggregate address, and ``None`` if it cannot be
        classified from the AST alone (caller falls back to the LLVM type).
        """
        # ADR/ADS factors and NIL are always pointer *values*.
        if isinstance(expr, (AdrExpr, AdsExpr, NilLiteral)):
            return True
        # A nested RETYPE's value type is its declared target type.
        if isinstance(expr, RetypeExpr):
            t = self.resolve_type_alias(NamedType(expr.type_id, None))
            return isinstance(t, PointerType)
        # Named variables/designators: consult the declared Pascal type.
        if isinstance(expr, (Identifier, Designator)):
            sym = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if sym is None or sym.type_expr is None:
                return None
            t = self.resolve_type_alias(sym.type_expr)
            # A selector chain (field/index/deref) yields whatever the selected
            # component is; that is not necessarily a pointer, so do not claim
            # to know — let the caller fall back to the LLVM-type heuristic.
            if getattr(expr, 'selectors', None):
                return None
            return isinstance(t, PointerType)
        return None
