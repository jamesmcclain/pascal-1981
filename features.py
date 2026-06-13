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
    'wide-integers': Feature(
        name='wide-integers',
        default=False,
        help='Enable extension INTEGER32/INTEGER64 types and wide integer constants.',
    ),
    'symbolic-enum-io': Feature(
        name='symbolic-enum-io',
        default=False,
        help='Enable symbolic (name-based) enum WRITE and READ instead of the faithful 1981 ordinal/numeric form; BOOLEAN always writes TRUE/FALSE regardless.',
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
