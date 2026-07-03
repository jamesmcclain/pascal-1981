"""Shared CUDA architectural ceilings for device-kernel launch geometry.

Single source of truth for the per-axis thread-block and grid limits used by
both the frontend (``type_checker.py`` bound-checks ``[MAXNTID]``/``[REQNTID]``
attributes against them) and the backend (``codegen/exprs.py`` uses them for
``!range`` metadata on special-register reads).

Conservative architectural ceilings for CUDA compute capability 7.0+
(``sm_70`` and later; this repo's device examples target ``sm_70``/``sm_86``,
both well within this range) per the CUDA C Programming Guide's "Compute
Capabilities" appendix. [DOCUMENTED -- CUDA architectural limits, corroborated
against a real-device query; not measured against this repository's own
code/tests.]

  threadIdx.{x,y} / blockDim.{x,y} <= 1024 ; threadIdx.z / blockDim.z <= 64.
  Total threads per block (product of extents) <= 1024.
  blockIdx/gridDim.x addresses up to 2**31-1 blocks; y/z are capped at 65535
  on all CUDA compute capabilities to date.

This module intentionally has no dependencies beyond the standard library so
it can be imported from anywhere in the package without risking an import
cycle.
"""

from __future__ import annotations

# Per-axis maximum extent of a thread block (CTA), keyed by axis letter.
NVVM_AXIS_MAX = {'X': 1024, 'Y': 1024, 'Z': 64}

# Maximum total threads per block: the product of the extents must not exceed
# this even when each individual axis is within NVVM_AXIS_MAX (e.g. 1024x2
# passes per-axis but is 2048 total and must be rejected).
NVVM_MAX_THREADS_PER_BLOCK = 1024

# Per-axis maximum grid dimension (number of blocks), keyed by axis letter.
NVVM_GRID_AXIS_MAX = {'X': 2**31 - 1, 'Y': 65535, 'Z': 65535}
