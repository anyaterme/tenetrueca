"""Read-only Compose and Docker isolation preflight. Does not start or stop anything."""
import argparse
import json
from pathlib import Path
import sys
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chatbot.staging_isolation import validate



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--project', default='trueca-chatbot-staging')
    parser.add_argument('--ephemeral', action='store_true')
    args = parser.parse_args()
    command = ['docker', 'compose', '--env-file', args.env_file, '-p', args.project,
               '-f', str(ROOT / 'compose.staging.yml')]
    if args.ephemeral:
        command += ['-f', str(ROOT / 'compose.staging.ephemeral.yml')]
    config = json.loads(subprocess.check_output(command + ['config', '--format', 'json'], cwd=ROOT))
    app = config['services'].get('app', {})
    if app.get('build') or not re.fullmatch(r'.+@sha256:[a-f0-9]{64}', app.get('image', '')):
        raise ValueError('Staging must execute an already built candidate by immutable digest, without build metadata.')
    if app.get('platform') != 'linux/amd64':
        raise ValueError('The Ubuntu VPS requires the AMD64 candidate platform.')
    actual = subprocess.check_output(['docker','image','inspect','--format','{{.Os}}/{{.Architecture}}', app['image']], text=True).strip()
    if actual != 'linux/amd64':
        raise ValueError('The selected candidate image is not linux/amd64; do not promote it to the VPS.')
    volumes, networks = validate(config, ephemeral=args.ephemeral)
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True).split()
    if ids:
        # Project/mount/network metadata only; never fetch Config.Env or credentials.
        projection = '{"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}},"project":{{json (index .Config.Labels "com.docker.compose.project")}}}'
        raw = subprocess.check_output(['docker', 'inspect', '--format', projection, *ids], text=True)
        containers = [json.loads(line) for line in raw.splitlines() if line.strip()]
        for item in containers:
            if item['project'] == args.project:
                continue
            used_volumes = {m.get('Name') for m in item.get('mounts', [])}
            used_networks = set(item.get('networks', {}))
            if used_volumes & volumes or used_networks & networks:
                raise ValueError('Staging resources overlap a different Docker project.')
    print('Isolation preflight passed: independent project, networks, volumes, secrets and loopback port.')


if __name__ == '__main__':
    main()
