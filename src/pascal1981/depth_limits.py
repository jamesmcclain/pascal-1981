"""Recursion-depth ceilings for the recursive walks over Pascal source and ASTs.

Recursive descent is unbounded by construction: a source file can nest
expressions or statements as deeply as it likes, and each level costs a real
stack frame.  Without a ceiling the only limit is the host's -- an 8MB stack in
the native stages, ``sys.getrecursionlimit()`` here -- and reaching that limit
produces either a segfault or a ``RecursionError`` whose message says nothing
about the program being compiled.

Bounding this is what the compiler being reimplemented did, and it said so.
The Aug-1981 manual's appendix A lists both

    Expression too complex/Too many internal labels.
    Try breaking up expression with intermediate value assigns.

and

    Identifier scopes nested too deeply

as documented fatal conditions.  So a ceiling is the period-correct behavior
rather than a concession to either implementation's stack.

The two recursion cycles get separate ceilings because they cost very
different amounts of stack per level.  Measured on the native stages, built
optimized:

    expression  ParseExpression -> ParseSimpleExpression -> ParseTerm ->
                ParseFactor -> ParseExpression, about 37KB per level
    statement   ParseStatement -> ParseStatement (ELSE branches, loop and
                CASE bodies), about 7.6KB per level

Worst case at these ceilings is 64*37KB + 256*7.6KB, about 4.3MB, comfortably
inside a default 8MB stack.  In this toolchain the same ceilings sit well
inside the default 1000-frame recursion limit: the parser is the deepest
recursing phase, and at the default limit it manages about 245 levels of
expression nesting and about 491 of statement nesting, so the ceiling is
reached first and it is reached with a message.

Real code is nowhere near either bound.  The deepest of the five self-hosting
sources drives the whole native pipeline in under 1MB.

These values are duplicated as ``CONST MAX_EXPR_DEPTH``/``MAX_STMT_DEPTH`` in
``pascal_src/parser.pas`` and ``pascal_src/codegen.pas`` -- the native stages
cannot import Python.  ``tests/test_depth_limits.py`` asserts the two
definitions agree, so the compilers cannot drift into accepting different
languages.
"""

from __future__ import annotations

import sys

MAX_EXPR_DEPTH = 64
MAX_STMT_DEPTH = 256

EXPR_TOO_DEEP = (f'expression too complex (nesting deeper than {MAX_EXPR_DEPTH}); '
                 'try breaking it up with intermediate value assigns')
STMT_TOO_DEEP = (f'statements nested too deeply (deeper than {MAX_STMT_DEPTH}); '
                 'try splitting the routine up')


class DepthLimitExceeded(Exception):
    """Raised to unwind a walk whose ceiling has been passed.

    Only phases whose ``error`` callable *records* a diagnostic instead of
    raising one need this: the recording kind returns normally, so without a
    raise the walk would note the problem and then carry straight on down the
    stack it was trying not to exhaust.  Such a phase catches this at its top
    level and stops; the diagnostic is already in its error list.
    """


class DepthGuard:
    """Counts one recursion cycle and raises once it passes its ceiling.

    Use as a context manager so the count unwinds on every path out of the
    guarded call, including the one where a diagnostic is raised from further
    down::

        with self._expr_depth.enter():
            ...

    ``error`` is the caller's own diagnostic-raising callable (each phase has
    one, and they raise different exception types with different position
    information), so a depth failure reads like every other error that phase
    reports rather than like an internal limit leaking out.
    """

    __slots__ = ('limit', 'message', 'error', 'depth')

    def __init__(self, limit: int, message: str, error) -> None:
        self.limit = limit
        self.message = message
        self.error = error
        self.depth = 0

    def enter(self, node=None) -> '_DepthGuardScope':
        """Guard one level.  ``node`` is passed to ``error`` for position
        information by phases whose diagnostics carry a location."""
        return _DepthGuardScope(self, node)

    def reset(self) -> None:
        self.depth = 0


class _DepthGuardScope:
    __slots__ = ('guard', 'node')

    def __init__(self, guard: DepthGuard, node=None) -> None:
        self.guard = guard
        self.node = node

    def __enter__(self) -> None:
        guard = self.guard
        guard.depth += 1
        if guard.depth > guard.limit:
            # Unwind before reporting: the error path may be caught by a
            # caller that goes on to parse or lower something else, and it
            # should not inherit a count this call never got to decrement.
            guard.depth = 0
            if self.node is None:
                guard.error(guard.message)
            else:
                guard.error(guard.message, self.node)
            # An ``error`` that records rather than raises returns here, and
            # returning would resume the very descent this guard exists to
            # stop.
            raise DepthLimitExceeded(guard.message)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.guard.depth > 0:
            self.guard.depth -= 1


def recursion_error_message() -> str:
    """Diagnostic for a RecursionError that got past the ceilings above.

    The ceilings bound the compiler's own recursive walks, but the JSON AST
    transport that the split-process CLIs use (``json.loads`` and
    ``serialization._from_serializable``) recurses over the same tree before
    any of those walks begin, and it is bounded only by
    ``sys.getrecursionlimit()``.  A hand-built AST nested past that limit
    therefore fails in transport, in a phase with no construct to name.

    That is fail-loud rather than a crash, but the stock message
    ("maximum recursion depth exceeded") says nothing about the input, so
    replace it with one that says which limit was hit and which it was not.
    """
    return (f'input nests deeper than this toolchain can walk (reached the '
            f'{sys.getrecursionlimit()}-frame recursion limit while reading the '
            f'AST). The expression and statement ceilings report the offending '
            f'construct by name; reaching this limit instead means the depth is '
            f'in the JSON AST transport, which they do not cover.')
