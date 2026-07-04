"""System V AMD64 C ABI classifier and call/declaration marshalling.

Phase 2 of the C-FFI plan (docs/c-abi-foreign-functions.md).  This is the
per-target seam that makes by-value aggregates cross the C ABI correctly: it
reproduces clang's System V AMD64 lowering (eightbyte INTEGER/SSE/MEMORY
classification, register coercion, ``byval``/``sret``) so a Pascal ``[C]``
foreign routine that passes or returns a struct by value links and runs against
an unmodified clang-compiled callee.

Only the *rules* in `classify_aggregate` are System V specific.  The decl/call
marshalling and the call-plan vocabulary are ABI-neutral, so a future Microsoft
x64 / AArch64 implementation swaps the classifier and reuses the rest.  The
active ABI is selected from the host triple in `c_abi_for_triple`; an
unimplemented triple raises rather than mislowering.

LLVM facts this relies on (verified against llvmlite 0.47): typed pointers render
``byval``/``sret`` with their pointee type automatically, and `IRBuilder.call`
accepts call-site argument attributes via ``arg_attrs={index: ArgumentAttributes}``.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import llvmlite.ir as ir

from .base import CodegenError

# Eightbyte classes (System V AMD64 ABI 3.2.3).
_NONE = 0
_INTEGER = 1
_SSE = 2
_MEMORY = 3


def _is_sysv_amd64(triple: str) -> bool:
    t = (triple or '').lower()
    return t.startswith('x86_64') or t.startswith('amd64')


# ---------------------------------------------------------------------------
# Layout: sizes, alignments, and a flat (offset, scalar-leaf) walk.  These match
# the natural (non-packed) layout LLVM/clang use for the scalar, array, struct,
# and pointer types this compiler emits, which is also how Pascal records are
# laid out (one LLVM struct element per field, declaration order).
# ---------------------------------------------------------------------------


def _align_of(t: ir.Type) -> int:
    if isinstance(t, ir.IntType):
        return max(1, (t.width + 7) // 8)
    if isinstance(t, ir.HalfType):
        return 2
    if isinstance(t, ir.FloatType):
        return 4
    if isinstance(t, ir.DoubleType):
        return 8
    if isinstance(t, ir.PointerType):
        return 8
    if isinstance(t, ir.ArrayType):
        return _align_of(t.element)
    if isinstance(t, (ir.LiteralStructType, ir.IdentifiedStructType)):
        return max((_align_of(e) for e in t.elements), default=1)
    raise CodegenError(f"C ABI: cannot align type {t}")


def _size_of(t: ir.Type) -> int:
    if isinstance(t, ir.IntType):
        return (t.width + 7) // 8
    if isinstance(t, ir.HalfType):
        return 2
    if isinstance(t, ir.FloatType):
        return 4
    if isinstance(t, ir.DoubleType):
        return 8
    if isinstance(t, ir.PointerType):
        return 8
    if isinstance(t, ir.ArrayType):
        stride = _round_up(_size_of(t.element), _align_of(t.element))
        return stride * t.count
    if isinstance(t, (ir.LiteralStructType, ir.IdentifiedStructType)):
        off = 0
        for e in t.elements:
            ea = _align_of(e)
            off = _round_up(off, ea) + _size_of(e)
        return _round_up(off, _align_of(t))
    raise CodegenError(f"C ABI: cannot size type {t}")


def _round_up(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def _leaves(t: ir.Type, base: int):
    """Yield (offset, scalar_leaf_type) for every scalar field, recursively."""
    if isinstance(t, (ir.IntType, ir.HalfType, ir.FloatType, ir.DoubleType, ir.PointerType)):
        yield base, t
        return
    if isinstance(t, ir.ArrayType):
        stride = _round_up(_size_of(t.element), _align_of(t.element))
        for i in range(t.count):
            yield from _leaves(t.element, base + i * stride)
        return
    if isinstance(t, (ir.LiteralStructType, ir.IdentifiedStructType)):
        off = 0
        for e in t.elements:
            off = _round_up(off, _align_of(e))
            yield from _leaves(e, base + off)
            off += _size_of(e)
        return
    raise CodegenError(f"C ABI: cannot walk type {t}")


# ---------------------------------------------------------------------------
# Lowering plan vocabulary (ABI-neutral).
# ---------------------------------------------------------------------------


@dataclass
class AggLowering:
    """How one by-value aggregate (parameter or return) crosses the ABI."""
    kind: str  # 'coerced' | 'memory'
    agg_type: ir.Type  # the aggregate's LLVM type
    size: int
    align: int
    pieces: List[ir.Type] = field(default_factory=list)  # coerced register types

    def coerced_struct(self) -> ir.LiteralStructType:
        return ir.LiteralStructType(list(self.pieces))


@dataclass
class CParamPlan:
    kind: str  # 'ref' | 'scalar' | 'coerced' | 'memory'
    llvm_type: Optional[ir.Type] = None  # ref/scalar: the single LLVM arg type
    agg: Optional[AggLowering] = None  # coerced/memory
    sign_attr: Optional[str] = None  # 'signext' | 'zeroext' | None (Phase 4)


@dataclass
class CCallPlan:
    params: List[CParamPlan]
    ret_kind: str  # 'void' | 'scalar' | 'coerced' | 'memory'
    ret_llvm: Optional[ir.Type] = None  # scalar: the LLVM return type
    ret_agg: Optional[AggLowering] = None  # coerced/memory
    is_variadic: bool = False  # True when the declaration has [VARARGS]
    ret_sign_attr: Optional[str] = None  # 'signext' | 'zeroext' | None (Phase 4)


# ---------------------------------------------------------------------------
# System V AMD64 classification.
# ---------------------------------------------------------------------------


class SysVAmd64Abi:
    name = "System V AMD64"

    def is_aggregate(self, t: ir.Type) -> bool:
        return isinstance(t, (ir.ArrayType, ir.LiteralStructType, ir.IdentifiedStructType))

    def classify_aggregate(self, t: ir.Type) -> AggLowering:
        size = _size_of(t)
        align = _align_of(t)
        if size == 0 or size > 16:
            return AggLowering('memory', t, size, align)

        n_eb = (size + 7) // 8
        cls = [_NONE] * n_eb
        end = [0] * n_eb  # last occupied byte offset within each eightbyte
        sse_has_double = [False] * n_eb
        sse_end = [0] * n_eb
        for off, leaf in _leaves(t, 0):
            lsz = _size_of(leaf)
            eb = off // 8
            if eb >= n_eb:
                continue
            leaf_cls = _SSE if isinstance(leaf, (ir.HalfType, ir.FloatType, ir.DoubleType)) else _INTEGER
            cls[eb] = _merge(cls[eb], leaf_cls)
            end[eb] = max(end[eb], min((eb + 1) * 8, off + lsz))
            if leaf_cls == _SSE:
                sse_end[eb] = max(sse_end[eb], min((eb + 1) * 8, off + lsz))
                if isinstance(leaf, ir.DoubleType):
                    sse_has_double[eb] = True

        pieces: List[ir.Type] = []
        for eb in range(n_eb):
            start = eb * 8
            used = end[eb] - start
            if cls[eb] == _MEMORY:
                return AggLowering('memory', t, size, align)
            if cls[eb] == _SSE:
                if sse_has_double[eb]:
                    pieces.append(ir.DoubleType())
                elif (sse_end[eb] - start) > 4:
                    pieces.append(ir.VectorType(ir.FloatType(), 2))
                else:
                    pieces.append(ir.FloatType())
            else:  # INTEGER (also the merge result of INTEGER+SSE in one eightbyte)
                pieces.append(ir.IntType(max(8, used * 8)))
        return AggLowering('coerced', t, size, align, pieces)


def _merge(a: int, b: int) -> int:
    if a == b:
        return a
    if a == _NONE:
        return b
    if b == _NONE:
        return a
    if a == _MEMORY or b == _MEMORY:
        return _MEMORY
    if a == _INTEGER or b == _INTEGER:
        return _INTEGER
    return _SSE


def c_abi_for_triple(triple: str) -> SysVAmd64Abi:
    if _is_sysv_amd64(triple):
        return SysVAmd64Abi()
    raise CodegenError(f"C-ABI by-value aggregates are only implemented for System V AMD64; "
                       f"the target triple '{triple}' has no aggregate classifier yet. Pass "
                       f"aggregates by CONST/VAR, or add a classifier for this target.")


# ---------------------------------------------------------------------------
# Codegen mixin: build [C] declarations and marshal [C] calls.
# ---------------------------------------------------------------------------


class CAbiMixin:
    """Lower foreign ``[C]`` routine declarations and calls per the host C ABI."""

    @staticmethod
    def is_c_abi_foreign(decl) -> bool:
        """True for an EXTERN/EXTERNAL routine carrying the [C] / [CDECL] marker."""
        attrs = {a.name.upper() for a in getattr(decl, 'attributes', [])}
        if 'C' not in attrs:
            return False
        directive = (getattr(decl, 'directive', None) or '').upper()
        return directive in {'EXTERN', 'EXTERNAL'} or bool(attrs & {'EXTERN', 'EXTERNAL'})

    def _c_abi(self):
        return c_abi_for_triple(getattr(self, 'host_triple', 'x86_64-pc-linux-gnu'))

    def build_c_abi_plan(self, decl, flat_param_types, flat_modes, return_llvm, is_variadic=False, flat_sign_attrs=None, ret_sign_attr=None):
        """Compute the coerced LLVM signature and the per-call marshalling plan.

        ``flat_param_types`` / ``flat_modes`` are already flattened per parameter
        name (the same lists the normal path builds).  ``return_llvm`` is None for
        procedures.  Returns ``(ir_arg_types, ir_return_type, sret, arg_attrs, plan)``
        where ``arg_attrs`` maps LLVM-arg index -> ArgumentAttributes for the
        declaration's byval/sret/signext/zeroext.

        ``flat_sign_attrs`` (Phase 4): optional list parallel to ``flat_param_types``;
        each entry is ``'signext'``, ``'zeroext'``, or ``None``.  When non-None, the
        attribute is added to both the declaration's arg attrs and the call-site plan.
        ``ret_sign_attr`` (Phase 4): ``'signext'``, ``'zeroext'``, or ``None`` for the
        scalar return type; callers apply it to the function's return-value attrs.
        """
        abi = self._c_abi()
        ir_args: List[ir.Type] = []
        arg_attrs = {}
        params: List[CParamPlan] = []

        # Return classification first (an sret return prepends a hidden pointer).
        ret_kind, ir_ret, ret_agg = 'void', ir.VoidType(), None
        if return_llvm is not None:
            if abi.is_aggregate(return_llvm):
                ret_agg = abi.classify_aggregate(return_llvm)
                if ret_agg.kind == 'memory':
                    ret_kind, ir_ret = 'memory', ir.VoidType()
                    arg_attrs[len(ir_args)] = (('sret', 'noalias'), ret_agg.align)
                    ir_args.append(ir.PointerType(ret_agg.agg_type))
                else:
                    ret_kind = 'coerced'
                    pcs = ret_agg.pieces
                    ir_ret = pcs[0] if len(pcs) == 1 else ret_agg.coerced_struct()
            else:
                ret_kind, ir_ret = 'scalar', return_llvm

        for i, (llvm_t, mode) in enumerate(zip(flat_param_types, flat_modes)):
            sattr = flat_sign_attrs[i] if flat_sign_attrs and i < len(flat_sign_attrs) else None
            by_ref = mode in {'VAR', 'VARS', 'CONST', 'CONSTS'}
            if by_ref or not abi.is_aggregate(llvm_t):
                kind = 'ref' if by_ref else 'scalar'
                params.append(CParamPlan(kind, llvm_type=llvm_t, sign_attr=sattr))
                # Attach signext/zeroext to the declaration arg attrs for sub-32-bit
                # scalars (not references -- references are pointers, no extension).
                if sattr and not by_ref and isinstance(llvm_t, ir.IntType) and llvm_t.width < 32:
                    arg_attrs[len(ir_args)] = ((sattr, ), None)
                ir_args.append(llvm_t)
                continue
            agg = abi.classify_aggregate(llvm_t)
            if agg.kind == 'memory':
                params.append(CParamPlan('memory', agg=agg))
                arg_attrs[len(ir_args)] = (('byval', ), agg.align)
                ir_args.append(ir.PointerType(agg.agg_type))
            else:
                params.append(CParamPlan('coerced', agg=agg))
                ir_args.extend(agg.pieces)

        plan = CCallPlan(params,
                         ret_kind,
                         ret_llvm=(ir_ret if ret_kind == 'scalar' else None),
                         ret_agg=ret_agg,
                         is_variadic=is_variadic,
                         ret_sign_attr=ret_sign_attr if ret_kind == 'scalar' else None)
        return ir_args, ir_ret, (ret_kind == 'memory'), arg_attrs, plan

    # -- call-site marshalling ------------------------------------------------

    def _c_abi_arg_ptr(self, arg_expr, agg_type):
        """Return a pointer (to ``agg_type``) holding the aggregate argument.

        Uses the designator's own storage when possible; otherwise spills the
        computed value to a fresh slot.
        """
        from ..ast_nodes import Designator, Identifier
        ptr = None
        if isinstance(arg_expr, Identifier):
            ptr = self.resolve_designator_ptr(Designator(arg_expr.name, []))
        elif isinstance(arg_expr, Designator):
            ptr = self.resolve_designator_ptr(arg_expr)
        if ptr is None:
            val = self.codegen_expr(arg_expr)
            ptr = self.builder.alloca(val.type)
            self.builder.store(val, ptr)
        return self.builder.bitcast(ptr, ir.PointerType(agg_type))

    def _i8p(self, ptr):
        return self.builder.bitcast(ptr, ir.PointerType(ir.IntType(8)))

    def _c_abi_memcpy(self, dst_ptr, src_ptr, nbytes):
        self.builder.call(self.memcpy_func(), [self._i8p(dst_ptr), self._i8p(src_ptr), ir.Constant(ir.IntType(64), nbytes)])

    def _c_abi_variadic_promote(self, v: ir.Value, src_expr=None) -> ir.Value:
        """Apply C default argument promotions to one variadic-tail value.

        Rules (C11 6.5.2.2 p7):
        - ``float`` -> ``double`` (fpext).
        - Integer types narrower than ``int`` (i1, i8, i16) -> ``i32``.  The
          extension follows the C default-promotion of the *source* type: an
          unsigned type whose values all fit in ``int`` zero-extends, a signed
          type sign-extends.  At the IR level i8/i16 alone cannot tell WORD from
          INTEGER, so we consult the Pascal source expression (Finding 2): a WORD
          (unsigned) zero-extends -- the previous unconditional ``sext`` turned a
          WORD like 60000 into -5536 in the variadic tail -- while signed
          INTEGER/CHAR sign-extend.  i1 (BOOLEAN) is always zero-extended.
        - i32, i64, double, pointer: passed as-is.
        """
        t = v.type
        if isinstance(t, ir.FloatType):
            return self.builder.fpext(v, ir.DoubleType())
        if isinstance(t, ir.IntType):
            if t.width == 1:
                return self.builder.zext(v, ir.IntType(32))
            if t.width < 32:
                if src_expr is not None and self._expr_is_unsigned_word(src_expr):
                    return self.builder.zext(v, ir.IntType(32))
                return self.builder.sext(v, ir.IntType(32))
        return v

    def codegen_c_abi_call(self, fn, plan: CCallPlan, arg_exprs, modes):
        """Emit an ABI-correct call to a foreign ``[C]`` routine.

        Returns the call's result value (an aggregate value for coerced/sret
        returns, a scalar for scalar returns, or the raw call for void).
        """
        i32 = ir.IntType(32)
        call_args = []
        arg_attrs = {}
        sret_slot = None

        if plan.ret_kind == 'memory':
            sret_slot = self.builder.alloca(plan.ret_agg.agg_type)
            aa = ir.ArgumentAttributes()
            aa.add('sret')
            aa.add('noalias')
            aa.align = plan.ret_agg.align
            arg_attrs[len(call_args)] = aa
            call_args.append(sret_slot)

        for i, pp in enumerate(plan.params):
            expr = arg_exprs[i]
            if pp.kind in ('ref', 'scalar'):
                v = self.codegen_actual_arg(expr, modes[i])
                v = self.coerce_arg(v, pp.llvm_type, src_expr=expr)
                # Phase 4: attach signext/zeroext to sub-32-bit scalar call-site args.
                if pp.sign_attr and isinstance(pp.llvm_type, ir.IntType) and pp.llvm_type.width < 32:
                    aa = ir.ArgumentAttributes()
                    aa.add(pp.sign_attr)
                    arg_attrs[len(call_args)] = aa
                call_args.append(v)
            elif pp.kind == 'memory':
                src = self._c_abi_arg_ptr(expr, pp.agg.agg_type)
                tmp = self.builder.alloca(pp.agg.agg_type)
                self._c_abi_memcpy(tmp, src, pp.agg.size)
                aa = ir.ArgumentAttributes()
                aa.add('byval')
                aa.align = pp.agg.align
                arg_attrs[len(call_args)] = aa
                call_args.append(tmp)
            else:  # coerced
                src = self._c_abi_arg_ptr(expr, pp.agg.agg_type)
                csrc = self.builder.bitcast(src, ir.PointerType(pp.agg.coerced_struct()))
                for pi in range(len(pp.agg.pieces)):
                    gep = self.builder.gep(csrc, [i32(0), i32(pi)], inbounds=True)
                    ld = self.builder.load(gep)
                    ld.align = pp.agg.align
                    call_args.append(ld)

        # Variadic tail: arguments beyond the fixed parameters.
        # Apply C default argument promotions: float->double, i1/i8/i16->i32.
        if plan.is_variadic:
            for expr in arg_exprs[len(plan.params):]:
                v = self.codegen_expr(expr)
                v = self._c_abi_variadic_promote(v, src_expr=expr)
                call_args.append(v)

        call = self.builder.call(fn, call_args, arg_attrs=(arg_attrs or None))

        if plan.ret_kind == 'void':
            return call
        if plan.ret_kind == 'scalar':
            return call
        if plan.ret_kind == 'memory':
            return self.builder.load(sret_slot)
        # coerced return: store the register value(s) back through the aggregate's
        # own storage, then load the aggregate.
        agg = plan.ret_agg
        slot = self.builder.alloca(agg.agg_type)
        ret_ll = agg.pieces[0] if len(agg.pieces) == 1 else agg.coerced_struct()
        typed = self.builder.bitcast(slot, ir.PointerType(ret_ll))
        self.builder.store(call, typed)
        return self.builder.load(slot)
