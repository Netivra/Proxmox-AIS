#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import json, pathlib, re, shutil, socket, subprocess, sys, time
mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
try:
    result = subprocess.run(['pveversion'], check=True, capture_output=True, text=True, timeout=20)
    match = re.search(r'pve-manager/([^/\s]+)', result.stdout)
    if not match:
        raise ValueError('Target does not report a Proxmox VE manager version')
    if p.get('allowed_versions') and match[1] not in p['allowed_versions']:
        raise ValueError('PVE version is outside the approved module target versions')
    minimum = p.get('minimum_free_mb', 2048)
    if not isinstance(minimum, int) or not 512 <= minimum <= 1048576:
        raise ValueError('minimum_free_mb is invalid')
    if shutil.disk_usage('/').free < minimum * 1024 * 1024:
        raise ValueError('Insufficient free root filesystem space')
    if time.time() < 1704067200:
        raise ValueError('System clock is not plausible')
    for name in p.get('dns_names', []):
        if not isinstance(name, str) or not name or len(name) > 253:
            raise ValueError('Invalid DNS target')
        socket.getaddrinfo(name, 443)
    print(json.dumps({'passed': True, 'pve_version': match[1], 'dns': 'resolved', 'free_space': 'sufficient'}))
except (ValueError, OSError, subprocess.SubprocessError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(2)
PY
