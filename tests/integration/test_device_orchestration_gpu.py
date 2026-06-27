"""Milestone D on a REAL GPU — the CUDA Driver API shim (cuda-kernel-prescription
§5.2 Strategy 1, §9).

This is the GPU counterpart of ``test_device_orchestration.py``: the *same*
Pascal vector-add (a ``DEVICE UNIT`` kernel + a host ``PROGRAM`` that DEVALLOCs,
H2D-copies, LAUNCHes, D2H-copies, and prints) but run on the device through
``runtime/cuda_launch.c`` (cuMemAlloc / cuMemcpyHtoD / cuModuleLoadData /
cuLaunchKernel / ...) instead of the CPU stand-in.  Running the identical Pascal
on the GPU is a pure runtime-library swap; this test proves it.

Gated by ``@requires_gpu`` so it skips cleanly on CPU-only machines.

The device kernel is compiled to PTX (NVPTX backend) and embedded into the host
compiland via ``--embed-device-ptx``; the host links the CUDA shim archive plus
``-lcuda``.  Asserts the result is ``0 3 6 … 21``.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from pascal1981.compile_to_ptx import compile_file_to_ptx
from pascal1981.features import resolve_features
from tests.support import (RUNTIME_DIR, compile_pascal_file, requires_gpu,
                           temporary_pascal_project)

_WIDE = resolve_features(overrides=['wide-integers'])

_INTERFACE = """\
DEVICE INTERFACE;
UNIT vadd (add);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""

_IMPLEMENTATION = """\
(*$INCLUDE:'vadd.inc'*)
DEVICE IMPLEMENTATION OF vadd;
PROCEDURE add(a: ADS(GLOBAL) OF BUFFER; b: ADS(GLOBAL) OF BUFFER;
              c: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR
  i, stride: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < n DO
  BEGIN
    c^[i] := a^[i] + b^[i];
    i := i + stride
  END
END;
.
"""

_MAIN = """\
(*$INCLUDE:'vadd.inc'*)
PROGRAM main(output);
USES vadd (add);
CONST n = 8;
VAR
  ha, hb, hc: ARRAY [0..7] OF INTEGER32;
  da, db, dc: ADRMEM;
  i: INTEGER;
  bytes: INTEGER;
BEGIN
  bytes := n * 4;
  FOR i := 0 TO n - 1 DO
  BEGIN
    ha[i] := i;
    hb[i] := i + i;
    hc[i] := 0
  END;
  da := DEVALLOC(bytes);
  db := DEVALLOC(bytes);
  dc := DEVALLOC(bytes);
  DEVCOPYTO(da, ADR ha, bytes);
  DEVCOPYTO(db, ADR hb, bytes);
  LAUNCH(add, 1, n, da, db, dc, n);
  DEVCOPYFROM(ADR hc, dc, bytes);
  FOR i := 0 TO n - 1 DO
    WRITELN(hc[i]);
  DEVFREE(da);
  DEVFREE(db);
  DEVFREE(dc)
END.
"""


def _build_cuda_runtime(tmpdir: str) -> str:
    """Build the CUDA-shim runtime archive into an ISOLATED temp dir.

    Building in a *copy* of the runtime sources keeps the shared source-tree
    ``runtime/build/`` (which every other link test links against as
    ``libpascalrt.a``) completely untouched -- so this test can neither delete
    nor repoint it, and a build failure leaves only a leaked /tmp dir, never a
    broken source tree.  Raises ``unittest.SkipTest`` (so a GPU box with a
    broken/incomplete CUDA toolkit skips cleanly instead of erroring and
    cascading failures into every other link test) if the build fails.
    """
    # Copy the runtime sources (skip any pre-existing build/ dir) so the
    # Makefile can build self-contained inside the temp dir.
    for name in os.listdir(RUNTIME_DIR):
        if name == 'build':
            continue
        src = os.path.join(RUNTIME_DIR, name)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(tmpdir, name))
    r = subprocess.run(["make", "-C", tmpdir, "DEVICE_SHIM=cuda"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise unittest.SkipTest(f"CUDA runtime build failed: {r.stderr}")
    out = os.path.join(tmpdir, "build", "libpascalrt.a")
    if not os.path.exists(out):
        raise unittest.SkipTest("CUDA runtime build failed: no archive produced")
    return out


@requires_gpu
class TestDeviceOrchestrationVectorAddGPU(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Build into an isolated temp dir; never touch the shared
        # runtime/build/ that every other link test depends on.
        cls._runtime_tmp = tempfile.mkdtemp(prefix='pascalrt-cuda-')
        try:
            cls.runtime_lib = _build_cuda_runtime(cls._runtime_tmp)
        except BaseException:
            # setUpClass failure (incl. SkipTest) skips tearDownClass, so clean
            # the temp dir here rather than leak it.
            shutil.rmtree(cls._runtime_tmp, ignore_errors=True)
            cls._runtime_tmp = None
            raise

    @classmethod
    def tearDownClass(cls):
        # The only shared state we created is our private temp dir; the source
        # tree's runtime/build/ was never touched, so there is nothing to
        # restore.
        tmp = getattr(cls, '_runtime_tmp', None)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_vector_add_runs_on_gpu(self):
        files = {
            'vadd.inc': _INTERFACE,
            'vadd.pas': _IMPLEMENTATION,
            'main.pas': _MAIN,
        }
        cuda_home = os.environ.get("CUDA_HOME", "/usr/local/cuda")
        with temporary_pascal_project(files) as proj:
            inc = os.path.join(proj, 'vadd.inc')
            dev = os.path.join(proj, 'vadd.pas')
            main = os.path.join(proj, 'main.pas')

            # 1. device unit -> PTX (NVPTX backend).
            ptx_path = os.path.join(proj, 'vadd.ptx')
            ptx = compile_file_to_ptx(dev, device_triple='nvptx64-nvidia-cuda',
                                      cpu='sm_70', features=_WIDE)
            with open(ptx_path, 'w') as f:
                f.write(ptx)

            # 2/3. device unit + interface -> host x86 .ll (the device .ll
            # defines the kernel symbol the host launch thunk references; the
            # real kernel comes from the embedded PTX at run time).
            dev_ll = compile_pascal_file(dev, os.path.join(proj, 'vadd.ll'),
                                         features=_WIDE)
            compile_pascal_file(inc, os.path.join(proj, 'vadd-iface.ll'),
                                features=_WIDE)

            # 4. host program -> .ll, embedding the PTX.
            from pascal1981.codegen import compile_to_llvm
            from pascal1981.parser import parse_file
            from pascal1981.type_checker import PascalTypeChecker
            ast = parse_file(main)
            self.assertTrue(PascalTypeChecker(source_file=main, features=_WIDE)
                            .check(ast).success)
            main_ll = os.path.join(proj, 'main.ll')
            with open(main_ll, 'w') as f:
                f.write(compile_to_llvm(ast, source_file=main, features=_WIDE,
                                        embed_device_ptx_text=ptx))

            # 5. link host + device .ll + CUDA shim + -lcuda.
            exe = os.path.join(proj, 'vadd-gpu')
            link = subprocess.run(
                ['clang', main_ll, dev_ll, self.runtime_lib,
                 '-L' + os.path.join(cuda_home, 'lib64', 'stubs'), '-lcuda',
                 '-o', exe],
                capture_output=True, text=True)
            self.assertEqual(link.returncode, 0, msg=link.stderr)

            # 6. run on the GPU.
            run = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, msg=run.stderr)
            self.assertEqual(run.stdout.split(),
                             ['0', '3', '6', '9', '12', '15', '18', '21'])


if __name__ == '__main__':
    unittest.main()
