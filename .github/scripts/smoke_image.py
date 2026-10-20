"""Check packaged runtime dependencies without starting services or contacting providers."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    image, path, arch = sys.argv[1:]
    plan = json.loads(Path('.github/addons.json').read_text(encoding='utf-8'))
    smoke = next(entry['smoke'] for entry in plan if entry['path'] == path)
    platform = {'amd64': 'linux/amd64', 'aarch64': 'linux/arm64'}[arch]
    subprocess.run(['docker', 'run', '--rm', '--platform', platform, '--network', 'none',
                    '--read-only', '--tmpfs', '/tmp', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--pids-limit', '128',
                    '--memory', '1g', '--cpus', '2', '--entrypoint', smoke['entrypoint'],
                    image, *smoke['args']], check=True, timeout=120)
    print(f'PASS isolated runtime dependency check: {image}')


if __name__ == '__main__':
    main()
