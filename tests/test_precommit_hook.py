"""Behavior of the tracked pre-commit formatting hook.

The hook (``scripts/hooks/pre-commit``) runs ``scripts/beautify.sh`` and then
re-stages the files the commit already touched. Its predecessor did only the
first half, so reformatting landed in the working tree and was silently left
*out* of the commit that triggered it.

Re-staging cannot simply be ``git add -u``: beautify.sh formats the whole
tree, so that would sweep unrelated working-tree edits into the commit. The
tests below pin both halves of that contract -- reformatted staged files go in,
everything else stays out.

Each test builds a throwaway git repository containing the *real* hook and
beautify.sh, so the artifact under test is the one that ships.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import requires_formatters

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / 'scripts' / 'hooks' / 'pre-commit'
BEAUTIFY = REPO_ROOT / 'scripts' / 'beautify.sh'

# Deliberately misformatted C: indent -kr reflows all of this.
UGLY_C = 'int   f( int x ){return x   ;}\n'
TIDY_C = 'int f(int x)\n{\n    return x;\n}\n'


class PreCommitHookTests(unittest.TestCase):
    """End-to-end runs of the hook in a scratch repository."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / 'repo'
        # A HOME beside the repo, not inside it: isort and yapf drop a
        # .cache/ directory there, which would otherwise surface as untracked
        # content and defeat the working-tree-clean assertions.
        self.home = root / 'home'
        self.repo.mkdir()
        self.home.mkdir()

        # beautify.sh runs `find src tests setup.py ... -name '*.py'` and
        # `find runtime -name '*.c'` under `set -e`, so every one of those
        # paths must exist or the hook aborts on a missing-operand error.
        for d in ('src', 'tests', 'runtime', 'scripts/hooks'):
            (self.repo / d).mkdir(parents=True)
        (self.repo / 'setup.py').write_text('')

        shutil.copy2(BEAUTIFY, self.repo / 'scripts' / 'beautify.sh')
        shutil.copy2(HOOK, self.repo / 'scripts' / 'hooks' / 'pre-commit')

        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.hooksPath', 'scripts/hooks')
        self.git('add', '-A')
        self.commit('initial')

    def git(self, *args):
        """Run git in the scratch repo, asserting success."""
        r = self._git(*args)
        self.assertEqual(r.returncode, 0, f'git {" ".join(args)} failed:\n{r.stdout}\n{r.stderr}')
        return r

    def _git(self, *args):
        env = dict(os.environ)
        # Keep an ambient user gitconfig or hook path from reaching this repo.
        env.update(GIT_CONFIG_NOSYSTEM='1', HOME=str(self.home), GIT_TERMINAL_PROMPT='0')
        env.pop('GIT_DIR', None)
        return subprocess.run(('git', ) + args, cwd=self.repo, capture_output=True, text=True, env=env, timeout=180)

    def commit(self, message):
        """Commit, returning the completed process (may have failed)."""
        return self._git('commit', '-m', message)

    def write(self, relpath, text):
        p = self.repo / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def committed(self, relpath):
        return self.git('show', f'HEAD:{relpath}').stdout

    def porcelain(self):
        return self.git('status', '--porcelain').stdout.strip()

    # -- the contract -----------------------------------------------------

    def test_reformats_and_restages_staged_file(self):
        """A staged misformatted file is committed *formatted*."""
        self.write('runtime/a.c', UGLY_C)
        self.git('add', 'runtime/a.c')
        r = self.commit('add a.c')

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.committed('runtime/a.c'), TIDY_C)

    def test_worktree_clean_after_commit(self):
        """The reformat must not survive as an unstaged change.

        This is the regression the old hook produced on every single commit:
        it rewrote the file in place without staging it, so the file
        reappeared as modified the instant the commit finished.
        """
        self.write('runtime/a.c', UGLY_C)
        self.git('add', 'runtime/a.c')
        self.commit('add a.c')

        self.assertEqual(self.porcelain(), '')

    def test_unstaged_file_is_not_swept_into_commit(self):
        """Unrelated working-tree edits stay out, even though beautify.sh
        reformats the whole tree and thus touches them too."""
        self.write('runtime/staged.c', UGLY_C)
        self.write('runtime/loose.c', UGLY_C)  # never staged
        self.git('add', 'runtime/staged.c')
        self.commit('add staged.c')

        files = self.git('show', '--name-only', '--format=', 'HEAD').stdout.split()
        self.assertEqual(files, ['runtime/staged.c'])
        # Still untracked, and still holding its own content.
        self.assertIn('?? runtime/loose.c', self.porcelain())

    def test_python_files_are_restaged_too(self):
        """The re-staging is not C-specific; isort/yapf output counts."""
        self.write('tests/z_fmt.py', 'x = {   "a":1 }\n')
        self.git('add', 'tests/z_fmt.py')
        self.commit('add py')

        self.assertEqual(self.committed('tests/z_fmt.py'), 'x = {"a": 1}\n')
        self.assertEqual(self.porcelain(), '')

    def test_already_formatted_file_produces_no_hook_output(self):
        """A clean commit stays quiet -- no spurious 're-staged' line."""
        self.write('runtime/tidy.c', TIDY_C)
        self.git('add', 'runtime/tidy.c')
        r = self.commit('add tidy.c')

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('re-staged', r.stdout + r.stderr)
        self.assertEqual(self.porcelain(), '')

    def test_empty_stage_does_not_trip_set_u(self):
        """`git commit --allow-empty` stages nothing; the hook must not die
        dereferencing an empty array under `set -u`."""
        r = self._git('commit', '--allow-empty', '-m', 'empty')

        self.assertEqual(r.returncode, 0, r.stderr)

    def test_partially_staged_file_warns_and_proceeds(self):
        """A file with staged *and* unstaged hunks cannot be re-staged: `git
        add` would take the unstaged hunks too. The hook reports it rather
        than re-staging, and lets the commit through.

        Aborting here was tried and rejected -- beautify.sh has already
        dirtied the tree by the time the hook can tell, so an immediate retry
        finds nothing left to change and commits the stale content anyway.
        """
        self.write('runtime/p.c', TIDY_C)
        self.git('add', 'runtime/p.c')
        self.commit('seed p.c')

        self.write('runtime/p.c', TIDY_C + '\n' + UGLY_C)
        self.git('add', 'runtime/p.c')  # stage the ugly hunk
        self.write('runtime/p.c', TIDY_C + '\n' + UGLY_C + '\nint c(void)\n{\n    return 3;\n}\n')

        r = self.commit('partial')

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('partially staged', r.stderr)
        self.assertIn('runtime/p.c', r.stderr)


class TrackedHookTests(unittest.TestCase):
    """Properties of the shipped hook file itself."""

    def test_hook_is_tracked_and_executable(self):
        """Mode 100755 in the index: a hook without the executable bit is
        silently ignored by git, which would disable formatting for everyone
        who clones."""
        r = subprocess.run(['git', 'ls-files', '-s', 'scripts/hooks/pre-commit'], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip(), 'scripts/hooks/pre-commit is not tracked')
        self.assertEqual(r.stdout.split()[0], '100755')


# Applied here rather than per-method so the whole module skips as one unit.
PreCommitHookTests = requires_formatters(PreCommitHookTests)
TrackedHookTests = unittest.skipUnless(shutil.which('git'), 'requires git')(TrackedHookTests)

if __name__ == '__main__':
    unittest.main()
