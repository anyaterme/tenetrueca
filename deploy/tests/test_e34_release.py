"""Pure/offline checks only: never execute the deployment script or Docker/Compose."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'e34_release.py'
spec = importlib.util.spec_from_file_location('release_e34_tooling', MODULE)
release = importlib.util.module_from_spec(spec)
with patch('subprocess.run', side_effect=AssertionError('No subprocess during import')):
    spec.loader.exec_module(release)


def old_container(root):
    return {'Image': 'sha256:old-immutable-id', 'Name': '/tenetrueca_app',
        'Config': {'Env': ['SECRET_KEY=a$b${KEEP}', 'MODE=original'], 'User': '', 'WorkingDir': '/app',
            'Entrypoint': None, 'Labels': {'com.docker.compose.project': 'tenetrueca', 'routing': 'keep'},
            'Cmd': ['sh', '-c', 'python manage.py migrate && gunicorn tenetrueca.wsgi:application --bind 0.0.0.0:8000 --workers 3']},
        'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'},
            'PortBindings': {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8082'}]}},
        'Mounts': [{'Type': 'bind', 'Source': str(root / 'app'), 'Destination': '/app', 'RW': True}],
        'NetworkSettings': {'Networks': {'tenetrueca_network': {'Aliases': ['app']}}}}


def model(root):
    paths = {'tenetrueca/local_settings.py': '/app/tenetrueca/local_settings.py',
             'media': '/app/media', 'private_media': '/app/private_media', 'staticfiles': '/app/staticfiles'}
    return {'services': {'app': {'image': release.IMAGE, 'platform': 'linux/amd64', 'pull_policy': 'never',
        'user': '10001:10001', 'read_only': True, 'command': ['gunicorn', 'tenetrueca.wsgi:application'],
        'environment': {'CHATBOT_ENABLED': 'False', 'CHATBOT_PROVIDER': 'mock', 'CHATBOT_EVAL_ALLOW_LIVE': 'False'},
        'volumes': [{'type': 'bind', 'source': str(root / 'app' / name), 'target': target,
                     'read_only': name.endswith('local_settings.py'), 'bind': {'create_host_path': False}}
                    for name, target in paths.items()]}}}


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('/srv/docker/tenetrueca')
        guard = patch.object(release.subprocess, 'run', side_effect=AssertionError('Subprocesses forbidden in these offline checks'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_roll_back_preserves_old_image_ports_env_and_data_without_migrations(self):
        original = old_container(self.root)
        before = copy.deepcopy(original)
        saved = release.rollback_model(original, 'tenetrueca')
        self.assertEqual(set(saved['services']), {'app'})
        app = saved['services']['app']
        self.assertEqual(app['image'], original['Image'])
        self.assertEqual(app['command'][0], 'gunicorn')
        self.assertNotIn('migrate', app['command'])
        self.assertEqual(app['environment']['SECRET_KEY'], 'a$$b$${KEEP}')
        self.assertEqual(app['ports'][0]['published'], '8082')
        self.assertEqual(app['volumes'][0]['source'], str(self.root / 'app'))
        self.assertTrue(all(n['external'] for n in saved['networks'].values()))
        self.assertEqual(app['labels'], {'routing': 'keep'})
        self.assertEqual(original, before)

    def test_unknown_or_compound_shell_commands_fail_closed(self):
        for command in (['sh', '-c', 'gunicorn app; run_other_action'], ['python', 'manage.py', 'runserver'],
                        ['sh', '-c', 'python manage.py migrate --fake && gunicorn tenetrueca.wsgi:application'],
                        ['sh', '-c', 'python manage.py migrate && gunicorn tenetrueca.wsgi:application && touch /tmp/x']):
            with self.subTest(command=command), self.assertRaises(release.ReleaseError):
                release.rollback_command(command)

    def test_named_rollback_volumes_remain_existing_external_resources(self):
        old = old_container(self.root)
        old['Mounts'].append({'Type': 'volume', 'Name': 'original_media', 'Source': '/docker/volume',
                              'Destination': '/other-data', 'RW': False})
        result = release.rollback_model(old, 'tenetrueca')
        self.assertEqual(result['volumes']['saved_volume_1'], {'external': True, 'name': 'original_media'})
        self.assertTrue(result['services']['app']['volumes'][1]['read_only'])

    def test_release_rejects_build_source_overlay_live_activation_and_missing_bind_guard(self):
        valid = model(self.root)
        release.validate_model(valid, self.root, release.IMAGE)
        for mutate in (lambda a: a.update(build={'context': '.'}),
                       lambda a: a.update(image='trueca:latest'),
                       lambda a: a['environment'].update(CHATBOT_ENABLED='True'),
                       lambda a: a['volumes'][0].update(read_only=False),
                       lambda a: a['volumes'][1]['bind'].update(create_host_path=True),
                       lambda a: a['volumes'][1].update(target='/app'),
                       lambda a: a.update(command=['sh', '-c', 'python manage.py migrate'])):
            candidate = copy.deepcopy(valid)
            mutate(candidate['services']['app'])
            with self.assertRaises(release.ReleaseError):
                release.validate_model(candidate, self.root, release.IMAGE)

    def test_release_cannot_silently_move_original_media(self):
        original = old_container(self.root)
        release.preserved_paths(original, self.root)
        original['Mounts'].append({'Type': 'bind', 'Source': '/external/real-media', 'Destination': '/app/media', 'RW': True})
        with self.assertRaises(release.ReleaseError):
            release.preserved_paths(original, self.root)

    def test_private_atomic_metadata_round_trips_without_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'state.json'
            release.atomic_json(target, {'secret_fixture': 'x${Y}$z'})
            self.assertEqual(json.loads(target.read_text()), {'secret_fixture': 'x${Y}$z'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertFalse(target.with_name('state.json.tmp').exists())

    def test_cli_requires_explicit_action_and_preserves_digest_default(self):
        args = release.parser().parse_args(['preflight', '--root', str(self.root), '--health-host', 'trueca.example'])
        self.assertEqual(args.image, release.IMAGE)
        self.assertFalse(args.maintenance_confirmed)
        self.assertEqual(args.expected_current_commit, release.BASE_COMMIT)

    def test_inherited_compose_options_cannot_remove_workers_as_orphans(self):
        args = release.parser().parse_args(['preflight', '--root', str(self.root), '--health-host', 'trueca.example'])
        with patch.dict('os.environ', {'COMPOSE_REMOVE_ORPHANS': 'true', 'COMPOSE_PROFILES': 'evaluation_worker'}):
            deployment = release.Deployment(args)
        self.assertEqual(deployment.env['COMPOSE_REMOVE_ORPHANS'], '0')
        self.assertEqual(deployment.env['COMPOSE_PROFILES'], '')
        self.assertEqual(deployment.env['COMPOSE_IGNORE_ORPHANS'], '1')


if __name__ == '__main__':
    unittest.main()
