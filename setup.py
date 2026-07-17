"""
Setup script for pascal1981.

A custom ``build_py`` command compiles the C runtime static libraries via
``make -C runtime`` and copies the produced archives into the package
directory so that they travel with the wheel.

Build-time tool policy:

* ``make`` and ``clang`` are HARD requirements -- the compiler is useless
  without its runtime archive, so the build fails early with an actionable
  message when either is missing.
* The CUDA archive (``libpascalrt_cuda.a``) is built automatically when the
  CUDA toolkit headers are visible to clang (probed exactly the way the
  runtime Makefile compiles ``cuda_launch.c``: a syntax-only compile of
  ``#include <cuda.h>`` against ``$CUDA_HOME/include``).  Without the
  headers the build is CPU-only and says so.

A custom ``bdist_wheel`` marks the wheel platform-specific: the bundled
archives are compiled binaries, so a ``py3-none-any`` tag would lie.  The
tag stays Python-agnostic (``py3-none-<platform>``) because the archives
are data files, not CPython extensions.  On glibc Linux the platform tag is
the PEP 600 perennial tag of the BUILD machine (e.g.
``manylinux_2_39_x86_64`` when built on Ubuntu 24.04): the archives are
compiled against the build machine's glibc, so that tag makes pip refuse
older-glibc machines at install time instead of letting the archives fail
later at link time.

All project metadata lives in pyproject.toml; this file only exists to
register the custom build commands.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

try:  # setuptools >= 70.1 vendors bdist_wheel
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # older setuptools: provided by the wheel package
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(HERE, "runtime")
PACKAGE_DIR = os.path.join(HERE, "src", "pascal1981")

RUNTIME_ARCHIVES = ("libpascalrt.a", "libpascalrt_cpu.a", "libpascalrt_cuda.a")


def _require_tools(*tools: str) -> None:
    """Fail early with an actionable message if a hard build tool is missing."""
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        print(
            "error: required build tool(s) not found on PATH: "
            + ", ".join(missing)
            + "\nThe pascal1981 build compiles its C runtime at build time, so "
            "'make' and 'clang' are required.  Install them "
            "(e.g. `apt-get install make clang`) and retry.",
            file=sys.stderr,
        )
        sys.exit(1)


def _have_cuda_headers() -> bool:
    """True iff ``<cuda.h>`` is findable by clang the way the runtime build looks.

    Mirrors the probe in tests/support.py: the Makefile compiles
    ``cuda_launch.c`` with ``-I$(CUDA_HOME)/include``, so probe exactly that
    with a syntax-only compile.  Returns False on a box that has the NVIDIA
    driver but not the CUDA toolkit headers.
    """
    cuda_home = os.environ.get("CUDA_HOME", "/usr/local/cuda")
    try:
        result = subprocess.run(
            ["clang", "-x", "c", "-fsyntax-only", "-I",
             os.path.join(cuda_home, "include"), "-"],
            input="#include <cuda.h>\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


class BuildPyWithRuntime(_build_py):
    """Custom build_py that compiles the C runtime before installing."""

    def run(self) -> None:
        # -- Hard tool requirements ------------------------------------------
        _require_tools("make", "clang")

        # -- Choose make targets: CPU always, CUDA when the toolkit is seen --
        targets = ["all"]
        if _have_cuda_headers():
            cuda_home = os.environ.get("CUDA_HOME", "/usr/local/cuda")
            print(
                f"* CUDA toolkit headers found under {cuda_home}: "
                "building the CUDA runtime archive too",
                file=sys.stderr,
            )
            targets.append("cuda")
        else:
            print(
                "* CUDA toolkit headers not found: building the CPU runtime "
                "only (libpascalrt_cuda.a will be omitted; set CUDA_HOME to "
                "a CUDA toolkit prefix to enable it)",
                file=sys.stderr,
            )

        # -- Build the C runtime static libraries ----------------------------
        make_cmd = ["make", "-C", RUNTIME_DIR, *targets]
        print(f"* building C runtime: {' '.join(make_cmd)}", file=sys.stderr)
        try:
            subprocess.run(make_cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
        except subprocess.CalledProcessError:
            print("error: failed to compile the C runtime.", file=sys.stderr)
            sys.exit(1)

        # -- Copy produced archives into the package directory ---------------
        for archive in RUNTIME_ARCHIVES:
            src = os.path.join(RUNTIME_DIR, "build", archive)
            dst = os.path.join(PACKAGE_DIR, archive)

            if not os.path.exists(src):
                # Remove stale archives from earlier, fuller builds so the
                # wheel never ships an archive this build did not produce.
                # Two hiding places: the package dir (previous copy step) and
                # build/lib (pip builds in-tree, so build/ persists across
                # runs and build_py never deletes files from it).
                if archive != "libpascalrt.a":
                    for stale in (dst, os.path.join(self.build_lib, "pascal1981", archive)):
                        if os.path.exists(stale):
                            print(f"* removing stale {stale}", file=sys.stderr)
                            os.remove(stale)
                print(f"* skipping missing optional runtime archive {src}", file=sys.stderr)
                continue

            print(f"* copying {src} -> {dst}", file=sys.stderr)
            shutil.copy2(src, dst)

        # -- Proceed with normal Python build --------------------------------
        super().run()


class NonPureBdistWheel(_bdist_wheel):
    """Tag the wheel platform-specific but Python-agnostic.

    The wheel bundles compiled static archives, so ``py3-none-any`` would be
    a lie; but the archives are data files rather than CPython extensions, so
    a CPython ABI tag would overclaim.  On glibc Linux the platform is the
    PEP 600 perennial tag computed from the build machine's own glibc
    (``manylinux_<glibc>_<arch>``), which is exactly the floor the compiled
    archives impose.  Non-Linux or non-glibc build hosts keep the default
    platform tag.
    """

    def finalize_options(self) -> None:
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        _impl, _abi, plat = super().get_tag()
        libc, version = platform.libc_ver()
        if sys.platform.startswith("linux") and libc == "glibc" and version:
            major, minor = version.split(".")[:2]
            plat = f"manylinux_{major}_{minor}_{platform.machine()}"
        return "py3", "none", plat


setup(cmdclass={"build_py": BuildPyWithRuntime, "bdist_wheel": NonPureBdistWheel})
