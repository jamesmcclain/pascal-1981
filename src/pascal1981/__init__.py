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
    """Return the absolute filesystem path to ``libpascalrt.a``.

    In an installed wheel this points to the bundled package-data archive.  In a
    source checkout before installation, it falls back to
    ``runtime/build/libpascalrt.a`` when that archive has been built with
    ``make -C runtime``.
    """
    import importlib.resources
    from pathlib import Path

    try:
        ref = importlib.resources.files(__package__) / 'libpascalrt.a'
        if ref.is_file():
            return str(ref)
    except Exception:
        try:
            with importlib.resources.path(__package__, 'libpascalrt.a') as p:
                if p.is_file():
                    return str(p)
        except Exception:
            pass

    checkout_archive = Path(__file__).resolve().parents[2] / 'runtime' / 'build' / 'libpascalrt.a'
    return str(checkout_archive)
