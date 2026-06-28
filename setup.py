"""
Setup script for pascal1981.

A custom ``build_py`` command compiles the C runtime static libraries via
``make -C runtime`` and copies any produced archives into the package directory
so that they travel with the wheel.

All project metadata lives in pyproject.toml; this file only exists to
register the custom build command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(HERE, "runtime")
PACKAGE_DIR = os.path.join(HERE, "src", "pascal1981")


class BuildPyWithRuntime(_build_py):
    """Custom build_py that compiles the C runtime before installing."""

    def run(self) -> None:
        # -- Build the C runtime static library ------------------------------
        make_cmd = ["make", "-C", RUNTIME_DIR]
        print(f"* building C runtime: {' '.join(make_cmd)}", file=sys.stderr)
        try:
            subprocess.run(make_cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
        except subprocess.CalledProcessError:
            print(
                "error: failed to compile the C runtime.  Is clang installed?",
                file=sys.stderr,
            )
            sys.exit(1)

        # -- Copy produced archives into the package directory ---------------
        for archive in ("libpascalrt.a", "libpascalrt_cpu.a", "libpascalrt_cuda.a"):
            src = os.path.join(RUNTIME_DIR, "build", archive)
            dst = os.path.join(PACKAGE_DIR, archive)

            if not os.path.exists(src):
                if archive != "libpascalrt.a" and os.path.exists(dst):
                    os.remove(dst)
                print(f"* skipping missing optional runtime archive {src}", file=sys.stderr)
                continue

            print(f"* copying {src} -> {dst}", file=sys.stderr)
            shutil.copy2(src, dst)

        # -- Proceed with normal Python build --------------------------------
        super().run()


setup(cmdclass={"build_py": BuildPyWithRuntime})
