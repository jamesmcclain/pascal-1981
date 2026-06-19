"""
FILES mixin for Codegen.

File I/O operations

"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ..ast_nodes import *


class FilesMixin:
    """Mixin for files functionality."""

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
        self.builder.store(ir.Constant(i32, 0), self.builder.gep(fcb, [zero, ir.Constant(i32, 3)]))
        buf_i8 = self.builder.bitcast(buf, ir.IntType(8).as_pointer())
        self.builder.store(buf_i8, self.builder.gep(fcb, [zero, ir.Constant(i32, 4)]))
        self.builder.store(ir.Constant(ir.IntType(8).as_pointer(), None), self.builder.gep(fcb, [zero, ir.Constant(i32, 5)]))
        self.builder.store(ir.Constant(ir.IntType(8).as_pointer(), None), self.builder.gep(fcb, [zero, ir.Constant(i32, 6)]))
        default_mode = 1 if getattr(slot, 'name', '').lower() in {'input', 'output'} else 0
        self.builder.store(ir.Constant(i32, default_mode), self.builder.gep(fcb, [zero, ir.Constant(i32, 7)]))
        # Trapped-I/O fields: TRAP off, ERRS clear.
        self.builder.store(ir.Constant(ir.IntType(8), 0), self.builder.gep(fcb, [zero, ir.Constant(i32, 8)]))
        self.builder.store(ir.Constant(i32, 0), self.builder.gep(fcb, [zero, ir.Constant(i32, 9)]))
        # The handle handed to the rest of codegen is an opaque i8* to the FCB.
        self.builder.store(self.builder.bitcast(fcb, ir.IntType(8).as_pointer()), slot)

    def _file_buffer_ptr(self, file_slot: ir.Value, elem_type: Type, touch: bool) -> ir.Value:
        handle = self.builder.load(file_slot)
        fptr = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
        if touch:
            self.builder.call(self.runtime_extern('pas_file_touch_buffer'), [fptr])
        buf_fn = self.runtime_extern('pas_file_buffer')
        raw = self.builder.call(buf_fn, [fptr])
        return self.builder.bitcast(raw, ir.PointerType(self.llvm_type(elem_type)))

    def _file_element_size_and_structure(self, type_expr: Type) -> tuple[int, int]:
        resolved = self.resolve_type_alias(type_expr)
        if isinstance(resolved, FileType):
            return self.get_type_size(resolved.element_type), (1 if getattr(resolved, 'structure', 'BINARY') == 'ASCII' else 0)
        if isinstance(type_expr, NamedType) and type_expr.name.upper() == 'TEXT':
            return 1, 1
        return 1, 0
