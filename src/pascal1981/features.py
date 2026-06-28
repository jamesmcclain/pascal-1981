"""Compile-time extension feature registry.

The faithful 1981 dialect is the default: every registered feature defaults off.
Drivers may enable features explicitly or via an umbrella dialect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable


@dataclass(frozen=True)
class Feature:
    name: str
    default: bool
    help: str
    # Whether this feature participates in the ``extended`` umbrella.
    #
    # The ``extended`` dialect is "every umbrella feature on", and ``is_extended``
    # (the C-FFI gate) tests exactly that subset.  A feature with
    # ``in_extended=False`` is a *policy* flag: it is registered, listable, and
    # toggleable with ``-f``/``--feature``, but it is orthogonal to the dialect
    # surface -- ``--dialect extended`` does not turn it on, and disabling it does
    # not pull the program out of the extended dialect (so it can never gate the
    # C-FFI surface on or off as a side effect).
    in_extended: bool = True


_FEATURES: Dict[str, Feature] = {
    'wide-integers':
    Feature(
        name='wide-integers',
        default=False,
        help='Enable extension INTEGER32/INTEGER64 (signed) and WORD32/WORD64 (unsigned) types, the WORD16 (= WORD) and INTEGER16 (= INTEGER) synonyms, and wide integer constants.',
    ),
    'wide-reals':
    Feature(
        name='wide-reals',
        default=False,
        help=
        'Enable extension REAL32 (32-bit float) and REAL64 (a 64-bit synonym for REAL) types in host code. In DEVICE code these are always available, independent of this flag.',
    ),
    'symbolic-enum-io':
    Feature(
        name='symbolic-enum-io',
        default=False,
        help='Enable symbolic (name-based) enum WRITE and READ instead of the faithful 1981 ordinal/numeric form; BOOLEAN always writes TRUE/FALSE regardless.',
    ),
    'string-precision':
    Feature(
        name='string-precision',
        default=False,
        help='Honor the ::N precision operand on STRING/LSTRING WRITE values (truncate to N chars). The faithful 1981 default ignores ::N on strings and prints the whole value.',
    ),
    'readset-set-literal':
    Feature(
        name='readset-set-literal',
        default=False,
        help=
        "Accept an inline set-constructor literal (e.g. ['A'..'Z']) as the READSET set argument. The faithful 1981 default rejects it (Character Set Expected) and requires a declared SET OF CHAR value or a type-prefixed constructor.",
    ),
    'strict-word-int':
    Feature(
        name='strict-word-int',
        default=False,
        # Policy flag, orthogonal to the extended dialect (in_extended=False).
        # The DEFAULT (both dialects) already matches the vintage compiler: a
        # signed INTEGER variable/expression is NOT assignment compatible with
        # WORD (use WRD(...)), and mixing WORD with a non-constant INTEGER in an
        # expression produces a WARNING (the vintage compiler arbitrarily picks
        # signed or unsigned arithmetic).  This flag promotes that mix WARNING to
        # a hard ERROR -- a deliberate step *beyond* vintage strictness for code
        # bases that want every WORD/INTEGER crossing spelled out.  It does not
        # affect the assignment rule (already an error) or constants (always
        # exempt: INTEGER constants change to WORD per the manual).
        help='Promote the WORD/INTEGER expression-mix warning to a hard error (stricter than vintage). Orthogonal to --dialect; the assignment-compatibility error and the constant exemption apply regardless.',
        in_extended=False,
    ),
}

# The ``extended`` umbrella is the subset of features that define the *dialect
# surface* (wider types, symbolic enum I/O, etc.).  Policy flags such as
# ``strict-word-int`` are registered but excluded, so the C-FFI gate
# (``is_extended``) and ``--dialect extended`` stay orthogonal to them.
_EXTENDED_UMBRELLA = frozenset(name for name, feat in _FEATURES.items() if feat.in_extended)


def all_features() -> Iterable[Feature]:
    return _FEATURES.values()


def feature_names() -> list[str]:
    return sorted(_FEATURES)


def default_features() -> Dict[str, bool]:
    return {name: feature.default for name, feature in _FEATURES.items()}


def extended_features() -> Dict[str, bool]:
    # Umbrella features on; policy flags (in_extended=False) stay at their
    # registered default so the dialect surface and policy flags are orthogonal.
    return {name: (name in _EXTENDED_UMBRELLA or feat.default)
            for name, feat in _FEATURES.items()}


def is_extended(features: Dict[str, bool] | None) -> bool:
    """True iff every *umbrella* feature is enabled: the 'extended' umbrella.

    The C-FFI surface -- the ``[C]``/``[CDECL]`` attribute and the
    ``CINT``/``CLONG``/``CSIZE_T``/... fixed-width aliases -- is available only
    under this umbrella, so the wide C widths and the interface that needs them
    arrive together rather than letting ``[C]`` smuggle wide types into an
    otherwise-vintage program.  Read deliberately as "all of the umbrella
    (``_EXTENDED_UMBRELLA``) is on"; a finer-grained ``c-ffi`` gate could replace
    this later without changing callers.  Policy flags such as
    ``strict-word-int`` are intentionally *not* part of the umbrella, so toggling
    them never moves a program in or out of the extended dialect.
    """
    if not features:
        return False
    return all(features.get(name, False) for name in _EXTENDED_UMBRELLA)


# ---------------------------------------------------------------------------
# Device dialect (DEVICE compiland) feature set.
#
# Per ads-memory-spaces-design.md (S1.2, S9): a DEVICE compiland uses the
# *device dialect* =
#     extended host features - a recission set + the address-space surface.
#
# The address-space surface is NOT a feature flag -- module kind subsumes it
# (design S3.1), so it is intentionally absent here; it is registered by
# module-kind gating in the type checker, not toggled via resolve_features.
#
# The recission set is deliberately EMPTY here. The rescinded constructs
# (recursion, set I/O / dynamic set ranges, NEW/heap, host I/O, GOTO and its
# non-loop labels, flat-heap pointer-chasing, and DEVICE UNIT initializer
# blocks) are *language constructs*, not entries in _FEATURES, so they are
# enforced as device-compiland-scoped checker bans rather than feature
# toggles. Names listed here that match a real feature flag are turned off;
# the rest are owned by the checker.
_DEVICE_RECISSIONS: frozenset[str] = frozenset()  # recissions live in the checker


def device_features(host_features: Dict[str, bool] | None = None) -> Dict[str, bool]:
    """Build the device-compiland feature set from a host baseline.

    The type checker swaps this in as the active feature set on entry to a
    DEVICE compiland and restores the host set on exit, so the faithful/host
    path is unaffected.
    """
    base = dict(host_features) if host_features is not None else extended_features()
    for name in _DEVICE_RECISSIONS:
        if name in base:
            base[name] = False
    return base


def normalize_feature_name(name: str) -> str:
    return name.strip().lower()


def resolve_features(dialect: str = 'vintage', overrides: Iterable[str] = ()) -> Dict[str, bool]:
    if dialect == 'vintage':
        resolved = default_features()
    elif dialect == 'extended':
        resolved = extended_features()
    else:
        raise ValueError(f"Unknown dialect {dialect!r}; valid dialects: vintage, extended")

    for raw in overrides:
        raw_name = normalize_feature_name(raw)
        enabled = True
        name = raw_name
        if raw_name.startswith('no-'):
            enabled = False
            name = raw_name[3:]
        if name not in _FEATURES:
            valid = ', '.join(feature_names())
            raise ValueError(f"Unknown feature {name!r}; valid features: {valid}")
        resolved[name] = enabled
    return resolved
