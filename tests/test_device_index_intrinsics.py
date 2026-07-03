import unittest

from pascal1981.codegen import compile_to_llvm
from tests.support import parse_source, requires_llvm, typecheck_source

DEVICE_SRC = """
DEVICE MODULE M;
VAR x: INTEGER32;
PROCEDURE go;
BEGIN
  x := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X + GRIDDIM_X
END;
.
"""

ALL_INDEX_READS_SRC = """
DEVICE MODULE M;
VAR x: INTEGER32;
PROCEDURE go;
BEGIN
  x := THREADIDX_X + THREADIDX_Y + THREADIDX_Z
     + BLOCKIDX_X + BLOCKIDX_Y + BLOCKIDX_Z
     + BLOCKDIM_X + BLOCKDIM_Y + BLOCKDIM_Z
     + GRIDDIM_X + GRIDDIM_Y + GRIDDIM_Z
END;
.
"""


class DeviceIndexIntrinsicTypecheckTests(unittest.TestCase):

    def test_normal_host_code_rejects_threadidx(self):
        result = typecheck_source("PROGRAM P; VAR x: INTEGER; BEGIN x := THREADIDX_X END.")
        self.assertFalse(result.success)
        self.assertTrue(any('THREADIDX_X is only available in DEVICE code' in e.message for e in result.errors), result.errors)

    def test_device_code_accepts_integer32_and_index_reads_without_feature_flag(self):
        result = typecheck_source(DEVICE_SRC)
        self.assertTrue(result.success, result.errors)

    def test_device_index_read_requires_no_arguments(self):
        result = typecheck_source("DEVICE MODULE M; VAR x: INTEGER32; PROCEDURE go; BEGIN x := THREADIDX_X(1) END; .")
        self.assertFalse(result.success)
        self.assertTrue(any("Function 'THREADIDX_X' expects 0 arguments" in e.message for e in result.errors), result.errors)


@requires_llvm
class DeviceIndexIntrinsicCodegenTests(unittest.TestCase):

    def _compile(self, src, device_triple='x86_64-pc-linux-gnu'):
        result = typecheck_source(src)
        self.assertTrue(result.success, result.errors)
        ast = parse_source(src)
        return compile_to_llvm(ast, device_triple=device_triple)

    def test_cpu_device_lowers_reads_to_tls_globals(self):
        ir = self._compile(DEVICE_SRC)
        self.assertNotIn('llvm.nvvm.read.ptx.sreg', ir)
        # Each builtin lowers to a load from a thread-local global so that
        # pas_dev_launch can set the correct index before each thunk call.
        self.assertIn('thread_local global i32', ir)
        self.assertIn('@"__pas_tid_x"', ir)
        self.assertIn('@"__pas_ntid_x"', ir)
        self.assertIn('@"__pas_ctaid_x"', ir)

    def test_nvptx_lowers_all_reads_to_special_register_intrinsics(self):
        ir = self._compile(ALL_INDEX_READS_SRC, device_triple='nvptx64-nvidia-cuda')
        for name in [
                'llvm.nvvm.read.ptx.sreg.tid.x',
                'llvm.nvvm.read.ptx.sreg.tid.y',
                'llvm.nvvm.read.ptx.sreg.tid.z',
                'llvm.nvvm.read.ptx.sreg.ctaid.x',
                'llvm.nvvm.read.ptx.sreg.ctaid.y',
                'llvm.nvvm.read.ptx.sreg.ctaid.z',
                'llvm.nvvm.read.ptx.sreg.ntid.x',
                'llvm.nvvm.read.ptx.sreg.ntid.y',
                'llvm.nvvm.read.ptx.sreg.ntid.z',
                'llvm.nvvm.read.ptx.sreg.nctaid.x',
                'llvm.nvvm.read.ptx.sreg.nctaid.y',
                'llvm.nvvm.read.ptx.sreg.nctaid.z',
        ]:
            self.assertIn(name, ir)
        self.assertNotIn('declare void @abort', ir)
        self.assertNotIn('declare i32 @fflush', ir)

    def test_nvptx_sreg_calls_carry_range_metadata(self):
        """docs/followups.md item 7: every sreg read carries !range so LLVM
        can prove the index math is non-negative and non-overflowing, without
        needing per-launch information.

        tid/ctaid range over [0, max); ntid/nctaid range over [1, max+1) (a
        block/grid always has at least one thread/block along an axis).
        """
        ir = self._compile(ALL_INDEX_READS_SRC, device_triple='nvptx64-nvidia-cuda')
        expected_ranges = {
            'llvm.nvvm.read.ptx.sreg.tid.x': (0, 1024),
            'llvm.nvvm.read.ptx.sreg.tid.y': (0, 1024),
            'llvm.nvvm.read.ptx.sreg.tid.z': (0, 64),
            'llvm.nvvm.read.ptx.sreg.ctaid.x': (0, 2**31 - 1),
            'llvm.nvvm.read.ptx.sreg.ctaid.y': (0, 65535),
            'llvm.nvvm.read.ptx.sreg.ctaid.z': (0, 65535),
            'llvm.nvvm.read.ptx.sreg.ntid.x': (1, 1025),
            'llvm.nvvm.read.ptx.sreg.ntid.y': (1, 1025),
            'llvm.nvvm.read.ptx.sreg.ntid.z': (1, 65),
            'llvm.nvvm.read.ptx.sreg.nctaid.x': (1, 2**31),
            'llvm.nvvm.read.ptx.sreg.nctaid.y': (1, 65536),
            'llvm.nvvm.read.ptx.sreg.nctaid.z': (1, 65536),
        }
        import re
        # Every call site to each intrinsic carries a `!range` reference; find
        # the referenced metadata node and check its (lo, hi) pair.
        for name, (lo, hi) in expected_ranges.items():
            call_match = re.search(r'call i32 @"' + re.escape(name) + r'"\(\), !range !(\d+)', ir)
            self.assertIsNotNone(call_match, f'no !range-tagged call to {name} found')
            node_id = call_match.group(1)
            node_match = re.search(r'!' + node_id + r' = !\{ i32 (-?\d+), i32 (-?\d+) \}', ir)
            self.assertIsNotNone(node_match, f'metadata node !{node_id} for {name} not found')
            self.assertEqual((int(node_match.group(1)), int(node_match.group(2))), (lo, hi), f'{name} range mismatch')

    def test_cpu_device_reads_carry_no_range_metadata(self):
        """!range is an NVPTX-sreg-specific fact; the CPU-device TLS-global
        load path has nothing analogous and must stay untouched."""
        ir = self._compile(ALL_INDEX_READS_SRC, device_triple='x86_64-pc-linux-gnu')
        self.assertNotIn('!range', ir)


if __name__ == '__main__':
    unittest.main()
