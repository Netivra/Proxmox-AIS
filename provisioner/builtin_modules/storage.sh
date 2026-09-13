#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import json, pathlib, re, subprocess, sys
mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
storage_id, location, content = (p.get(k) for k in ('id', 'path', 'content'))
if not isinstance(storage_id, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', storage_id):
    raise SystemExit('Invalid storage identifier')
if not isinstance(location, str) or any(c.isspace() for c in location) or not location.startswith(('/mnt/', '/srv/')):
    raise SystemExit('Storage must be an existing absolute directory under /mnt or /srv')
directory = pathlib.Path(location)
if not directory.is_dir() or directory.is_symlink() or str(directory.resolve()) != location.rstrip('/'):
    raise SystemExit('Storage directory must already exist without symbolic links or traversal')
if not isinstance(content, list) or not content or not set(content).issubset({'images', 'rootdir', 'vztmpl', 'iso', 'backup', 'snippets'}):
    raise SystemExit('Invalid storage content types')
def configuration():
    result = subprocess.run(['pvesh', 'get', '/storage', '--output-format', 'json'], check=True, capture_output=True, text=True, timeout=30)
    if not any(entry.get('storage') == storage_id for entry in json.loads(result.stdout)):
        return None
    detail = subprocess.run(['pvesh', 'get', '/storage/' + storage_id, '--output-format', 'json'], check=True, capture_output=True, text=True, timeout=30)
    return json.loads(detail.stdout)
existing = configuration()
if existing and (existing.get('type') != 'dir' or existing.get('path', '').rstrip('/') != location.rstrip('/') or set(existing.get('content', '').split(',')) != set(content)):
    raise SystemExit('Existing storage has conflicting settings; automatic changes are refused')
if mode == 'check':
    sys.exit(0 if existing else 1)
if mode == 'apply' and not existing:
    subprocess.run(['pvesm', 'add', 'dir', storage_id, '--path', location, '--content', ','.join(content)], check=True, timeout=60)
result = subprocess.run(['pvesm', 'status', '--storage', storage_id], check=True, capture_output=True, text=True, timeout=30)
if not any(re.match(r'^' + re.escape(storage_id) + r'\s+dir\s+active\s', line) for line in result.stdout.splitlines()):
    raise SystemExit('Storage is not active')
print(json.dumps({'passed': True, 'storage': storage_id, 'destructive_operations': False}))
PY
