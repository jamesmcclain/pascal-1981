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


_FEATURES: Dict[str, Feature] = {
    'wide-integers':
    Feature(
        name='wide-integers',
        default=False,
        help='Enable extension INTEGER32/INTEGER64 types and wide integer constants.',
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
}


def all_features() -> Iterable[Feature]:
    return _FEATURES.values()


def feature_names() -> list[str]:
    return sorted(_FEATURES)


def default_features() -> Dict[str, bool]:
    return {name: feature.default for name, feature in _FEATURES.items()}


def extended_features() -> Dict[str, bool]:
    return {name: True for name in _FEATURES}


# ---------------------------------------------------------------------------
# Device dialect (DEVICE MODULE) scaffold -- inert until Step 2/3 wiring.
#
# Per ads-memory-spaces-design.md (S1.2, S9) and the implementation plan
# (Step 0.5): a DEVICE MODULE uses the *device dialect* =
#     extended host features - a recission set + the address-space surface.
#
# The address-space surface is NOT a feature flag -- module kind subsumes it
# (design S3.1, plan Step 0.5), so it is intentionally absent here; it is
# registered by module-kind gating in the type checker, not toggled via
# resolve_features.
#
# The recission set is deliberately EMPTY and NOT FROZEN. The candidate
# constructs (recursion, set I/O / dynamic set ranges, NEW/heap, host I/O,
# nonlocal GOTO, flat-heap pointer-chasing) are *language constructs*, not
# entries in _FEATURES, so they are enforced as module-scoped checker bans in
# Step 3 -- not expressible as feature toggles. Names listed here that match a
# real feature flag are turned off; the rest are owned by the checker. The set
# stays empty until the owner decides the recission set per-construct.
_DEVICE_RECISSIONS: frozenset[str] = frozenset()  # NOT FROZEN -- owner decision pending


def device_features(host_features: Dict[str, bool] | None = None) -> Dict[str, bool]:
    """Build the DEVICE MODULE feature set from a host baseline.

    Scaffold only: not yet consumed by any caller. The active feature set
    becomes module-scoped (plan Step 0.5) once the parser learns the DEVICE
    keyword (Step 2) and the checker swaps it in on entry (Step 3). Until
    then this is inert and the faithful/host path is unaffected.
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
