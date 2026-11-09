"""Pure checks for the standalone, offline staging Compose contract."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def validate(config, *, ephemeral=False):
    if not re.fullmatch(r'trueca-chatbot-(?:staging|validation-[a-z0-9]+)', config.get('name', '')):
        raise ValueError('Use the independent staging/validation project name.')
    services = config['services']
    if set(services) != {'app', 'staging_db', 'proxy'}:
        raise ValueError('Unexpected services; never merge operational Compose overrides.')
    if not config['networks']['staging_private'].get('internal'):
        raise ValueError('Staging private network must prohibit external egress.')
    if set(config['networks']) != {'staging_private', 'staging_ingress'}:
        raise ValueError('Unexpected staging network.')
    for name, service in services.items():
        expected_networks = {'staging_private', 'staging_ingress'} if name == 'proxy' else {'staging_private'}
        if service.get('container_name') or set(service.get('networks', {})) != expected_networks:
            raise ValueError('Unexpected container name or network.')
        for mount in service.get('volumes', []):
            if mount.get('type') == 'volume':
                if name == 'proxy' and (mount.get('source') != 'staging_static' or not mount.get('read_only')):
                    raise ValueError('Proxy may only read the collected static volume.')
                continue
            if (name == 'proxy' and mount.get('type') == 'bind' and mount.get('read_only')
                    and Path(mount['source']).resolve() == ROOT / 'deploy/staging/nginx.conf'
                    and mount.get('target') == '/etc/nginx/nginx.conf'):
                continue
            raise ValueError('Source/operational bind mounts are forbidden.')
    if any(item.get('external') for item in list(config['networks'].values()) + list(config.get('volumes', {}).values())):
        raise ValueError('External/shared Docker resources are forbidden.')
    app, db, proxy = services['app'], services['staging_db'], services['proxy']
    if any(not isinstance(mount, str) or not mount.startswith('/') for mount in app.get('tmpfs', [])):
        raise ValueError('Invalid tmpfs mount specification.')
    if db.get('ports') or app.get('ports'):
        raise ValueError('Only the credential-free proxy may publish a host port.')
    if ephemeral and db.get('volumes'):
        raise ValueError('Validation database must use tmpfs, not persistent volumes.')
    if proxy.get('secrets') or not proxy.get('read_only') or proxy.get('user') != '101:101':
        raise ValueError('Proxy must be unprivileged and have no secrets.')
    ports = proxy.get('ports', [])
    if (len(ports) != 1 or ports[0]['host_ip'] != '127.0.0.1'
            or ports[0]['published'] in ('8000', '8082', '9000')):
        raise ValueError('Use an independent loopback-only HTTP port.')
    env = app['environment']
    if (not app.get('read_only') or app.get('user') != '10001:10001'
            or 'ALL' not in app.get('cap_drop', []) or 'no-new-privileges:true' not in app.get('security_opt', [])):
        raise ValueError('Staging web container hardening is missing.')
    if (env.get('DJANGO_SETTINGS_MODULE') != 'tenetrueca.staging_settings'
            or env.get('CHATBOT_PROVIDER') != 'mock' or env.get('CHATBOT_EVAL_ALLOW_LIVE') != 'False'
            or env.get('OPENAI_API_KEY') or env.get('OPENAI_LIVE_ENABLED') != 'False'):
        raise ValueError('Staging web AI activation is forbidden.')
    for secret in config['secrets'].values():
        path = Path(secret['file']).resolve()
        if ROOT == path or ROOT in path.parents or path.name in ('.env', 'local_settings.py'):
            raise ValueError('Provide new secrets outside the source checkout.')
    volume_names = {v['name'] for v in config.get('volumes', {}).values()}
    network_names = {v['name'] for v in config['networks'].values()}
    for kind, values in [('volume', volume_names), ('network', network_names)]:
        if any(not v.startswith(config['name'] + '_') for v in values):
            raise ValueError(f'Unexpected shared {kind}.')
    return volume_names, network_names

