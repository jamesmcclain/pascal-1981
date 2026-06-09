"""
FILES mixin for Codegen.

File I/O operations

Part of Plan 1 refactoring (mixin-based architecture).
"""

from __future__ import annotations

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder
from typing import Optional, List, Union, Any, Tuple

from ast_nodes import *


class FilesMixin:
    """Mixin for files functionality."""

    def file_fcb_type(self) -> ir.LiteralStructType:
        """The file-control-block layout shared by every file variable.

        Fields: element size, structure (0 = binary FILE OF T, 1 = ASCII/TEXT),
        a touched flag, and a pointer to the current-component buffer.
        """
        if not hasattr(self, '_fcb_ty'):
            i32 = ir.IntType(32)
            self._fcb_ty = ir.LiteralStructType([i32, i32, i32, ir.IntType(8).as_pointer()])
        return self._fcb_ty

    def _register_file_helpers(self) -> None:
        """Register file helper functions (pas_file_buffer, pas_file_touch_buffer).
        
        This is called after __init__ to avoid initialization order issues with mixins.
        """
        import llvmlite.ir as ir
        from llvmlite.ir import IRBuilder
        
        fcb_ty = self.file_fcb_type()
        fcb_ptr = fcb_ty.as_pointer()
        file_buffer_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [fcb_ptr])
        file_touch_ty = ir.FunctionType(ir.VoidType(), [fcb_ptr])
        i32 = ir.IntType(32)
        file_buffer = ir.Function(self.module, file_buffer_ty, name='pas_file_buffer')
        b = IRBuilder(file_buffer.append_basic_block(name='entry'))
        buf_field = b.gep(file_buffer.args[0], [ir.Constant(i32, 0), ir.Constant(i32, 3)])
        b.ret(b.load(buf_field))
        self.scope.define('pas_file_buffer', file_buffer, None)
        file_touch = ir.Function(self.module, file_touch_ty, name='pas_file_touch_buffer')
        b = IRBuilder(file_touch.append_basic_block(name='entry'))
        touched_field = b.gep(file_touch.args[0], [ir.Constant(i32, 0), ir.Constant(i32, 2)])
        b.store(ir.Constant(i32, 1), touched_field)
        b.ret_void()
        self.scope.define('pas_file_touch_buffer', file_touch, None)


    def _init_file_storage(self, slot: ir.Value, type_expr: Type) -> None:
        elem_size, structure = self._file_element_size_and_structure(type_expr)
        i32 = ir.IntType(32)
        zero = ir.Constant(i32, 0)
        fcb_ty = self.file_fcb_type()
        # Allocate the control block and its component buffer inline at the file
        # variable's storage site. The element size is a compile-time constant,
        # so no runtime malloc is needed (the previous model malloc'd per file
        # and never freed). For locals this lives in the function frame; for
        # program-level/predeclared files it lives in main's frame, i.e. for the
        # whole program. Either way it is reclaimed automatically.
        fcb = self.builder.alloca(fcb_ty, name='file_fcb')
        buf = self.builder.alloca(ir.ArrayType(ir.IntType(8), max(1, elem_size)), name='file_buf')
        self.builder.store(ir.Constant(i32, elem_size), self.builder.gep(fcb, [zero, ir.Constant(i32, 0)]))
        self.builder.store(ir.Constant(i32, structure), self.builder.gep(fcb, [zero, ir.Constant(i32, 1)]))
        self.builder.store(ir.Constant(i32, 0), self.builder.gep(fcb, [zero, ir.Constant(i32, 2)]))
        buf_i8 = self.builder.bitcast(buf, ir.IntType(8).as_pointer())
        self.builder.store(buf_i8, self.builder.gep(fcb, [zero, ir.Constant(i32, 3)]))
        # The handle handed to the rest of codegen is an opaque i8* to the FCB.
        self.builder.store(self.builder.bitcast(fcb, ir.IntType(8).as_pointer()), slot)


    def _file_buffer_ptr(self, file_slot: ir.Value, elem_type: Type, touch: bool) -> ir.Value:
        handle = self.builder.load(file_slot)
        fptr = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
        if touch:
            touch_fn = self.scope.lookup('pas_file_touch_buffer').llvm_value
            self.builder.call(touch_fn, [fptr])
        buf_fn = self.scope.lookup('pas_file_buffer').llvm_value
        raw = self.builder.call(buf_fn, [fptr])
        return self.builder.bitcast(raw, ir.PointerType(self.llvm_type(elem_type)))


    def _file_element_size_and_structure(self, type_expr: Type) -> tuple[int, int]:
        resolved = self.resolve_type_alias(type_expr)
        if isinstance(resolved, FileType):
            return self.get_type_size(resolved.element_type), (1 if getattr(resolved, 'structure', 'BINARY') == 'ASCII' else 0)
        if isinstance(type_expr, NamedType) and type_expr.name.upper() == 'TEXT':
            return 1, 1
        return 1, 0


