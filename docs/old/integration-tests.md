# Integration tests

The `tests/integration/` tier exists to lock down **multi-file** behavior that a
single-buffer unit test cannot reach.

These tests use real files on disk and exercise some or all of:

- interface / implementation resolution by filename
- `USES` import binding
- separate compilation to multiple `.ll` files
- `clang` link of separately compiled units
- execution of the linked native binary

Current integration fixtures include:

- `tests/integration/test_device_primes.py` — `DEVICE INTERFACE` + `DEVICE IMPLEMENTATION OF`
  + host `USES` program, linked and run on the CPU-device path
- `tests/integration/test_host_uses.py` — plain host `INTERFACE` / `IMPLEMENTATION` /
  `USES` control case
- `tests/integration/test_uses_graphics.py` — plain and renamed `USES GRAPHICS`
  end-to-end cases, including IR-level proof that positional renames bind to the
  real exported symbols

## Running the integration tests

From a source checkout:

```bash
PYTHONPATH=src python3 -m pytest tests/integration/ -q
```

Run one fixture:

```bash
PYTHONPATH=src python3 -m pytest tests/integration/test_device_primes.py -q
```

These tests require the executable-build toolchain (`llvmlite` + `clang`) and
use the same `@requires_exe` skip discipline as the rest of the suite.

## About `-Wl,--allow-multiple-definition`

Some current multi-file integration tests link with:

```text
-Wl,--allow-multiple-definition
```

This is a **temporary workaround**, not a stable contract of the compiler.
Today, separately compiled Pascal units can each emit shared runtime globals
(such as `input`, `output`, and related predeclared/runtime symbols), which
forces the host linker to tolerate duplicate definitions.

This should be revisited once the Phase 2 cleanup lands. The relevant design
notes already call out the root smell:

- `docs/cuda-kernel-prescription.md` §5.3 — the current link hack should go away
  once device/runtime emission is cleaned up
- `docs/device-unit-migration-checklist.md` Phase 2 (§2.2 in particular) — stop
  unconditionally dumping extern/runtime baggage into every compiland

Until then, integration tests that model the real multi-file workflow may need
that linker flag. Treat it as evidence of debt, not as a feature.
