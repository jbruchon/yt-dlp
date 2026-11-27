import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from devscripts.source_snapshot import manifest_digest, materialize_commit, verify_snapshot


@unittest.skipUnless(shutil.which('git'), 'requires Git')
class TestSourceSnapshot(unittest.TestCase):
    def test_committed_bytes_not_hidden_worktree_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()

            git('init')
            test = repo / 'test_fixture.py'
            test.write_bytes(b'assert False\n')
            (repo / '.gitattributes').write_bytes(b'test_fixture.py export-ignore\n')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
            head = git('rev-parse', 'HEAD')
            git('update-index', '--assume-unchanged', 'test_fixture.py')
            test.write_bytes(b'assert True\n')
            self.assertEqual(git('status', '--porcelain'), '')
            generation = root / 'generation'
            manifest = materialize_commit(repo, head, generation)
            self.assertEqual((generation / test.name).read_bytes(), b'assert False\n')
            verify_snapshot(generation, manifest)
            second = root / 'second'
            self.assertEqual(manifest_digest(materialize_commit(repo, head, second)), manifest_digest(manifest))
            (generation / test.name).write_bytes(b'assert True\n')
            with self.assertRaises(ValueError):
                verify_snapshot(generation, manifest)

    def test_parallel_rejects_hidden_failing_committed_test(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            scripts = repo / 'devscripts'
            scripts.mkdir()
            for filename in ('source_snapshot.py', 'parallel_tests.py', 'strict_json.py', 'process_lifecycle.py'):
                shutil.copyfile(project / 'devscripts' / filename, scripts / filename)
            (repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n', encoding='utf-8')
            test = repo / 'test_fixture.py'
            test.write_bytes(b'def test_fixture(): assert False\n')

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()

            git('init')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
            git('update-index', '--assume-unchanged', test.name)
            test.write_bytes(b'def test_fixture(): assert True\n')
            self.assertEqual(git('status', '--porcelain'), '')
            env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
            env.pop('PYTHONPATH', None)
            command = [sys.executable, '-c',
                       'from devscripts.parallel_tests import run_parallel; raise SystemExit(run_parallel(1, flaky=True))']
            result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('test_fixture.py::test_fixture', result.stdout)
            self.assertIn('assert False', result.stdout)
            self.assertNotIn('OPT source/coverage receipt:', result.stdout)

    @unittest.skipIf(os.name == 'nt' or sys.platform == 'cygwin', 'POSIX executable bit')
    def test_committed_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()
            subprocess.check_call(['git', 'init'], cwd=repo)
            file = repo / 'script'
            file.write_bytes(b'#!/bin/sh\n')
            file.chmod(0o755)
            subprocess.check_call(['git', 'add', '.'], cwd=repo)
            subprocess.check_call(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                   'commit', '-m', 'fixture'], cwd=repo)
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
            dest = root / 'generation'
            manifest = materialize_commit(repo, head, dest)
            verify_snapshot(dest, manifest)
            (dest / 'script').chmod(0o644)
            with self.assertRaises(ValueError):
                verify_snapshot(dest, manifest)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX SIGINT delivery')
    def test_interrupt_retains_completed_attempts(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            scripts = repo / 'devscripts'
            scripts.mkdir(parents=True)
            for filename in ('source_snapshot.py', 'parallel_tests.py', 'strict_json.py', 'process_lifecycle.py'):
                shutil.copyfile(project / 'devscripts' / filename, scripts / filename)
            (repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n', encoding='utf-8')
            (repo / 'test_first.py').write_text('def test_first(): pass\n', encoding='utf-8')
            (repo / 'test_second.py').write_text(
                'import json, os, time\n'
                'from pathlib import Path\n'
                'def test_second():\n'
                '    spec = json.loads(Path(os.environ["YTDLP_OPT_SPEC"]).read_text())\n'
                '    if spec["assigned"] is not None:\n'
                '        Path(os.environ["OPT_INTERRUPT_MARKER"]).touch()\n'
                '        time.sleep(1)\n', encoding='utf-8')
            for args in (('init',), ('add', '.'),
                         ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')):
                subprocess.check_call(['git', *args], cwd=repo)
            marker = root / 'active'
            env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', OPT_INTERRUPT_MARKER=str(marker))
            env.pop('PYTHONPATH', None)
            command = [sys.executable, '-c',
                       'from devscripts.parallel_tests import run_parallel; raise SystemExit(run_parallel(2, flaky=True, verify=True))']
            with subprocess.Popen(command, cwd=repo, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) as process:
                try:
                    deadline = time.monotonic() + 30
                    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), 'worker did not reach the interruption fixture')
                    process.send_signal(signal.SIGINT)
                    stdout, stderr = process.communicate(timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            self.assertEqual(process.returncode, 130, stdout + stderr)
            self.assertNotIn('OPT source/coverage receipt:', stdout)
            prefix = 'OPT failure receipt: '
            receipt = json.loads(next(line[len(prefix):] for line in stderr.splitlines() if line.startswith(prefix)))
            self.assertEqual(receipt['status'], 'interrupted')
            self.assertFalse(receipt['outcome_parity_verified'])
            attempts = {item['name']: item for item in receipt['attempts']}
            self.assertEqual(attempts['reference']['status'], 'completed')
            self.assertIn('shard-0', attempts)
            self.assertIn('shard-1', attempts)
            self.assertGreater(receipt['elapsed_seconds'], 0)
