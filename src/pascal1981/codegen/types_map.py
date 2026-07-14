"""
Types Map mixin for Codegen.

Type system operations: conversion, coercion, sizing.
Handles llvm_type conversion, parameter handling, type size computation.

Checklist: 4.1 (type system), 4.4 (RETYPE intrinsic)
"""

from __future__ import annotations

from typing import List, Optional

import llvmlite.ir as ir

from ..ast_nodes import (AdrExpr, AdsExpr, ArrayType, BuiltinType, Designator, EnumType, FileType, Identifier, LStringType, NamedType, NilLiteral, Param, PointerType, RecordType,
                         RetypeExpr, SetType, SubrangeType, Type)
from ..type_system import CHAR_TYPE
from ..type_system import ArrayType as ResolvedArrayType
from ..type_system import LStringType as ResolvedLStringType
from ..type_system import StringType as ResolvedStringType
from .base import CodegenError


class TypesMapMixin:
    """Mixin for type system operations."""

    def llvm_type(self, type_expr: Type) -> ir.Type:
        """Convert a Pascal type to LLVM type."""
        if isinstance(type_expr, BuiltinType):
            if type_expr.name == 'INTEGER':
                return ir.IntType(16)
            elif type_expr.name == 'INTEGER32':
                return ir.IntType(32)
            elif type_expr.name == 'INTEGER64':
                return ir.IntType(64)
            elif type_expr.name == 'BOOLEAN':
                return ir.IntType(8)  # one byte, so adr/sizeof/fillc agree on layout
            elif type_expr.name == 'WORD':
                return ir.IntType(16)
            elif type_expr.name == 'WORD16':
                return ir.IntType(16)
            elif type_expr.name == 'WORD8':
                return ir.IntType(8)
            elif type_expr.name == 'INTEGER8':
                return ir.IntType(8)
            elif type_expr.name == 'WORD32':
                return ir.IntType(32)
            elif type_expr.name == 'WORD64':
                return ir.IntType(64)
            elif type_expr.name == 'INTEGER16':
                return ir.IntType(16)
            elif type_expr.name == 'CHAR':
                return ir.IntType(8)
            elif type_expr.name == 'REAL':
                return ir.DoubleType()
            elif type_expr.name == 'REAL64':
                return ir.DoubleType()
            elif type_expr.name == 'REAL32':
                return ir.FloatType()
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
                return ir.IntType(16)
            elif name_up == 'INTEGER8':
                return ir.IntType(8)
            elif name_up == 'INTEGER16':
                return ir.IntType(16)
            elif name_up == 'INTEGER32':
                return ir.IntType(32)
            elif name_up == 'INTEGER64':
                return ir.IntType(64)
            elif name_up == 'BOOLEAN':
                return ir.IntType(8)
            elif name_up == 'WORD':
                return ir.IntType(16)
            elif name_up == 'WORD16':
                return ir.IntType(16)
            elif name_up == 'WORD8':
                return ir.IntType(8)
            elif name_up == 'WORD32':
                return ir.IntType(32)
            elif name_up == 'WORD64':
                return ir.IntType(64)
            elif name_up == 'REAL':
                return ir.DoubleType()
            elif name_up == 'REAL64':
                return ir.DoubleType()
            elif name_up == 'REAL32':
                return ir.FloatType()
            elif name_up == 'CHAR':
                return ir.IntType(8)
            elif name_up == 'TEXT':
                return ir.PointerType(ir.IntType(8))
            elif name_up == 'FILEMODES':
                return ir.IntType(32)
            elif name_up == 'FCBFQQ':
                return self.llvm_type(self.resolve_type_alias(type_expr))
            if name_up in self.type_aliases:
                aliased = self.type_aliases[name_up]
                if isinstance(aliased, RecordType):
                    return self.named_record_struct(name_up, aliased)
                return self.llvm_type(aliased)
            raise CodegenError(f'Unknown named type: {type_expr.name}')
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
                if self.is_device_module:
                    # Inside a DEVICE MODULE, ADS(s) OF T lowers to a typed
                    # address-space pointer (T addrspace(k)*), collapsing the
                    # vintage {ptr, i16} segmented pair (design S5.3).
                    space_expr = getattr(type_expr, 'space', None)
                    space_ord = self.eval_const_expr(space_expr) if space_expr is not None else 0
                    return ir.PointerType(base_type, addrspace=self._space_addrspace(space_ord))
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
        # Resolve named aliases first. A variable declared with a user TYPE name
        # (record, array, wide-int alias) or a C alias (CINT/CLONG/...) reaches
        # here as a NamedType; without unwrapping it, the NamedType branch below
        # falls through to _scalar_size, whose default is 4 -- the long-standing
        # "SIZEOF(record) == 4" bug, which also hit named arrays and CLONG.
        t = self.resolve_type_alias(t)
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
            # Size the record exactly as it is laid out in memory -- field
            # alignment and tail padding included -- using the same layout helper
            # the C-ABI marshaller trusts, so SIZEOF agrees with both the
            # allocation and the C ABI rather than a naive field-byte sum.
            from .c_abi import _size_of
            return _size_of(self.llvm_type(t))
        else:
            return 4  # fallback

    def natural_alignment(self, llvm_type: ir.Type) -> int:
        """Natural byte alignment of an LLVM type for the NVPTX target.

        Used to annotate device pointer parameters with a tight ``.align N``
        hint instead of the backend's conservative ``.align 1``. Scalars align
        to their width (1/2/4/8); arrays/records take the max element alignment;
        pointers align to 8 on the 64-bit address-size target. This is the
        alignment the element type is known to carry, so it is a correctness-
        neutral hint -- it never over-promises what the caller actually passes.
        """
        if isinstance(llvm_type, ir.IntType):
            return max(1, llvm_type.width // 8)
        if isinstance(llvm_type, ir.FloatType):
            return 4
        if isinstance(llvm_type, ir.DoubleType):
            return 8
        if isinstance(llvm_type, ir.PointerType):
            return 8  # 64-bit address space on nvptx64
        if isinstance(llvm_type, ir.ArrayType):
            return self.natural_alignment(llvm_type.element)
        if isinstance(llvm_type, ir.LiteralStructType):
            best = 1
            for el in llvm_type.elements:
                best = max(best, self.natural_alignment(el))
            return best
        return 1  # conservative fallback

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

    def coerce_arg(self, value: ir.Value, target_type: ir.Type, src_expr=None) -> ir.Value:
        """Coerce a call argument to the callee's declared parameter type.

        Handles the two cases the vintage benchmark needs: any-pointer-to-any
        -pointer (adrmem) via bitcast, and integer width adjustment (e.g. an
        i32 expression into a WORD/i16 parameter).

        ``src_expr`` (optional) is the Pascal source expression being coerced.
        When an integer is *widened*, the correct extension depends on the
        Pascal source signedness, not the LLVM width: a signed INTEGER/INTEGER32
        must sign-extend, an unsigned WORD must zero-extend.  Passing ``src_expr``
        lets us consult ``_expr_is_unsigned_word`` and pick ``sext``/``zext``
        accordingly (Finding 1: this path used to always ``zext``, silently
        turning a negative INTEGER positive when widened into a wider parameter).
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
            # Segmented value into a flat pointer parameter.
            #
            # In *host* (vintage) code this is the correct seg->flat collapse:
            # the host is a single flat address space, the segment word is
            # always 0, and dropping it loses nothing (e.g. passing `ADS x` to
            # an ADRMEM/flat-pointer parameter).
            #
            # In *device* code a {ptr, i16} value reaching here would mean a
            # genuine address-space crossing was about to be silently dropped,
            # which the design forbids (S6.3: cross-space is data movement, not a
            # cast).  Device ADS values lower to bare addrspace pointers and are
            # reconciled by the bare-pointer path below, so this branch should be
            # unreachable in device code; fail loudly if it is ever reached
            # rather than emit a silent segment drop.  (followups.md item 4.)
            if self.is_device_module:
                raise CodegenError("cannot silently drop an address-space segment "
                                   "when crossing to a flat pointer in device code")
            return self.builder.bitcast(self.builder.extract_value(value, 0), target_type)
        if isinstance(vt, ir.PointerType) and _is_seg(target_type):
            # Flat pointer into a segmented parameter: segment zero.
            ptr = self.builder.bitcast(value, target_type.elements[0])
            out = self.builder.insert_value(ir.Constant(target_type, ir.Undefined), ptr, 0)
            return self.builder.insert_value(out, ir.Constant(target_type.elements[1], 0), 1)

        if isinstance(target_type, ir.PointerType) and isinstance(vt, ir.PointerType):
            # Never silently cross address spaces (design S6.3). A bitcast
            # cannot change addrspace, and no-mixing is enforced in the checker, so a
            # differing addrspace here means something slipped through -- fail loudly
            # rather than emit illegal IR.
            if getattr(vt, 'addrspace', 0) != getattr(target_type, 'addrspace', 0):
                raise CodegenError("cannot implicitly cross address spaces when passing an argument")
            return self.builder.bitcast(value, target_type)
        if isinstance(target_type, ir.IntType) and isinstance(vt, ir.IntType):
            if vt.width > target_type.width:
                return self.builder.trunc(value, target_type)
            elif vt.width < target_type.width:
                # Widening: choose the extension from the Pascal source signedness.
                # WORD (and unsigned-WORD expressions) zero-extend; every signed
                # integer type (INTEGER/INTEGER32/INTEGER64) sign-extends.
                if src_expr is not None and self._expr_is_unsigned_word(src_expr):
                    return self.builder.zext(value, target_type)
                if src_expr is not None:
                    return self.builder.sext(value, target_type)
                # No source expression (internal/runtime coercions): preserve the
                # historical zero-extend.  After the WORD/INTEGER strictness gate
                # in the type checker, a signedness-crossing widening cannot reach
                # here implicitly from user code, and every user-facing argument
                # call site below passes src_expr.
                return self.builder.zext(value, target_type)
        _floats = (ir.FloatType, ir.DoubleType)
        # Integer -> floating (REAL/REAL32): sitofp into the target float width.
        if isinstance(target_type, _floats) and isinstance(vt, ir.IntType):
            return self.builder.sitofp(value, target_type)
        # Floating -> integer: fptosi.
        if isinstance(target_type, ir.IntType) and isinstance(vt, _floats):
            return self.builder.fptosi(value, target_type)
        # Floating width adjustment: REAL32 (float) <-> REAL/REAL64 (double).
        if isinstance(target_type, _floats) and isinstance(vt, _floats) and type(target_type) is not type(vt):
            if isinstance(target_type, ir.DoubleType):
                return self.builder.fpext(value, target_type)
            return self.builder.fptrunc(value, target_type)
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
        from ..type_system import LStringType as ResolvedLStringType
        from ..type_system import StringType as ResolvedStringType

        if isinstance(t, (ResolvedLStringType, ResolvedStringType)):
            return True, t.max_len, isinstance(t, ResolvedLStringType)
        if isinstance(t, ResolvedArrayType) and t.packed and t.element_type.equivalent_to(CHAR_TYPE):
            return True, t.upper_bound - t.lower_bound + 1, False

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

        resolved = self.resolve_type_alias(t)
        if resolved is not t:
            return self.get_string_type_info(resolved)
        if isinstance(t, ArrayType) and getattr(t, 'packed', False):
            elem = self.resolve_type_alias(t.element_type)
            elem_name = getattr(elem, 'name', '').upper()
            if elem_name == 'CHAR' or elem is CHAR_TYPE:
                low = self.eval_const_expr(t.index_range.low)
                high = self.eval_const_expr(t.index_range.high)
                return True, high - low + 1, False

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
            if getattr(t, 'super', False) or t.index_range.high is None:
                # Super array [low..*]: the upper bound is dynamic, so there is
                # no declared static (low, high) pair to check against. The
                # dereferenced-heap case gets a dynamic header check in
                # resolve_designator_ptr_typed instead.
                return None, None
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

    def named_record_struct(self, name: str, ast_record) -> ir.Type:
        """Build (once) an LLVM identified struct for a named record type.

        Named records lower to an identified struct keyed by the type name
        rather than an anonymous literal struct, so a field that points back at
        the same record (``next: ^node`` in a linked list) can reference the
        struct by identity instead of recursively rebuilding it forever. The
        handle is cached *before* its body is set, so self-references reached
        while laying out the fields resolve to the in-progress (opaque) handle
        and the recursion terminates.
        """
        existing = self._identified_records.get(name)
        if existing is not None:
            return existing
        st = self.module.context.get_identified_type(name)
        self._identified_records[name] = st
        elem_types: List[ir.Type] = []
        for names, ftype in ast_record.fields:
            lt = self.llvm_type(ftype)
            for _ in names:
                elem_types.append(lt)
        if st.is_opaque:
            st.set_body(*elem_types)
        return st

    def resolve_designator_type_expr(self, expr):
        """Best-effort Pascal type expression of a designator, selector chain
        included.

        Walks INDEX selectors into array element types, DEREF selectors into
        pointer pointees, and FIELD selectors into record field types,
        resolving named aliases at each step.  Returns None when a step cannot
        be resolved (the callers fall back to their existing behavior).  This
        is what lets WRITE and the signedness query see that ``a[i]`` is a
        WORD8 element rather than just "some array".
        """
        if not isinstance(expr, (Identifier, Designator)):
            return None
        sym = self.scope.lookup(expr.name)
        ty = getattr(sym, 'type_expr', None) if sym else None
        if ty is None:
            return None
        selectors = expr.selectors if isinstance(expr, Designator) else []
        for sel in selectors:
            ty = self.resolve_type_alias(ty)
            if sel.kind == 'INDEX':
                if isinstance(ty, ArrayType):
                    ty = ty.element_type
                elif isinstance(ty, (NamedType, )) and ty.name.upper() in {'STRING', 'LSTRING'}:
                    ty = NamedType('CHAR', None)
                else:
                    return None
            elif sel.kind == 'DEREF':
                if isinstance(ty, PointerType):
                    ty = ty.base
                else:
                    return None
            elif sel.kind == 'FIELD':
                if isinstance(ty, RecordType):
                    field = str(sel.index_or_field).upper()
                    found = None
                    for names, fty in ty.fields:
                        if any(n.upper() == field for n in names):
                            found = fty
                            break
                    if found is None:
                        return None
                    ty = found
                else:
                    return None
            else:
                return None
        return ty

    def resolve_designator_ptr(self, designator: Designator) -> ir.Value:
        """Resolve a designator to its LLVM pointer (handles arrays/selectors)."""
        ptr, _ = self.resolve_designator_ptr_typed(designator)
        return ptr

    def _emit_designator_gep(self, ptr: ir.Value, indices, *, proven_inbounds: bool) -> ir.Value:
        """Emit one designator GEP under the central ``inbounds`` policy.

        ``inbounds`` is an LLVM object-provenance promise, not merely a hint
        that an INDEXCK comparison was generated somewhere nearby.  Callers
        may set it only while the selector chain remains rooted in a known
        typed aggregate and this particular index has a compile-time proof.
        Pointer dereferences, raw/retyped storage, and dynamic indexes pass
        ``False``.  Keeping the policy at this chokepoint prevents a future
        selector path from accidentally turning an ordinary Pascal access into
        undefined LLVM IR.
        """
        return self.builder.gep(ptr, indices, inbounds=proven_inbounds)

    def resolve_designator_ptr_typed(self, designator: Designator):
        """Resolve a designator to ``(llvm_pointer, resolved_ast_type)``.

        Same walk as :meth:`resolve_designator_ptr`, but also returns the AST
        type the pointer ultimately designates. WITH needs the type so it can
        enumerate a record target's fields; the public single-value method is
        kept as a thin wrapper for all existing call sites.
        """
        symbol = self.scope.lookup(designator.name)
        if not symbol:
            symbol = self.scope.lookup(designator.name.upper())
            if not symbol:
                raise CodegenError(f'Undefined variable: {designator.name}')

        ptr = symbol.llvm_value
        cur_type = symbol.type_expr
        base_is_parameter = symbol.is_parameter
        # Tracks whether this selector chain remains rooted in a typed aggregate
        # object.  It is deliberately cleared by pointer dereference: a typed
        # pointer value can come from unchecked/raw storage, so its later GEPs
        # must not make LLVM's stronger `inbounds` promise.
        inbounds_base = True
        # Set by a DEREF through a plain ^SUPER ARRAY pointer; consumed by the
        # next INDEX selector for a dynamic upper-bound check.
        super_heap_data_ptr = None

        if designator.selectors:
            for selector in designator.selectors:
                if selector.kind == 'INDEX':
                    index = self.codegen_expr(selector.index_or_field)
                    resolved_cur = self.resolve_type_alias(cur_type)
                    if (super_heap_data_ptr is not None and isinstance(resolved_cur, ArrayType) and getattr(resolved_cur, 'super', False) and isinstance(index.type, ir.IntType)
                            and self.check_enabled('INDEXCK')):
                        # $INDEXCK for a heap super array: the lower bound is
                        # declared and static; the upper bound is the i64 the
                        # long-form NEW wrote just before the element data.
                        low_c = self.eval_const_expr(resolved_cur.index_range.low)
                        hdr = self.builder.bitcast(self.builder.bitcast(super_heap_data_ptr, ir.IntType(8).as_pointer()), ir.IntType(64).as_pointer())
                        hdr = self.builder.gep(hdr, [ir.Constant(ir.IntType(64), -1)])
                        bound64 = self.builder.load(hdr)
                        idx64 = self.builder.sext(index, ir.IntType(64)) if index.type.width < 64 else index
                        ge = self.builder.icmp_signed('>=', idx64, ir.Constant(ir.IntType(64), low_c))
                        le = self.builder.icmp_signed('<=', idx64, bound64)
                        self._emit_runtime_check(self.builder.and_(ge, le), 'indexck')
                    super_heap_data_ptr = None
                    # $INDEXCK (manual: default +, "bounds checking is
                    # separate from other subrange checking"): emit
                    # low <= idx <= high against the declared bounds.
                    # Constant indices provably in range skip the check;
                    # checks are emitted only when both bounds are known.
                    low_b, high_b = self._array_bounds_or_none(cur_type)
                    const_idx = None
                    if low_b is not None and high_b is not None:
                        try:
                            const_idx = self.eval_const_expr(selector.index_or_field)
                        except Exception:
                            pass
                    index_is_proven_inbounds = (isinstance(const_idx, int) and low_b <= const_idx <= high_b)
                    if (low_b is not None and high_b is not None and isinstance(index.type, ir.IntType) and self.check_enabled('INDEXCK')):
                        if not index_is_proven_inbounds:
                            ge = self.builder.icmp_signed('>=', index, ir.Constant(index.type, low_b))
                            le = self.builder.icmp_signed('<=', index, ir.Constant(index.type, high_b))
                            self._emit_runtime_check(self.builder.and_(ge, le), 'indexck')
                    # Pascal array indices are relative to the declared lower
                    # bound, but storage is allocated as [high-low+1 x elem]
                    # (0-based). Translate the index to a 0-based slot so that
                    # e.g. ARRAY[5..7] indexed by 5 lands on slot 0, not slot 5
                    # (which would read/write outside the allocation).
                    low, elem_type = self.array_lower_bound(cur_type)
                    if low is None:
                        # STRING/LSTRING character indexing: STRING(n) is
                        # 1-based over [n x i8] (slot = index - 1); LSTRING(n)
                        # is 0-based over [n+1 x i8] with the length byte at
                        # logical index 0 (slot = index).  Element type: CHAR.
                        _str_t = self.resolve_type_alias(cur_type)
                        _is_string = (isinstance(_str_t, (ResolvedStringType, )) or (isinstance(_str_t, NamedType) and _str_t.name.upper() == 'STRING'))
                        _is_lstring = (isinstance(_str_t, (ResolvedLStringType, LStringType)) or (isinstance(_str_t, NamedType) and _str_t.name.upper() == 'LSTRING'))
                        if _is_string or _is_lstring:
                            low = 1 if _is_string else 0
                            elem_type = NamedType('CHAR', None)
                    if low is not None and low != 0 and isinstance(index.type, ir.IntType):
                        index = self.builder.sub(index, ir.Constant(index.type, low))
                    # GEP requires [0, index] for pointers to arrays, or [index] for flat pointers
                    # Only a compile-time in-range index on a genuine typed
                    # array object is safe to mark inbounds.  Runtime checks
                    # may be disabled (and are suppressed in device code), so
                    # a dynamic index must remain a plain GEP even when an
                    # INDEXCK guard happened to be emitted on another path.
                    use_inbounds = inbounds_base and index_is_proven_inbounds
                    if isinstance(ptr.type.pointee, ir.ArrayType):
                        ptr = self._emit_designator_gep(ptr, [ir.Constant(ir.IntType(32), 0), index], proven_inbounds=use_inbounds)
                    else:
                        ptr = self._emit_designator_gep(ptr, [index], proven_inbounds=use_inbounds)
                    inbounds_base = use_inbounds
                    cur_type = elem_type
                elif selector.kind == 'FIELD':
                    base = self.resolve_type_alias(cur_type) if cur_type is not None else None
                    if isinstance(base, (ResolvedLStringType, LStringType)):
                        field = str(selector.index_or_field).upper()
                        if field == 'LEN':
                            ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
                            inbounds_base = False
                            cur_type = NamedType('CHAR', None)
                        else:
                            raise CodegenError(f"Cannot access LSTRING field '{selector.index_or_field}'")
                    elif isinstance(base, FileType):
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
                        ptr = self._emit_designator_gep(
                            ptr,
                            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), fidx)],
                            proven_inbounds=inbounds_base,
                        )
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
                        # Pointer dereference.  Pointer parameters are already
                        # pointer values; pointer variables are slots holding a
                        # pointer and must be loaded first.
                        if not base_is_parameter:
                            ptr = self.builder.load(ptr)
                        inbounds_base = False
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
                        # If we just dereferenced a plain ^SUPER ARRAY pointer,
                        # remember the data pointer: values of that pointer type
                        # come from long-form NEW, which recorded the dynamic
                        # upper bound in a header just before the data
                        # (docs/super-array-bounds-abi.md). A following INDEX
                        # selector can then bounds-check against that header.
                        pointee = self.resolve_type_alias(cur_type)
                        if (getattr(base, 'flavor', 'POINTER') == 'POINTER' and isinstance(pointee, ArrayType) and getattr(pointee, 'super', False)):
                            super_heap_data_ptr = ptr
                        else:
                            super_heap_data_ptr = None
        return ptr, cur_type

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
    # Enum support
    # ------------------------------------------------------------------

    def coerce_printf_int(self, val: ir.Value) -> ir.Value:
        """printf dynamic width/precision arguments must be C int-sized.

        These operands (field widths, precisions, string positions/counts/
        indices) are derived from signed Pascal INTEGER/INTEGER32 expressions and
        are semantically non-negative.  Sign-extend rather than zero-extend when
        widening (Finding 4): a non-negative value is identical either way, but a
        negative value stays a small negative i32 instead of becoming a huge
        positive field width.  (A WORD width >= 32768 is meaningless here, so no
        realistic case regresses.)
        """
        if isinstance(val.type, ir.IntType):
            if val.type.width < 32:
                return self.builder.sext(val, ir.IntType(32))
            if val.type.width > 32:
                return self.builder.trunc(val, ir.IntType(32))
        return val

    def retype_source_is_pointer_value(self, expr) -> Optional[bool]:
        """Classify the inner expression of a RETYPE for the pointer-vs-aggregate
        conflation between pointer and aggregate values.

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
            sym = self.scope.lookup(expr.name)
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
