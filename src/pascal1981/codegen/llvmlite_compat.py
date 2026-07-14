"""Compatibility shims for the llvmlite IR/binding surface this project uses.

This module is the single home for every place the compiler must either
(a) reach past llvmlite's public API (its attribute whitelists lag the LLVM
    attributes this compiler legitimately emits), or
(b) adapt to signature/spelling differences across the llvmlite releases
    allowed by pyproject.toml (``llvmlite>=0.47.0``).

Keeping these pokes in one file means a future llvmlite upgrade has exactly
one place to audit, instead of private-attribute (`_known`) manipulation
scattered through codegen.

Known version differences handled here:

* llvmlite 0.47 (LLVM 20) whitelists the classic ``nocapture`` parameter
  attribute; llvmlite 0.48 (LLVM 20/21 era) replaces it with the modern
  ``captures(none)`` spelling.  Both spellings round-trip through
  ``parse_assembly``/``verify`` on their respective bundled LLVMs.
* llvmlite 0.47's ``create_pipeline_tuning_options`` accepts a
  ``size_level`` keyword; llvmlite 0.48 removed it (only ``speed_level``
  remains).  This compiler only ever passed ``size_level=0`` (the default),
  so dropping the keyword when absent is behavior-preserving.
"""

from __future__ import annotations

import inspect
from types import MappingProxyType

from llvmlite import ir

__all__ = [
    'add_argument_attribute',
    'add_function_string_attribute',
    'nocapture_spelling',
    'create_pipeline_tuning_options',
]


def add_argument_attribute(arg: ir.Argument, name: str) -> None:
    """Add a parameter attribute, tolerating llvmlite's whitelist.

    llvmlite's ``ArgumentAttributes`` rejects attribute names missing from
    its ``_known`` mapping even when LLVM itself accepts them (e.g. the
    parameter-level ``readonly``, which LLVM has carried since long before
    this project's llvmlite floor).  When the name is not whitelisted, shadow
    the instance's ``_known`` mapping — the same trick
    ``add_function_string_attribute`` uses for function-level string
    attributes — so ``.add`` accepts it.  Round-trips through
    ``parse_assembly``/``verify`` are covered by test_kernel_param_attrs.py.
    """
    attrs = arg.attributes
    if name not in attrs._known:
        attrs._known = MappingProxyType({**dict(attrs._known), name: False})
    attrs.add(name)


def add_function_string_attribute(func: ir.Function, token: str) -> None:
    """Add a ``"key"="value"`` string attribute to a function.

    llvmlite's ``FunctionAttributes`` whitelists known enum attributes and
    has no string-attribute API, so the token is added by shadowing the
    instance's ``_known`` set; the token renders verbatim in the ``define``
    attribute list, which is exactly LLVM's string-attribute syntax
    (round-trip through parse_assembly/verify is covered by tests).
    """
    func.attributes._known = frozenset(func.attributes._known) | {token}
    func.attributes.add(token)


def nocapture_spelling(arg: ir.Argument) -> str:
    """The no-capture parameter attribute spelling for this llvmlite.

    Returns ``'captures(none)'`` where llvmlite whitelists the modern
    spelling (0.48+), else the classic ``'nocapture'`` (0.47).  Both carry
    the identical semantic fact to LLVM.
    """
    return 'captures(none)' if 'captures(none)' in arg.attributes._known else 'nocapture'


def create_pipeline_tuning_options(llvm_binding, *, speed_level: int):
    """Version-tolerant wrapper for ``llvm.create_pipeline_tuning_options``.

    Passes ``size_level=0`` only where the binding still accepts it
    (llvmlite 0.47); on 0.48+ the keyword no longer exists and 0 is the
    only behavior anyway.
    """
    factory = llvm_binding.create_pipeline_tuning_options
    kwargs = {'speed_level': speed_level}
    try:
        if 'size_level' in inspect.signature(factory).parameters:
            kwargs['size_level'] = 0
    except (TypeError, ValueError):  # pragma: no cover - C-callable without signature
        pass
    return factory(**kwargs)
