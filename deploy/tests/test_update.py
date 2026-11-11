"""Exercise the deployment shell in disposable trees, without Docker, Git remotes or APIs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'update.sh'
FAKE_COMMAND = '''import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['TRUECA_TEST_LOG'], 'a') as stream:
    stream.write(json.dumps([name, *args]) + '\\n')
case = os.environ['TRUECA_TEST_CASE']
if name == 'flock':
    sys.exit(1 if case == 'locked' else 0)
if name == 'git':
    args = args[2:]
    if args[0] == 'status' and case == 'dirty': print(' M owned-change.py')
    if args[0] == 'rev-parse': print('previous-revision')
    if args[0] == 'merge-base' and case == 'diverged': sys.exit(1)
    if args[0] == 'check-ignore': sys.exit(0 if case == 'collision' else 1)
    if args[0] == 'ls-tree': print('assistant/migrations/0001_initial.py')
    if args[0] == 'show': print('different migration')
    if args[0] == 'merge':
        created = Path('app/static/js/new_release.js')
        created.write_text('// mock new asset')
        with open(os.environ['TRUECA_TEST_LOG'], 'a') as stream:
            stream.write(json.dumps(['created_mode', created.stat().st_mode & 0o777]) + '\\n')
if name == 'docker':
    if args[:2] == ['inspect', '-f']: print('true')
    if args[:2] == ['exec', 'tenetrueca_db']: print('mock backup dump')
    if 'run' in args:
        if args[-1] == '-':
            assert 'CI=true' in args
            sys.stdin.read()
            if case == 'invalid_config': sys.exit(1)
        else:
            for guard in ['CI=true', 'OPENAI_EVALUATION_BACKEND=disabled', 'OPENAI_LIVE_ENABLED=False', 'OPENAI_API_KEY=']:
                assert guard in args, guard
            if case == 'migration_failure' and args[-2:] == ['migrate', '--noinput']: sys.exit(42)
'''


@unittest.skipUnless(shutil.which('bash') and shutil.which('tar'), 'Requires Bash and tar')
class DeploymentScriptTests(unittest.TestCase):
    def test_success_and_failures_protect_services_secrets_and_public_permissions(self):
        with tempfile.TemporaryDirectory(prefix='trueca-deploy-shell-') as temporary:
            base = Path(temporary)
            binaries = base / 'bin'
            binaries.mkdir()
            for name in ('docker', 'git', 'flock'):
                executable = binaries / name
                executable.write_text('#!' + sys.executable + '\n' + FAKE_COMMAND)
                executable.chmod(0o755)
            cases = ('success', 'dirty', 'locked', 'diverged', 'collision',
                     'missing_migration', 'invalid_config', 'migration_failure')
            for case in cases:
                with self.subTest(case=case):
                    root = base / case
                    (root / 'app/.git').mkdir(parents=True)
                    secret = root / '.env'
                    secret.write_text('secret-not-printed')
                    secret.chmod(0o600)
                    (root / 'docker-compose.yml').write_text('services: {}')
                    private = root / 'app/private_media/original.jpg'
                    private.parent.mkdir(mode=0o700)
                    private.write_bytes(b'private-original')
                    private.chmod(0o600)
                    for directory in ('static/css', 'static/js', 'staticfiles/css'):
                        folder = root / 'app' / directory
                        folder.mkdir(parents=True)
                        folder.chmod(0o700)
                        asset = folder / 'old-asset.txt'
                        asset.write_text('public fixture')
                        asset.chmod(0o600)
                    (root / 'app/static/secret-link').symlink_to(secret)
                    (root / 'app/static/private-link').symlink_to(private.parent, target_is_directory=True)
                    for migration in (
                        'publications/migrations/0002_publicationphoto_file_size_publicationphoto_height_and_more.py',
                        'moderation/migrations/0001_initial.py',
                        'assistant/migrations/0001_initial.py',
                    ):
                        if case == 'missing_migration' and migration.startswith('publications/'):
                            continue
                        path = root / 'app' / migration
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text('original migration')
                    log = root / 'commands.jsonl'
                    environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                                       TRUECA_DEPLOY_ROOT=str(root), TRUECA_TEST_CASE=case,
                                       TRUECA_TEST_LOG=str(log))
                    result = subprocess.run(['bash', str(SCRIPT)], env=environment, text=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    events = [json.loads(row) for row in log.read_text().splitlines()]
                    starts = [event for event in events if 'up' in event and '--force-recreate' in event]
                    if case == 'success':
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual([event[-1] for event in starts], ['app', 'evaluation_worker'])
                        migrated = next(i for i, event in enumerate(events) if event[-2:] == ['migrate', '--noinput'])
                        collected = next(i for i, event in enumerate(events) if event[-2:] == ['collectstatic', '--noinput'])
                        self.assertLess(migrated, collected)
                        self.assertLess(collected, events.index(starts[0]))
                        self.assertIn(['created_mode', 0o644], events)
                        for public_tree in ('static', 'staticfiles'):
                            for path in (root / 'app' / public_tree).rglob('*'):
                                if path.is_symlink():
                                    continue
                                self.assertEqual(path.stat().st_mode & 0o777, 0o755 if path.is_dir() else 0o644)
                        backup = next((root / 'backups').iterdir())
                        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
                        for name in ('database.dump', 'app.tar.gz', '.env'):
                            self.assertEqual((backup / name).stat().st_mode & 0o777, 0o600)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(starts)
                    self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(private.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(private.parent.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(private.read_bytes(), b'private-original')
                    self.assertEqual((root / 'app/static/secret-link').readlink(), secret)
                    self.assertEqual((root / 'app/static/private-link').readlink(), private.parent)
                    self.assertNotIn('secret-not-printed', result.stdout + result.stderr)
                    self.assertFalse(any('test_openai_integration' in str(event) or '--once' in event for event in events))


if __name__ == '__main__':
    unittest.main()
