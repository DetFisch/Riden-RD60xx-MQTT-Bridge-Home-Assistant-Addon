"""Reject images whose actual metadata disagrees with the HA release config."""
import json
import subprocess
import sys


def validate_image(image, arch, version):
    platform = {'amd64': 'amd64', 'aarch64': 'arm64'}[arch]
    if image['Os'] != 'linux' or image['Architecture'] != platform:
        raise ValueError(f'Wrong image platform for {arch}')
    config = image['Config']
    labels = config.get('Labels') or {}
    expected = {'io.hass.type': 'app', 'io.hass.arch': arch, 'io.hass.version': version}
    for key, value in expected.items():
        if labels.get(key) != value:
            raise ValueError(f'{key}: expected {value!r}, found {labels.get(key)!r}')
    if not config.get('Entrypoint') and not config.get('Cmd'):
        raise ValueError('Image has no startup command')


def main():
    reference, arch, version = sys.argv[1:]
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', reference]))[0]
    validate_image(image, arch, version)
    print(f'PASS {reference}: linux/{image["Architecture"]}, HA app {version}')


if __name__ == '__main__':
    main()
