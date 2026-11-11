#!/usr/bin/env python3
"""Promote the frozen E34 image, or roll back app/static files; never operate a worker.

Python standard library and the existing Docker/Git/tar CLIs only. No action on import.
Do not use with Compose overrides other than the base and compose.release-e34.yml.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from uuid import uuid4

DIGEST = 'sha256:dd28d44c918e3359c1d5b83ce64ec5be2447929641f798b957d60dd7f51814a4'
IMAGE = 'trueca-candidate@' + DIGEST
BASE_COMMIT = '2b3e1ac0bff6be219f06027ddd0b0511a764fd34'
ALLOWED_MIGRATIONS = {'chatbot.0001_initial', 'chatbot.0002_chatmessage_sources', 'chatbot.0003_evaluation_budget'}
HERE = Path(__file__).resolve().parent
HEALTH_CODE = "import os,http.client; c=http.client.HTTPConnection('127.0.0.1',8000,timeout=5); c.request('GET','/health/',headers={'Host':os.environ['TRUECA_E34_HEALTH_HOST'],'X-Forwarded-Proto':'https'}); assert c.getresponse().status==200"

# Sent over stdin; this diagnostic is not mounted over or installed into the image.
PROBE = r'''
import hashlib,json,os
from pathlib import Path
import django
django.setup()
from django.conf import settings as s
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
root=Path('/app')
expected=json.loads((root/'chatbot/staging_migrations.json').read_text())
actual={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*/migrations/*.py')}
assert actual==expected, 'Migration file manifest differs'
assert s.DEBUG is False and s.SECRET_KEY!='unsafe-development-placeholder' and len(s.SECRET_KEY)>=32, 'Unsafe Django settings'
assert os.environ['TRUECA_E34_HEALTH_HOST'] in s.ALLOWED_HOSTS, 'Health hostname must be explicitly allowed'
assert s.DATABASES['default']['ENGINE']=='django.db.backends.postgresql', 'PostgreSQL required'
assert s.DATABASES['default']['HOST']=='db', 'Expected existing db service'
assert s.SESSION_ENGINE in ('django.contrib.sessions.backends.db','django.contrib.sessions.backends.cached_db'), 'Database sessions required'
assert s.CHATBOT_ENABLED is False and s.CHATBOT_PROVIDER=='mock' and not s.CHATBOT_EVAL_ALLOW_LIVE, 'Initial deployment must be inert'
for field,path in (('STATIC_ROOT','/app/staticfiles'),('MEDIA_ROOT','/app/media'),('PRIVATE_MEDIA_ROOT','/app/private_media')):
    assert str(getattr(s,field))==path, 'Unexpected data root'
    assert os.access(path,os.R_OK|os.W_OK|os.X_OK), 'UID 10001 cannot access data root'
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(),current_user')
    identity=cursor.fetchone()
loader=MigrationExecutor(connection)
loader.loader.check_consistent_history(connection)
plan=loader.migration_plan(loader.loader.graph.leaf_nodes())
assert all(not backwards for _,backwards in plan), 'Backward migrations forbidden'
print(json.dumps({'identity':identity,'migrations':actual,'pending':[m.app_label+'.'+m.name for m,_ in plan]}))
'''

MANAGE = r'''
import os,sys
os.environ['CI']='true'
import django
django.setup()
from django.conf import settings
settings.OPENAI_API_KEY=''
settings.OPENAI_LIVE_ENABLED=False
settings.OPENAI_EVALUATION_BACKEND='disabled'
settings.CHATBOT_ENABLED=False
settings.CHATBOT_EVAL_ALLOW_LIVE=False
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py']+sys.argv[1:])
'''


class ReleaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w') as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run(args, *, data=None, output=None, env=None):
    """Do not print stderr, environment, Compose JSON, settings or SDK bodies."""
    result = subprocess.run(args, input=data, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise ReleaseError('Command failed: ' + args[0] + ' (exit ' + str(result.returncode) +
                           '). Review the step/configuration; output hidden to protect secrets.')
    return result.stdout or b''


def inspect(identifier, *, optional=False):
    result = subprocess.run(['docker', 'container', 'inspect', identifier], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if result.returncode:
        require(optional, 'Required container does not exist: ' + identifier)
        return None
    return json.loads(result.stdout)[0]


def worker_fingerprint(container):
    if container is None:
        return None
    result = {key: container[key] for key in ('Id', 'Image', 'Mounts', 'RestartCount')}
    result.update({
        'state': {k: container['State'].get(k) for k in ('Status', 'StartedAt', 'FinishedAt')},
        'networks': container['NetworkSettings']['Networks'],
    })
    return result


def literal_compose(value):
    # Resolved secrets may contain dollars. Never interpolate them a second time.
    if isinstance(value, str):
        return value.replace('$', '$$')
    if isinstance(value, list):
        return [literal_compose(v) for v in value]
    if isinstance(value, dict):
        return {k: literal_compose(v) for k, v in value.items()}
    return value


def rollback_command(command):
    """Only remove the known startup migrate wrapper; never migrate on rollback."""
    if command and command[0] == 'gunicorn' and command[1:2] == ['tenetrueca.wsgi:application']:
        return command
    if len(command or []) == 3 and command[:2] in (['sh', '-c'], ['/bin/sh', '-c']):
        match = re.fullmatch(r'\s*python(?:3)?\s+manage\.py\s+migrate\s*&&\s*(gunicorn\s+tenetrueca\.wsgi:application\b[\w\s.:=/-]+)', command[2])
        if match:
            import shlex
            return shlex.split(match[1])
    raise ReleaseError('Unrecognized previous startup command; review rollback before deploying.')


def rollback_model(container, project):
    """Snapshot the actual app, including private resolved env, not guessed old YAML."""
    cfg, host = container['Config'], container['HostConfig']
    require(not cfg.get('Entrypoint'), 'Custom app entrypoint requires a reviewed rollback recipe.')
    service = {'image': container['Image'], 'pull_policy': 'never',
               'container_name': container['Name'].lstrip('/'),
               'command': rollback_command(cfg.get('Cmd')), 'entrypoint': cfg.get('Entrypoint') or [],
               'environment': dict(v.split('=', 1) for v in cfg.get('Env', [])),
               'working_dir': cfg.get('WorkingDir') or '/app', 'user': cfg.get('User') or '',
               'restart': host['RestartPolicy']['Name'] or 'no',
               'read_only': host.get('ReadonlyRootfs', False), 'volumes': [], 'networks': {}, 'ports': []}
    require(not host.get('Privileged') and not host.get('Devices') and not host.get('Links')
            and container['Mounts'], 'Unsupported original app container/mounts.')
    volumes, networks = {}, {}
    for index, mount in enumerate(container['Mounts']):
        require(mount['Type'] in ('bind', 'volume'), 'Unsupported rollback mount type.')
        entry = {'type': mount['Type'], 'target': mount['Destination'], 'read_only': not mount['RW']}
        if mount['Type'] == 'volume':
            alias = 'saved_volume_' + str(index)
            volumes[alias] = {'external': True, 'name': mount['Name']}
            entry['source'] = alias
        else:
            entry.update(source=mount['Source'], bind={'create_host_path': False})
        service['volumes'].append(entry)
    for index, (name, attributes) in enumerate(container['NetworkSettings']['Networks'].items()):
        alias = 'saved_network_' + str(index)
        networks[alias] = {'external': True, 'name': name}
        service['networks'][alias] = {'aliases': attributes.get('Aliases') or []}
        ipam = attributes.get('IPAMConfig') or {}
        for source, target in (('IPv4Address', 'ipv4_address'), ('IPv6Address', 'ipv6_address')):
            if ipam.get(source):
                service['networks'][alias][target] = ipam[source]
    for port, bindings in (host.get('PortBindings') or {}).items():
        target, protocol = port.split('/')
        for binding in bindings or []:
            service['ports'].append({'target': int(target), 'published': binding['HostPort'],
                                     'host_ip': binding['HostIp'] or '0.0.0.0', 'protocol': protocol})
    for source, target in (('CapDrop', 'cap_drop'), ('CapAdd', 'cap_add'), ('SecurityOpt', 'security_opt')):
        if host.get(source):
            service[target] = host[source]
    if host.get('Tmpfs'):
        service['tmpfs'] = [path + ':' + options for path, options in host['Tmpfs'].items()]
    service['labels'] = {k: v for k, v in (cfg.get('Labels') or {}).items() if not k.startswith('com.docker.compose.')}
    for source, target in (('ExtraHosts', 'extra_hosts'), ('Dns', 'dns'), ('DnsSearch', 'dns_search'), ('DnsOptions', 'dns_opt')):
        if host.get(source):
            service[target] = host[source]
    if host.get('LogConfig', {}).get('Type'):
        service['logging'] = {'driver': host['LogConfig']['Type'], 'options': host['LogConfig'].get('Config') or {}}
    if host.get('RestartPolicy', {}).get('MaximumRetryCount'):
        service['restart'] += ':' + str(host['RestartPolicy']['MaximumRetryCount'])
    if cfg.get('Healthcheck'):
        hc = cfg['Healthcheck']
        service['healthcheck'] = {'test': hc['Test']}
        for source, target in (('Interval', 'interval'), ('Timeout', 'timeout'), ('StartPeriod', 'start_period')):
            if hc.get(source):
                service['healthcheck'][target] = str(hc[source]) + 'ns'
        if hc.get('Retries'):
            service['healthcheck']['retries'] = hc['Retries']
    return literal_compose({'name': project, 'services': {'app': service}, 'networks': networks, 'volumes': volumes})


def validate_model(model, root, image):
    app = model['services']['app']
    require(app.get('image') == image and not app.get('build') and app.get('pull_policy') == 'never',
            'Compose must use only the frozen local image, without build/pull.')
    require(app.get('platform') == 'linux/amd64' and app.get('user') == '10001:10001'
            and app.get('read_only'), 'Unexpected architecture, UID or filesystem policy.')
    expected = {'/app/tenetrueca/local_settings.py': root / 'app/tenetrueca/local_settings.py',
                '/app/media': root / 'app/media', '/app/private_media': root / 'app/private_media',
                '/app/staticfiles': root / 'app/staticfiles'}
    mounts = app.get('volumes', [])
    require(len(mounts) == len(expected) and {m['target'] for m in mounts} == set(expected),
            'Unexpected release mounts; source overlays are forbidden.')
    for mount in mounts:
        require(mount['type'] == 'bind' and mount['target'] in expected
                and Path(mount['source']).resolve() == expected[mount['target']].resolve(), 'Unexpected data/config mount.')
        require(mount.get('bind', {}).get('create_host_path') is False, 'Implicit host directory creation forbidden.')
        if mount['target'].endswith('local_settings.py'):
            require(mount.get('read_only'), 'Operational settings must be read-only.')
    env = app['environment']
    require(env.get('CHATBOT_ENABLED') == 'False' and env.get('CHATBOT_PROVIDER') == 'mock'
            and env.get('CHATBOT_EVAL_ALLOW_LIVE') == 'False', 'Initial promotion must not enable paid traffic.')
    command = app['command']
    require(command[:2] == ['gunicorn', 'tenetrueca.wsgi:application'], 'Only Gunicorn may start the app.')


def preserved_paths(container, root):
    """The new bind mounts must resolve to the same data/config as the existing app."""
    for relative in ('tenetrueca/local_settings.py', 'media', 'private_media', 'staticfiles'):
        destination = '/app/' + relative
        covers = [m for m in container['Mounts'] if destination == m['Destination']
                  or destination.startswith(m['Destination'].rstrip('/') + '/')]
        require(covers, 'Cannot prove the current data/config path: ' + destination)
        mount = max(covers, key=lambda m: len(m['Destination']))
        require(mount['Type'] == 'bind', 'Existing named data volume requires a reviewed override.')
        suffix = destination[len(mount['Destination']):].lstrip('/')
        require((Path(mount['Source']) / suffix).resolve() == (root / 'app' / relative).resolve(),
                'The release would change an existing data/config path: ' + destination)


class Deployment:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.root).resolve()
        self.override = HERE.parent / 'compose.release-e34.yml'
        self.env = dict(os.environ, TRUECA_E34_IMAGE=args.image, TRUECA_E34_HEALTH_HOST=args.health_host,
                        COMPOSE_REMOVE_ORPHANS='0', COMPOSE_IGNORE_ORPHANS='1', COMPOSE_PROFILES='')
        self.backup = None
        self.state = {}
        self.step = 'preflight'
        self.mutated = False

    def compose(self, *args):
        return ['docker', 'compose', '--project-directory', str(self.root), '--env-file', str(self.root / '.env'),
                '-p', self.project, '-f', str(self.root / 'docker-compose.yml'), '-f', str(self.override), *args]

    def dc(self, *args, data=None):
        return run(self.compose(*args), data=data, env=self.env)

    def manage(self, *args):
        return self.dc('run', '--rm', '-T', '--no-deps', '--pull', 'never', '-e', 'CI=true',
                       '-e', 'OPENAI_API_KEY=', '-e', 'OPENAI_LIVE_ENABLED=False',
                       '-e', 'OPENAI_EVALUATION_BACKEND=disabled', 'app', 'python', '-c', MANAGE, *args)

    def checkpoint(self, status):
        self.state.update(status=status, step=self.step)
        if self.backup:
            atomic_json(self.backup / 'state.json', self.state)

    def check_worker(self):
        require(self.workers() == self.worker,
                'Moderation worker changed externally; stop and review without modifying it.')

    def workers(self):
        identifiers = set(run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + self.project,
                               '--filter', 'label=com.docker.compose.service=evaluation_worker']).decode().split())
        named = inspect(self.args.worker_container, optional=True)
        if named:
            require((named['Config'].get('Labels') or {}).get('com.docker.compose.project') == self.project,
                    'Named moderation worker belongs to a different project.')
            identifiers.add(named['Id'])
        containers = [inspect(identifier) for identifier in sorted(identifiers)]
        return {c['Id']: worker_fingerprint(c) for c in containers}

    def preflight(self):
        require(self.args.image.endswith('@' + DIGEST), 'Only the validated E34 digest is allowed.')
        require(re.fullmatch(r'[A-Za-z0-9.-]+', self.args.health_host) is not None, 'Expected a plain destination hostname.')
        for command in ('docker', 'git', 'tar'):
            require(shutil.which(command), 'Required executable missing: ' + command)
        self.local_docker()
        require(run(['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}']).decode().strip()
                in ('linux/x86_64', 'linux/amd64'), 'Destination Docker host must be Linux AMD64.')
        require((self.root / '.env').is_file() and (self.root / 'docker-compose.yml').is_file(), 'Invalid deployment root.')
        require((self.root / 'app').is_dir() and not (self.root / 'app').is_symlink()
                and not (self.root / 'backups').is_symlink(), 'Checkout/backups symlinks require a reviewed backup recipe.')
        self.original = inspect(self.args.app_container)
        self.db = inspect(self.args.db_container)
        require(self.original['State']['Running'], 'Current app is not running; diagnose before promotion.')
        labels = self.original['Config'].get('Labels') or {}
        self.project = labels.get('com.docker.compose.project')
        require(self.project and labels.get('com.docker.compose.service') == 'app', 'App must belong to the expected Compose service.')
        require((self.db['Config'].get('Labels') or {}).get('com.docker.compose.project') == self.project
                and self.db['State']['Running'], 'Existing DB must belong to the same project and be running.')
        self.worker = self.workers()
        require(all(w['state']['Status'] in ('exited', 'created') for w in self.worker.values()),
                'Moderation worker is active. This script will not stop it; arrange a separate maintenance window.')
        revision = run(['git', '-C', str(self.root / 'app'), 'rev-parse', 'HEAD']).decode().strip()
        require(revision == self.args.expected_current_commit, 'Host checkout differs from expected current commit.')
        require(not run(['git', '-C', str(self.root / 'app'), 'status', '--porcelain']).strip(), 'Host checkout is dirty.')
        version = run(['docker', 'compose', 'version', '--short']).decode()
        numbers = re.search(r'(\d+)\.(\d+)\.(\d+)', version)
        require(numbers and tuple(map(int, numbers.groups())) >= (2, 24, 4), 'Docker Compose >=2.24.4 required.')
        image = json.loads(run(['docker', 'image', 'inspect', self.args.image]))[0]
        require(image['Architecture'] == 'amd64' and image['Os'] == 'linux'
                and image['Config'].get('User') == '10001:10001', 'Unexpected candidate image metadata.')
        model = json.loads(self.dc('config', '--format', 'json'))
        validate_model(model, self.root, self.args.image)
        require(str(model['services']['app']['environment'].get('CI', '')).lower() not in ('true', '1', 'yes'),
                'CI must not be enabled in the persistent app.')
        actual_networks = set(self.original['NetworkSettings']['Networks'])
        planned_networks = {model['networks'][key]['name'] for key in model['services']['app']['networks']}
        require(planned_networks == actual_networks, 'Release networks differ from the current app.')
        current_ports = self.original['HostConfig'].get('PortBindings') or {}
        current_ports = {key: sorted(({'HostIp': item.get('HostIp') or '0.0.0.0', 'HostPort': item['HostPort']}
                                     for item in value or []), key=lambda v: (v['HostIp'], v['HostPort']))
                         for key, value in current_ports.items()}
        planned_ports = {}
        for port in model['services']['app'].get('ports', []):
            planned_ports.setdefault(str(port['target']) + '/' + port.get('protocol', 'tcp'), []).append(
                {'HostIp': port.get('host_ip', '0.0.0.0'), 'HostPort': str(port['published'])})
        planned_ports = {key: sorted(value, key=lambda v: (v['HostIp'], v['HostPort'])) for key, value in planned_ports.items()}
        require(planned_ports == current_ports, 'Release ports differ from the current app; review explicitly.')
        # Shared moderation-related environment must remain unchanged in the web app too.
        previous_env = dict(v.split('=', 1) for v in self.original['Config'].get('Env', []))
        new_env = model['services']['app']['environment']
        keys = {k for k in previous_env.keys() | new_env.keys() if k.startswith(('OPENAI_', 'AI_'))}
        require(all(previous_env.get(k) == new_env.get(k) for k in keys), 'Shared AI environment differs; review the base configuration first.')
        self.rollback = rollback_model(self.original, self.project)
        preserved_paths(self.original, self.root)
        for name in ('media', 'private_media', 'staticfiles'):
            directory = self.root / 'app' / name
            require(directory.is_dir() and not directory.is_symlink(), 'Expected existing data directory: ' + name)
            require(not any(p.is_symlink() for p in directory.rglob('*')), 'Symlinked media/static files need a reviewed backup recipe.')
        self.facts = json.loads(self.dc('run', '--rm', '-T', '--no-deps', '--pull', 'never', '-e', 'CI=true',
                                      '-e', 'OPENAI_API_KEY=', 'app', 'python', '-', data=PROBE.encode()))
        db_env = dict(v.split('=', 1) for v in self.db['Config'].get('Env', []))
        require(self.facts['identity'] == [db_env.get('POSTGRES_DB'), db_env.get('POSTGRES_USER')], 'Django points to an unexpected database/user.')
        require(set(self.facts['pending']) <= ALLOWED_MIGRATIONS, 'Non-chatbot migrations pending; separate review required.')
        for path, digest in self.facts['migrations'].items():
            existing = self.root / 'app' / path
            require(not existing.is_file() or sha(existing) == digest, 'Historical migration differs: ' + path)
        # Restore verification uses a fresh disk-backed anonymous volume, bounded CPU/RAM.
        size_sql = 'exec psql -XAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_database_size(current_database())"'
        database_bytes = int(run(['docker', 'exec', self.db['Id'], 'sh', '-c', size_sql]).strip())
        app_bytes = sum(path.stat().st_size for path in (self.root / 'app').rglob('*')
                        if path.is_file() and not path.is_symlink() and '.git' not in path.parts)
        old_image = json.loads(run(['docker', 'image', 'inspect', self.original['Image']]))[0]
        require(shutil.disk_usage(self.root).free > database_bytes * 3 + app_bytes * 2 + old_image['Size'] * 2 + 256 * 1024**2,
                'Insufficient free disk for database restore, files and previous image backups.')
        self.image_id = image['Id']
        print('Preflight OK: frozen AMD64 image, existing DB, inert chatbot, known migrations; worker unchanged.')

    def local_docker(self):
        require(sys.platform.startswith('linux'), 'Run the deployment tooling locally on the Linux destination.')
        # Docker gives an explicit context priority over DOCKER_HOST.
        context = os.environ.get('DOCKER_CONTEXT')
        if context:
            endpoint = run(['docker', 'context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}']).decode().strip()
        else:
            endpoint = os.environ.get('DOCKER_HOST') or run(
                ['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}']).decode().strip()
        require(endpoint.startswith('unix://'), 'Remote Docker contexts are forbidden: backups must read destination files locally.')

    def prepare_backup(self):
        self.step = 'prepare recovery metadata before stopping app'
        directory = self.root / 'backups' / ('e34-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid4().hex[:8])
        directory.mkdir(parents=True, mode=0o700)
        self.backup = directory
        atomic_json(directory / 'rollback.compose.json', self.rollback)
        atomic_json(directory / 'migration-plan.json', self.facts)
        self.state = {'root': str(self.root), 'project': self.project, 'previous_image': self.original['Image'],
                      'previous_container': self.original['Id'], 'candidate': self.args.image,
                      'candidate_id': self.image_id, 'worker': self.worker,
                      'worker_container': self.args.worker_container, 'app_container': self.args.app_container,
                      'db_container': self.args.db_container, 'db_id': self.db['Id'],
                      'revision': self.args.expected_current_commit, 'health_host': self.args.health_host,
                      'migrations_started': False, 'statics_started': False, 'backup_verified': False}
        for path in ('.env', 'docker-compose.yml', 'app/tenetrueca/local_settings.py'):
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(self.root / path, target)
            os.chmod(target, 0o600)
        self.state['recovery_hashes'] = {path: sha(directory / path) for path in
            ('rollback.compose.json', '.env', 'docker-compose.yml', 'app/tenetrueca/local_settings.py')}
        self.checkpoint('recovery_metadata_ready')

    def backup_files(self):
        self.step = 'backup'
        directory = self.backup
        self.checkpoint('backing_up')
        with (directory / 'previous-image.tar').open('wb') as output:
            run(['docker', 'image', 'save', self.original['Image']], output=output)
        with (directory / 'app.tar.gz').open('wb') as output:
            run(['tar', '-C', str(self.root), '--exclude=app/.git', '--exclude=app/.venv', '--exclude=app/venv',
                 '--exclude=__pycache__', '-czf', '-', 'app'], output=output)
        with (directory / 'staticfiles.tar').open('wb') as output:
            run(['tar', '-C', str(self.root / 'app/staticfiles'), '-cf', '-', '.'], output=output)
        with (directory / 'database.dump').open('wb') as output:
            run(['docker', 'exec', self.db['Id'], 'sh', '-c',
                 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom'], output=output)
        require((directory / 'database.dump').stat().st_size > 0, 'Empty PostgreSQL backup.')
        with (directory / 'database.dump').open('rb') as source:
            result = subprocess.run(['docker', 'exec', '-i', self.db['Id'], 'pg_restore', '--list'],
                                    stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        require(result.returncode == 0, 'Backup catalog invalid.')
        (directory / 'database-list.txt').write_bytes(result.stdout)
        self.verify_restore()
        paths = [p for p in directory.rglob('*') if p.is_file() and p.name != 'state.json']
        for path in paths:
            with path.open('rb') as source:
                os.fsync(source.fileno())
        self.state['backup_hashes'] = {str(p.relative_to(directory)): sha(p) for p in paths}
        self.state['backup_verified'] = True
        self.checkpoint('backup_verified')

    def verify_restore(self):
        self.step = 'isolated backup restore'
        name = 'trueca-e34-restore-' + uuid4().hex[:12]
        identifier = None
        try:
            # Network none, no published ports or operational mounts/passwords.
            identifier = run(['docker', 'run', '--rm', '-d', '--name', name, '--network', 'none', '--pull', 'never',
                              '--memory', '512m', '--cpus', '1', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
                              '-e', 'POSTGRES_DB=restore_check', self.db['Image']]).decode().strip()
            self.state['verification_container'] = identifier
            self.checkpoint('verifying_backup')
            for _ in range(60):
                result = subprocess.run(['docker', 'exec', identifier, 'pg_isready', '-U', 'postgres', '-d', 'restore_check'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise ReleaseError('Isolated backup verification database did not become ready.')
            with (self.backup / 'database.dump').open('rb') as source:
                result = subprocess.run(['docker', 'exec', '-i', identifier, 'pg_restore', '-U', 'postgres',
                                         '-d', 'restore_check', '--no-owner', '--no-privileges', '--exit-on-error'],
                                        stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            require(result.returncode == 0, 'Restore verification failed; live database was not migrated.')
        finally:
            if identifier:
                run(['docker', 'rm', '-f', '-v', identifier])
                self.state['verification_container'] = None

    def deploy(self):
        self.preflight()
        require(self.original['Image'] != self.image_id, 'E34 image already runs here; this tool is not a configuration updater.')
        self.check_worker()
        self.prepare_backup()
        self.step = 'stop app only'
        self.mutated = True
        # No compose stop/down: only the exact previously inspected app ID.
        run(['docker', 'stop', '--time', '150', self.original['Id']])
        self.backup_files()
        self.check_worker()
        self.step = 'explicit forward migrations'
        self.state['migrations_started'] = True
        self.checkpoint('migrating')
        self.manage('check')
        self.manage('migrate', '--plan')
        self.manage('migrate', '--noinput')
        self.manage('migrate', '--check')
        self.step = 'collectstatic'
        self.state['statics_started'] = True
        self.checkpoint('collecting_statics')
        self.manage('collectstatic', '--noinput')
        self.step = 'start frozen app only'
        self.checkpoint('starting')
        try:
            self.dc('up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '120', 'app')
        finally:
            # Capture this exact replacement even when Compose reports a health failure.
            replacement = inspect(self.args.app_container, optional=True)
            if (replacement and replacement['Image'] == self.image_id
                    and (replacement['Config'].get('Labels') or {}).get('com.docker.compose.project') == self.project):
                self.state['active_container'] = replacement['Id']
                self.checkpoint('starting')
        active = inspect(self.args.app_container)
        require(active['Image'] == self.image_id, 'Running image differs from validated candidate.')
        self.state['active_container'] = active['Id']
        self.checkpoint('checking')
        run(['docker', 'exec', active['Id'], 'python', '-c', HEALTH_CODE])
        self.check_worker()
        require(inspect(self.args.db_container)['Id'] == self.db['Id'], 'Existing DB container changed.')
        self.step = 'completed'
        self.checkpoint('deployed_chatbot_disabled')
        print('Deployment complete; chatbot disabled, no OpenAI probes. Backup: ' + str(self.backup))

    def roll_back(self):
        self.local_docker()
        self.backup = Path(self.args.backup).resolve()
        require(self.backup.parent == self.root / 'backups' and self.backup.name.startswith('e34-'), 'Unexpected rollback directory.')
        self.state = json.loads((self.backup / 'state.json').read_text())
        require(self.state['root'] == str(self.root) and self.state['status'] != 'rolled_back', 'Invalid or already used rollback state.')
        self.project, self.worker = self.state['project'], self.state['worker']
        require(self.args.worker_container == self.state['worker_container']
                and self.args.app_container == self.state['app_container']
                and self.args.db_container == self.state['db_container'], 'Container selection differs from backup.')
        self.check_worker()
        require(inspect(self.args.db_container)['Id'] == self.state['db_id'], 'DB container differs from saved deployment.')
        require(self.state.get('recovery_hashes'), 'Incomplete recovery metadata; manual intervention required.')
        for name, digest in self.state['recovery_hashes'].items():
            require(sha(self.backup / name) == digest, 'Recovery metadata integrity failed.')
        require(not self.state['migrations_started'] or self.state['backup_verified'], 'Migration backup is not verified.')
        for name, digest in self.state.get('backup_hashes', {}).items():
            require(sha(self.backup / name) == digest, 'Backup integrity failed: ' + name)
        for path in ('.env', 'docker-compose.yml', 'app/tenetrueca/local_settings.py'):
            require(sha(self.root / path) == sha(self.backup / path), 'Configuration changed; manual reconciliation required: ' + path)
        require(run(['git', '-C', str(self.root / 'app'), 'rev-parse', 'HEAD']).decode().strip() == self.state['revision'],
                'Host source changed since backup; do not restore over current work.')
        require(not run(['git', '-C', str(self.root / 'app'), 'status', '--porcelain']).strip(), 'Host source is dirty; manual rollback review required.')
        current = inspect(self.args.app_container, optional=True)
        if current:
            expected_id = self.state.get('active_container')
            require(current['Id'] in (self.state['previous_container'], expected_id),
                    'A later/unrelated deployment is active; automatic replacement forbidden.')
        self.mutated = True
        if current:
            run(['docker', 'stop', '--time', '150', current['Id']])
        self.step = 'rollback app and static files (database retained)'
        if self.state['statics_started']:
            require(not any(p.is_symlink() for p in (self.root / 'app/staticfiles').rglob('*')), 'Unexpected static symlinks; manual review required.')
            run(['tar', '-C', str(self.root / 'app/staticfiles'), '-xf', str(self.backup / 'staticfiles.tar')])
        image = subprocess.run(['docker', 'image', 'inspect', self.state['previous_image']],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if image.returncode:
            require('previous-image.tar' in self.state.get('backup_hashes', {}), 'Previous image unavailable; manual recovery required.')
            run(['docker', 'image', 'load', '-i', str(self.backup / 'previous-image.tar')])
        # JSON is a valid Compose document; it contains only app plus existing external resources.
        run(['docker', 'compose', '-p', self.project, '-f', str(self.backup / 'rollback.compose.json'),
             'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'app'], env=self.env)
        restored = inspect(self.args.app_container)
        require(restored['Image'] == self.state['previous_image'], 'Rollback image differs.')
        for _ in range(30):
            result = subprocess.run(['docker', 'exec', '-e', 'TRUECA_E34_HEALTH_HOST=' + self.state['health_host'],
                                     restored['Id'], 'python', '-c', HEALTH_CODE],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                break
            time.sleep(2)
        else:
            raise ReleaseError('Previous app health failed; keep maintenance enabled and review.')
        self.check_worker()
        self.step = 'rolled back; database not restored'
        self.checkpoint('rolled_back')
        print('Previous app restored, old static files overlaid. Database and media retained; verify public health before reopening traffic.')


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('action', choices=('preflight', 'deploy', 'rollback'))
    result.add_argument('--root', required=True)
    result.add_argument('--health-host', required=True)
    result.add_argument('--image', default=IMAGE)
    result.add_argument('--expected-current-commit', default=BASE_COMMIT)
    result.add_argument('--app-container', default='tenetrueca_app')
    result.add_argument('--db-container', default='tenetrueca_db')
    result.add_argument('--worker-container', default='tenetrueca_evaluation_worker')
    result.add_argument('--backup')
    result.add_argument('--maintenance-confirmed', action='store_true',
                        help='Confirm external writers/traffic have been quiesced; this script never stops workers.')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    require(args.action == 'preflight' or args.maintenance_confirmed, 'Explicit maintenance confirmation required for changes.')
    require(args.action != 'rollback' or args.backup, 'Rollback requires --backup.')
    deployment = Deployment(args)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    if args.action == 'preflight':
        deployment.preflight()
        return
    os.umask(0o077)
    with (deployment.root / '.update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError('Another deployment holds .update.lock.') from None
        try:
            if args.action == 'deploy':
                deployment.deploy()
            else:
                deployment.roll_back()
        except (Exception, KeyboardInterrupt):
            if deployment.backup and deployment.mutated:
                print('Stopped at ' + deployment.step + '. Backup/state: ' + str(deployment.backup), file=sys.stderr)
                try:
                    deployment.checkpoint('failed')
                except OSError:
                    print('Could not update state; review the last durable checkpoint before recovery.', file=sys.stderr)
            raise


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) if isinstance(error, ReleaseError) else 'interrupted' if isinstance(error, KeyboardInterrupt) else type(error).__name__
        print('Stopped: ' + reason +
              '. No automatic database rollback or worker action.', file=sys.stderr)
        sys.exit(1)
