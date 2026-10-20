"""Use the real Supervisor validators; run only in the pinned Supervisor image."""
import json
from pathlib import Path
import sys

from supervisor.apps.options import AppOptions
from supervisor.apps.validate import SCHEMA_APP_CONFIG_QUIET
from supervisor.store.validate import SCHEMA_REPOSITORY_CONFIG
import yaml


def main():
    folder = Path(sys.argv[1])
    root = Path(__file__).resolve().parents[2]
    repositories = [root / name for name in ('repository.yaml', 'repository.yml', 'repository.json')
                    if (root / name).is_file()]
    if len(repositories) != 1:
        raise ValueError('Expected exactly one repository metadata file')
    SCHEMA_REPOSITORY_CONFIG(yaml.safe_load(repositories[0].read_text(encoding='utf-8')))
    configs = [folder / name for name in ('config.yaml', 'config.yml', 'config.json')
               if (folder / name).is_file()]
    if len(configs) != 1:
        raise ValueError('Expected exactly one app configuration')
    raw = yaml.safe_load(configs[0].read_text(encoding='utf-8'))
    validated = SCHEMA_APP_CONFIG_QUIET(raw)
    if set(raw) - set(validated):
        raise ValueError(f'Unsupported fields: {sorted(set(raw) - set(validated))}')
    if not set(validated['arch']) <= {'amd64', 'aarch64'}:
        raise ValueError('Only supported 64-bit HA architectures may be released')
    options = raw.get('options', {})
    schema = raw.get('schema', {})
    if schema is not False:
        checked = AppOptions(None, schema, raw['name'], raw['slug']).validate(options)
        if checked != options:
            raise ValueError('Default options were dropped or changed by Supervisor validation')
    print(json.dumps(dict(name=raw['name'], version=raw['version'],
                          arch=raw['arch'], config='valid', default_options='valid')))


if __name__ == '__main__':
    main()
