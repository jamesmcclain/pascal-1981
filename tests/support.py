"""
Capability detection and in-process test helpers.

This module is the single source of truth for:
  • Capability probes (llvmlite, clang)
  • Skip decorators
  • In-process helpers (parse, type-check, IR generation, build & run)
"""

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

# Capability probes
HAS_LLVMLITE = importlib.util.find_spec("llvmlite") is not None
HAS_CLANG = shutil.which("clang") is not None

HAS_LLVM = HAS_LLVMLITE
CAN_BUILD_EXE = HAS_LLVMLITE and HAS_CLANG

# Skip decorators
requires_llvm = unittest.skipUnless(
    HAS_LLVM, "requires llvmlite (IR generation)")
requires_exe = unittest.skipUnless(
    CAN_BUILD_EXE, "requires llvmlite + clang (native build/run)")

# In-process helpers
from lexer import lex_file, LexerError
from parser import parse_file, ParserError
from type_checker import PascalTypeChecker, TypeCheckResult, TypeCheckError


def _write_temp(src: str) -> str:
    """Write a source string to a temp file, return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".pas", delete=False)
    f.write(src)
    f.close()
    return f.name


def parse_source(src: str):
    """
    Parse a source string in-process.
    Raises LexerError or ParserError on invalid input.
    Returns the AST on success.
    """
    path = _write_temp(src)
    try:
        return parse_file(path)
    finally:
        os.unlink(path)


def typecheck_source(src: str):
    """
    Parse and type-check a source string in-process.
    Returns a TypeCheckResult with .success (bool) and .errors (list).
    No llvmlite involved.
    """
    path = _write_temp(src)
    try:
        ast = parse_file(path)
        checker = PascalTypeChecker(source_file=path)
        return checker.check(ast)
    finally:
        os.unlink(path)


def typecheck_module(iface_code: str = None, impl_code: str = None, prog_code: str = None, module_name: str = 'TEST'):
    """
    Type-check a module with optional interface and implementation files.
    
    Args:
        iface_code: Optional interface file content (written under a literal name)
        impl_code: Optional implementation (.pas) file content  
        prog_code: Optional program (.pas) file content (if checking a standalone program)
        module_name: Module name (default 'TEST')
    
    Returns: TypeCheckResult with .success (bool) and .errors (list).
    No llvmlite involved.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        # Materialize the interface under a literal, extensionless basename so the
        # type checker's strict (no-extension-inference) resolution can find it.
        if iface_code:
            iface_path = os.path.join(tmpdir, module_name.lower())
            with open(iface_path, 'w') as f:
                f.write(iface_code)
        
        # Determine what to type-check
        if impl_code:
            file_to_check = os.path.join(tmpdir, f"{module_name.lower()}.pas")
            with open(file_to_check, 'w') as f:
                f.write(impl_code)
        elif prog_code:
            file_to_check = os.path.join(tmpdir, "prog.pas")
            with open(file_to_check, 'w') as f:
                f.write(prog_code)
        else:
            # No file to check
            return TypeCheckResult(False, [TypeCheckError("No code provided")])
        
        # Parse and type-check
        ast = parse_file(file_to_check)
        checker = PascalTypeChecker(source_file=file_to_check)
        return checker.check(ast)
    finally:
        shutil.rmtree(tmpdir)


# Codegen helpers are defined in test_codegen.py to keep llvmlite imports isolated
