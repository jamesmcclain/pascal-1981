"""
Pascal-1981: a Pascal compiler targeting LLVM IR.

This package provides a lexer, parser, type-checker, and LLVM IR code
generator that faithfully implements the 1981 IBM Pascal 2.0 dialect,
with optional opt-in extensions.
"""

from __future__ import annotations

__all__ = [
    'compile_to_llvm',
    'Codegen',
    'CodegenError',
    'runtime_lib_path',
]


# ---------------------------------------------------------------------------
# Lazy public exports
# ---------------------------------------------------------------------------

def __getattr__(name: str):
    """Lazily expose codegen symbols without importing llvmlite on package import."""
    if name in {'compile_to_llvm', 'Codegen', 'CodegenError'}:
        from . import codegen_llvm
        return getattr(codegen_llvm, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Runtime-library discovery
# ---------------------------------------------------------------------------

def runtime_lib_path() -> str:
    """Return the absolute filesystem path to the bundled libpascalrt.a.

    The static library is compiled from ``runtime/*.c`` at package installation
    time and placed inside the package directory so that it travels with the
    wheel.
    """
    import importlib.resources

    try:
        ref = importlib.resources.files(__package__) / 'libpascalrt.a'
        return str(ref)
    except Exception:
        with importlib.resources.path(__package__, 'libpascalrt.a') as p:
            return str(p)
