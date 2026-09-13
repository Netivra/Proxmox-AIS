#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import ipaddress, json, os, pathlib, re, subprocess, sys, tempfile
mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
servers = p.get('servers')
if not isinstance(servers, list) or not 1 <= len(servers) <= 16:
    raise SystemExit('Between one and sixteen explicit NTP servers are required')
for server in servers:
    if not isinstance(server, str) or len(server) > 253:
        raise SystemExit('Invalid NTP server')
    try:
        ipaddress.ip_address(server)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', server):
            raise SystemExit('NTP server must be a hostname or IP address')
target = pathlib.Path('/etc/chrony/sources.d/pve-provisioner.sources')
expected = ''.join(f'server {server} iburst\n' for server in servers)
active = subprocess.run(['systemctl', 'is-active', '--quiet', 'chrony.service'], timeout=30).returncode == 0
matches = target.is_file() and target.read_text() == expected
if mode == 'check':
    sys.exit(0 if matches and active else 1)
if mode == 'apply':
    # The package module owns installation; this module never swaps NTP daemons.
    config = pathlib.Path('/etc/chrony/chrony.conf')
    if not config.is_file() or not any(line.strip() == 'sourcedir /etc/chrony/sources.d' for line in config.read_text().splitlines()):
        raise SystemExit('Install chrony first and enable its standard sources.d directory')
    target.parent.mkdir(mode=0o755, exist_ok=True)
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
    subprocess.run(['systemctl', 'enable', '--now', 'chrony.service'], check=True, timeout=60)
    subprocess.run(['chronyc', 'reload', 'sources'], check=True, timeout=30)
subprocess.run(['chronyc', 'waitsync', '30', '0.5', '0', '2'], check=True, timeout=90)
subprocess.run(['systemctl', 'is-active', '--quiet', 'chrony.service'], check=True, timeout=30)
if not target.is_file() or target.read_text() != expected:
    raise SystemExit(1)
print(json.dumps({'passed': True, 'clock': 'synchronized'}))
PY
