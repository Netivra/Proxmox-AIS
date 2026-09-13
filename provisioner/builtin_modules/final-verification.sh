#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import json, pathlib, re, socket, subprocess, sys
mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
try:
    version = subprocess.run(['pveversion'], check=True, capture_output=True, text=True, timeout=30).stdout
    match = re.search(r'pve-manager/([^/\s]+)', version)
    if not match or (p.get('allowed_versions') and match[1] not in p['allowed_versions']):
        raise ValueError('PVE version verification failed')
    for service in ('pve-cluster.service', 'pvedaemon.service', 'pveproxy.service', 'pvestatd.service'):
        subprocess.run(['systemctl', 'is-active', '--quiet', service], check=True, timeout=30)
    for name in p.get('dns_names', []):
        socket.getaddrinfo(name, 443)
    if p.get('require_time_sync', True):
        sync = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], check=True, capture_output=True, text=True, timeout=30)
        if sync.stdout.strip() != 'yes':
            raise ValueError('System clock is not synchronized')
    for storage_id in p.get('storage_ids', []):
        if not isinstance(storage_id, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', storage_id):
            raise ValueError('Invalid storage identifier')
        result = subprocess.run(['pvesm', 'status', '--storage', storage_id], check=True, capture_output=True, text=True, timeout=30)
        if not any(re.match(r'^' + re.escape(storage_id) + r'\s+\S+\s+active\s', line) for line in result.stdout.splitlines()):
            raise ValueError('Required storage is not active')
    print(json.dumps({'passed': True, 'pve_version': match[1], 'services': 'active', 'clock': 'checked', 'storage': 'checked'}))
except (ValueError, OSError, subprocess.SubprocessError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(2)
PY
