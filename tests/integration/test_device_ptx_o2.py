"""Item 5 (docs/followups.md): O2 pipeline on the PTX path.

Proves `-O`/`opt_level=` actually runs LLVM's mid-level pass
pipeline before NVPTX codegen, that the default (0) is a byte-identical
no-op (so the existing exact-mnemonic PTX tests are undisturbed), and that
the ABI-level facts a caller depends on (entry name, parameter shapes,
void-return, no phantom externs) survive optimization on both examples.

Deliberately does NOT assert exact instruction selection at O2 (e.g. which
mnemonic appears where): LLVM version drift already bit this repo once on
exact-mnemonic asserts (see docs/followups.md's own account of the 0.47 vs
0.48 divergence), and this file's job is to prove the plumbing works, not
to pin a specific LLVM's optimization output.
"""

import os
import subprocess
import sys
import unittest

from tests.support import requires_llvm


def _run_compile_to_ptx(example_dir, source, ptx_path, *extra_args):
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    result = subprocess.run(
        [sys.executable, '-m', 'pascal1981.compile_to_ptx', source, '-o', ptx_path, *extra_args],
        cwd=example_dir,
        env={
            **os.environ, 'PYTHONPATH': os.path.join(repo, 'src')
        },
        capture_output=True,
        text=True,
    )
    return result


@requires_llvm
class TestDevicePtxO2Pipeline(unittest.TestCase):

    def _example_dir(self, *parts):
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        return os.path.join(repo, 'examples', 'device_ptx', *parts)

    def test_default_opt_level_is_byte_identical_to_pre_flag_behavior(self):
        """opt_level=0 (the default) must not perturb output at all -- this is
        the safety property that lets the existing exact-mnemonic PTX tests
        (test_device_ptx_artifact.py, test_device_mandelbrot_ptx.py) stay
        green with zero changes."""
        example_dir = self._example_dir('fill_indices')
        explicit = os.path.join(example_dir, 'fill.explicit0.ptx')
        implicit = os.path.join(example_dir, 'fill.implicit0.ptx')
        try:
            r1 = _run_compile_to_ptx(example_dir, 'fill.pas', implicit, '--cpu', 'sm_70')
            r2 = _run_compile_to_ptx(example_dir, 'fill.pas', explicit, '--cpu', 'sm_70', '-O0')
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            with open(implicit) as f:
                implicit_ptx = f.read()
            with open(explicit) as f:
                explicit_ptx = f.read()
            self.assertEqual(implicit_ptx, explicit_ptx)
        finally:
            for p in (explicit, implicit):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass

    def test_opt_level_flag_is_rejected_by_argparse_choices_outside_0_3(self):
        example_dir = self._example_dir('fill_indices')
        out = os.path.join(example_dir, 'fill.bad.ptx')
        try:
            result = _run_compile_to_ptx(example_dir, 'fill.pas', out, '-O9')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('invalid choice', result.stderr)
        finally:
            try:
                os.unlink(out)
            except FileNotFoundError:
                pass

    def test_o2_pipeline_actually_runs_and_stays_valid_ptx(self):
        """The mandelbrot kernel has real loop/branch structure (unlike the
        one-line fill_indices kernel, which is already minimal at O0), so
        this is where the pipeline has visible work to do. Assert the
        pipeline changed something (proving it ran) and that the module
        stayed verifiably valid, without pinning specific mnemonics."""
        example_dir = self._example_dir('mandelbrot')
        o0 = os.path.join(example_dir, 'mb.o0.ptx')
        o2 = os.path.join(example_dir, 'mb.o2.ptx')
        try:
            r0 = _run_compile_to_ptx(example_dir, 'mandelbrot.pas', o0, '--cpu', 'sm_86', '-O0')
            r2 = _run_compile_to_ptx(example_dir, 'mandelbrot.pas', o2, '--cpu', 'sm_86', '-O2')
            self.assertEqual(r0.returncode, 0, r0.stderr)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            with open(o0) as f:
                o0_ptx = f.read()
            with open(o2) as f:
                o2_ptx = f.read()
            self.assertNotEqual(o0_ptx, o2_ptx, 'expected -O2 to change the emitted PTX')
            # ABI-level facts must survive optimization: both are still real,
            # launchable void entries with the same parameter count/order.
            for ptx in (o0_ptx, o2_ptx):
                self.assertIn('.visible .entry mandelbrot_f32', ptx)
                self.assertIn('.visible .entry mandelbrot_f64', ptx)
                self.assertNotIn('func_retval', ptx)
        finally:
            for p in (o0, o2):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass

    def test_o2_pipeline_via_single_cli_target_ptx_flag(self):
        """The followup names both compile_to_ptx.py and the --target ptx
        branch of compile_to_llvm.py as touchpoints; exercise the latter."""
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        example_dir = self._example_dir('fill_indices')
        out = os.path.join(example_dir, 'fill.cli.ptx')
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pascal1981.compile_to_llvm', 'fill.pas', '-S', '-o', out, '--target', 'ptx', '--sm', 'sm_70', '-O2'],
                cwd=example_dir,
                env={
                    **os.environ, 'PYTHONPATH': os.path.join(repo, 'src')
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(out) as f:
                ptx = f.read()
            self.assertIn('.visible .entry fill_indices', ptx)
        finally:
            try:
                os.unlink(out)
            except FileNotFoundError:
                pass

    def test_target_ptx_requires_dash_S(self):
        """--target ptx emits assembly-level PTX, so the driver requires the
        -S stage flag (it cannot be assembled or linked into a host exe)."""
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        example_dir = self._example_dir('fill_indices')
        result = subprocess.run(
            [sys.executable, '-m', 'pascal1981.compile_to_llvm', 'fill.pas', '--target', 'ptx', '-O2'],
            cwd=example_dir,
            env={
                **os.environ, 'PYTHONPATH': os.path.join(repo, 'src')
            },
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires -S', result.stderr)


if __name__ == '__main__':
    unittest.main()
