"""Kernel-body launch geometry contract.

Grid/block geometry is not declared in the kernel body.  The body
contract is just the C.1 index reads plus C.2 synchronization; launch shape stays
with the future host-launch surface.
"""

import unittest

from pascal1981.codegen import compile_to_llvm
from tests.support import parse_source, requires_llvm, typecheck_source

_KERNEL_BODY_CONTRACT_SRC = """
DEVICE MODULE M;
VAR
  [SPACE(SHARED)] scratch: ARRAY [0..7] OF INTEGER32;
  [SPACE(GLOBAL)] out: ARRAY [0..7] OF INTEGER32;

PROCEDURE kernel;
VAR
  i, stride: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < 8 DO
  BEGIN
    scratch[i] := i;
    i := i + stride
  END;

  SYNCTHREADS;

  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  WHILE i < 8 DO
  BEGIN
    out[i] := scratch[i];
    i := i + stride
  END
END;
.
"""


@requires_llvm
class TestDeviceLaunchContract(unittest.TestCase):

    def test_nvptx_kernel_body_needs_only_index_reads_and_barrier(self):
        result = typecheck_source(_KERNEL_BODY_CONTRACT_SRC)
        self.assertTrue(result.success, result.errors)

        ir = compile_to_llvm(
            parse_source(_KERNEL_BODY_CONTRACT_SRC),
            device_triple='nvptx64-nvidia-cuda',
        )

        for name in [
                'llvm.nvvm.read.ptx.sreg.tid.x',
                'llvm.nvvm.read.ptx.sreg.ctaid.x',
                'llvm.nvvm.read.ptx.sreg.ntid.x',
                'llvm.nvvm.read.ptx.sreg.nctaid.x',
                'llvm.nvvm.barrier0',
        ]:
            self.assertIn(name, ir)

        # C.3 deliberately does not invent kernel-body launch-shape syntax or
        # launch-bounds metadata.  That information belongs to the host-side launch
        # launch surface.
        self.assertNotIn('nvvm.annotations', ir)
        self.assertNotIn('maxntid', ir)
        self.assertNotIn('reqntid', ir)

        # Still no host runtime bleed-through in a device artifact.
        self.assertNotIn('declare void @abort', ir)
        self.assertNotIn('declare i32 @fflush', ir)


if __name__ == '__main__':
    unittest.main()
