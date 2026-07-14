"""Diagnostics: source-location plumbing and error/warning recording.

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""

from typing import Optional

from ..ast_nodes import ASTNode
from ..symbol_table import SourceLocation
from .result import TypeCheckError


class DiagnosticsMixin:

    def make_location(self, location) -> Optional[SourceLocation]:
        """Convert AST location tuple to SourceLocation."""
        # Handle None and missing location attributes gracefully
        if location is None:
            return None
        if isinstance(location, tuple) and len(location) >= 3:
            return SourceLocation(location[0], location[1], location[2])
        return None

    def get_node_location(self, node: Optional[ASTNode]) -> Optional[SourceLocation]:
        """Get location from a node, handling missing attributes gracefully."""
        if not node:
            return None
        if hasattr(node, 'location'):
            return self.make_location(node.location)
        return None

    def error(self, message: str, location=None) -> None:
        """Record a type checking error."""
        # Handle node objects or tuple locations
        loc = None
        if location is not None:
            if isinstance(location, ASTNode):
                loc = self.get_node_location(location)
            else:
                loc = self.make_location(location)
        self.errors.append(TypeCheckError(message=message, location=loc, severity='error'))

    def warning(self, message: str, location=None) -> None:
        """Record a type checking warning."""
        # Handle node objects or tuple locations
        loc = None
        if location is not None:
            if isinstance(location, ASTNode):
                loc = self.get_node_location(location)
            else:
                loc = self.make_location(location)
        self.warnings.append(TypeCheckError(message=message, location=loc, severity='warning'))
