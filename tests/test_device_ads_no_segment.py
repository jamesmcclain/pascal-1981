"""Regression for followups.md item 4: device ADS values are bare addrspace
pointers, never the vintage {ptr, i16} segmented pair.

The AdsExpr value form (`ADS g` on a `[SPACE(GLOBAL)]` variable) inside a DEVICE
MODULE must lower to a bare ``addrspace(1)`` pointer, with no ``{ptr, i16}``
intermediary.  The host vintage path keeps the segmented form; only device code
is asserted here.  This pins the item-4 cleanup so the segmented form cannot
silently creep back into device lowering.
"""

import os
import shutil
import tempfile
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import requires_llvm

_DEVICE_ADS_ROUNDTRIP = """\
DEVICE MODULE adstest;
PROCEDURE k;
VAR
  [SPACE(GLOBAL)] g: INTEGER32;
  p: ADS(GLOBAL) OF INTEGER32;
  v: INTEGER32;
BEGIN
  p := ADS g;
  v := p^;
  p^ := v
END;
.
"""


@requires_llvm
class TestDeviceAdsNoSegment(unittest.TestCase):

    def _device_ir(self, src: str, triple: str) -> str:
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, 'adstest.pas')
            with open(path, 'w') as f:
                f.write(src)
            ast = parse_file(path)
            result = PascalTypeChecker(source_file=path).check(ast)
            assert result.success, result.errors
            return compile_to_llvm(ast, source_file=path, device_triple=triple)
        finally:
            shutil.rmtree(tmpdir)

    def test_nvptx_device_ads_is_bare_addrspace_pointer(self):
        ir = self._device_ir(_DEVICE_ADS_ROUNDTRIP, 'nvptx64-nvidia-cuda')
        # No segmented {ptr, i16} pair anywhere in the device IR.
        self.assertNotRegex(ir, r'\{\s*i\d+\s+addrspace\(1\)\*\s*,\s*i16\s*\}')
        self.assertNotRegex(ir, r'i16\s*\}')
        # The global lives in addrspace(1) and is addressed by a bare pointer.
        self.assertIn('addrspace(1)', ir)

    def test_cpu_device_ads_has_no_segment_struct(self):
        # device=x86 collapses spaces to addrspace 0; still no {ptr, i16}.
        ir = self._device_ir(_DEVICE_ADS_ROUNDTRIP, 'x86_64-pc-linux-gnu')
        self.assertNotRegex(ir, r'i16\s*\}')


if __name__ == '__main__':
    unittest.main()
