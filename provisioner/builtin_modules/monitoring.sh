#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import json, pathlib, subprocess, sys, urllib.request
mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
if not isinstance(p.get('enabled', False), bool):
    raise SystemExit('enabled must be a boolean')
if not p.get('enabled', False):
    print('{"passed":true,"enabled":false}')
    raise SystemExit(0)
service = 'prometheus-node-exporter.service'
active = subprocess.run(['systemctl', 'is-active', '--quiet', service], timeout=30).returncode == 0
if mode == 'check':
    sys.exit(0 if active else 1)
if mode == 'apply':
    package = subprocess.run(['dpkg-query', '-W', '-f=${Status}', 'prometheus-node-exporter'], capture_output=True, text=True, timeout=30)
    if package.returncode != 0 or package.stdout != 'install ok installed':
        raise SystemExit('Install prometheus-node-exporter with the package module first')
    subprocess.run(['systemctl', 'enable', '--now', service], check=True, timeout=60)
subprocess.run(['systemctl', 'is-active', '--quiet', service], check=True, timeout=30)
with urllib.request.urlopen('http://127.0.0.1:9100/metrics', timeout=10) as response:
    body = response.read(2 * 1024 * 1024)
if b'node_exporter_build_info' not in body:
    raise SystemExit('Node exporter metrics validation failed')
print('{"passed":true,"metrics":"available"}')
PY
