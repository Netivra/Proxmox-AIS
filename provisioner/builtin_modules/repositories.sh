#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import json, os, pathlib, re, subprocess, sys, tempfile, urllib.parse
def has_repository_indexes(policy, url, suite, components):
    indexes = [line.split() for line in policy.splitlines()]
    return all(any(any(field.rstrip('/') == url.rstrip('/') for field in fields) and f'{suite}/{component}' in fields for fields in indexes) for component in components)

mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
url, suite, components, keyring = (p.get(k) for k in ('url', 'suite', 'components', 'keyring'))
if not isinstance(url, str) or any(c.isspace() for c in url):
    raise SystemExit('An explicit HTTPS repository URL is required')
parsed = urllib.parse.urlsplit(url)
if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
    raise SystemExit('Repository URL must use HTTPS without credentials or query parameters')
if not isinstance(suite, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,40}', suite):
    raise SystemExit('Invalid repository suite')
if not isinstance(components, list) or not components or any(not isinstance(c, str) or not re.fullmatch(r'[a-z][a-z0-9/-]{0,50}', c) for c in components):
    raise SystemExit('Invalid repository components')
if not isinstance(keyring, str) or not re.fullmatch(r'/(usr/share|etc/apt)/keyrings/[A-Za-z0-9_.-]+\.(gpg|asc)', keyring) or not pathlib.Path(keyring).is_file():
    raise SystemExit('An existing administrator-provisioned APT keyring is required')
expected = f'Types: deb\nURIs: {url}\nSuites: {suite}\nComponents: {" ".join(components)}\nSigned-By: {keyring}\n'
target = pathlib.Path('/etc/apt/sources.list.d/pve-provisioner.sources')
matches = target.is_file() and target.read_text() == expected
if mode == 'check':
    sys.exit(0 if matches else 1)
if mode == 'apply' and not matches:
    fd, name = tempfile.mkstemp(prefix='.pve-provisioner-', dir=target.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, 'w') as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
if mode == 'apply':
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=180', 'update'], check=True, timeout=500)
policy = subprocess.run(['apt-cache', 'policy'], check=True, capture_output=True, text=True, timeout=30).stdout
indexed = has_repository_indexes(policy, url, suite, components)
passed = target.is_file() and target.read_text() == expected and indexed
print(json.dumps({'passed': passed, 'repository': url, 'suite': suite}))
sys.exit(0 if passed else 1)
PY
