#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import json, pathlib, re, subprocess, sys
mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
packages = p.get('packages', [])
if not isinstance(packages, list) or len(packages) > 100 or any(not isinstance(x, str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]{0,100}', x) for x in packages):
    raise SystemExit('Invalid package list; names only, no options or shell expressions')
def installed(name):
    result = subprocess.run(['dpkg-query', '-W', '-f=${Status}', name], capture_output=True, text=True, timeout=30)
    return result.returncode == 0 and result.stdout == 'install ok installed'
missing = [name for name in packages if not installed(name)]
if mode == 'apply' and missing:
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=180', 'update'], check=True, timeout=600)
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=180', 'install', '-y', '--no-install-recommends', '--', *missing], check=True, timeout=1200)
    missing = [name for name in packages if not installed(name)]
print(json.dumps({'passed': not missing, 'missing': missing}))
sys.exit(1 if missing else 0)
PY
