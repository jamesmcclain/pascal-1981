"""
Capability detection and in-process test helpers.

This module is the single source of truth for:
  • Capability probes (llvmlite, clang)
  • Skip decorators
  • In-process helpers (parse, type-check, IR generation, build & run)
  • Multi-file integration-test project helpers
"""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

# Capability probes
HAS_LLVMLITE = importlib.util.find_spec("llvmlite") is not None
HAS_CLANG = shutil.which("clang") is not None

HAS_LLVM = HAS_LLVMLITE
CAN_BUILD_EXE = HAS_LLVMLITE and HAS_CLANG

# Skip decorators
requires_llvm = unittest.skipUnless(HAS_LLVM, "requires llvmlite (IR generation)")
requires_exe = unittest.skipUnless(CAN_BUILD_EXE, "requires llvmlite + clang (native build/run)")

# In-process helpers
from pascal1981.lexer import LexerError, lex_file
from pascal1981.parser import ParserError, parse_file
from pascal1981.type_checker import (PascalTypeChecker, TypeCheckError, TypeCheckResult)

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime")
RUNTIME_LIB = os.path.join(RUNTIME_DIR, "build", "libpascalrt.a")


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


def typecheck_source(src: str, features=None):
    """
    Parse and type-check a source string in-process.
    Returns a TypeCheckResult with .success (bool) and .errors (list).
    No llvmlite involved.
    """
    path = _write_temp(src)
    try:
        ast = parse_file(path)
        checker = PascalTypeChecker(source_file=path, features=features)
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


@contextmanager
def temporary_pascal_project(files: dict[str, str]):
    """Materialize a temporary multi-file Pascal project on disk.

    Args:
        files: Mapping of relative path -> file content. Paths may include
            extensionless interface basenames such as ``kernel``.

    Yields:
        Project directory path.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        for relpath, content in files.items():
            path = os.path.join(tmpdir, relpath)
            os.makedirs(os.path.dirname(path) or tmpdir, exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir)


def compile_pascal_file(source_path: str, output_path: str = None, *, features=None, host_triple: str = 'x86_64-pc-linux-gnu', device_triple: str = 'x86_64-pc-linux-gnu') -> str:
    """Parse, type-check, and lower one Pascal source file to LLVM IR.

    Returns the output .ll path. Raises RuntimeError on type-check failure.
    """
    from pascal1981.codegen import compile_to_llvm

    ast = parse_file(source_path)
    result = PascalTypeChecker(source_file=source_path, features=features).check(ast)
    if not result.success:
        raise RuntimeError(f"Type check failed for {source_path}: {result.errors}")
    ir = compile_to_llvm(ast, source_file=source_path, features=features, host_triple=host_triple, device_triple=device_triple)
    if output_path is None:
        output_path = f"{source_path}.ll"
    with open(output_path, 'w') as f:
        f.write(ir)
    return output_path


def compile_pascal_project(project_dir: str,
                           compile_pairs: list[tuple[str, str]],
                           *,
                           features=None,
                           host_triple: str = 'x86_64-pc-linux-gnu',
                           device_triple: str = 'x86_64-pc-linux-gnu') -> dict[str, str]:
    """Compile multiple Pascal files in one project directory.

    Args:
        project_dir: Root directory holding the source files.
        compile_pairs: ``[(source_relpath, output_relpath), ...]``.

    Returns:
        Mapping of source_relpath -> absolute output .ll path.
    """
    outputs = {}
    for source_rel, output_rel in compile_pairs:
        source_path = os.path.join(project_dir, source_rel)
        output_path = os.path.join(project_dir, output_rel)
        outputs[source_rel] = compile_pascal_file(
            source_path,
            output_path,
            features=features,
            host_triple=host_triple,
            device_triple=device_triple,
        )
    return outputs


def link_pascal_project(project_dir: str, ir_relpaths: list[str], *, exe_name: str = 'prog', runtime_libs: list[str] = None, link_flags: list[str] = None) -> str:
    """Link one or more LLVM IR files plus the Pascal runtime into an executable."""
    runtime_libs = runtime_libs or [RUNTIME_LIB]
    link_flags = link_flags or []
    exe_path = os.path.join(project_dir, exe_name)
    sources = [os.path.join(project_dir, relpath) for relpath in ir_relpaths]
    result = subprocess.run(
        ['clang', *sources, *runtime_libs, *link_flags, '-o', exe_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"clang failed: {result.stderr}")
    return exe_path


def build_and_run_pascal_project(files: dict[str, str],
                                 compile_pairs: list[tuple[str, str]],
                                 link_ir_relpaths: list[str],
                                 *,
                                 exe_name: str = 'prog',
                                 runtime_libs: list[str] = None,
                                 link_flags: list[str] = None,
                                 run_args: list[str] = None,
                                 stdin: str = '',
                                 features=None,
                                 host_triple: str = 'x86_64-pc-linux-gnu',
                                 device_triple: str = 'x86_64-pc-linux-gnu') -> tuple[int, str, str]:
    """Full multi-file integration path: write files, compile separately, link, run."""
    run_args = run_args or []
    with temporary_pascal_project(files) as project_dir:
        compile_pascal_project(
            project_dir,
            compile_pairs,
            features=features,
            host_triple=host_triple,
            device_triple=device_triple,
        )
        exe_path = link_pascal_project(
            project_dir,
            link_ir_relpaths,
            exe_name=exe_name,
            runtime_libs=runtime_libs,
            link_flags=link_flags,
        )
        run = subprocess.run([exe_path, *run_args], input=stdin, capture_output=True, text=True)
        return run.returncode, run.stdout, run.stderr


# Codegen helpers are defined in test_codegen.py to keep llvmlite imports isolated
