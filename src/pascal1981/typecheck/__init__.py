"""Mixin package for the Pascal-1981 type checker.

PascalTypeChecker (in pascal1981.type_checker) is assembled from these
mixins, mirroring the codegen package split.
"""

from .builtin_args import BuiltinArgsMixin
from .consts import ConstFoldMixin
from .decls import DeclsMixin
from .device import DeviceCheckMixin
from .diagnostics import DiagnosticsMixin
from .exprs import ExprInferMixin
from .result import TypeChecker, TypeCheckError, TypeCheckResult
from .stmts import StmtsMixin
from .types_resolve import TypeResolveMixin
from .units import UnitsMixin

__all__ = [
    'BuiltinArgsMixin', 'ConstFoldMixin', 'DeclsMixin', 'DeviceCheckMixin',
    'DiagnosticsMixin', 'ExprInferMixin', 'StmtsMixin', 'TypeResolveMixin',
    'UnitsMixin', 'TypeCheckError', 'TypeCheckResult', 'TypeChecker',
]
