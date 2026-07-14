"""
Compatibility shim for codegen_llvm.py.

The original codegen_llvm.py has been refactored into a package.
This module preserves backward compatibility by re-exporting from codegen.
"""

from .codegen import (Codegen, CodegenError, LoopContext, Scope, Symbol, compile_to_llvm)

__all__ = ['Codegen', 'CodegenError', 'Symbol', 'LoopContext', 'Scope', 'compile_to_llvm']
