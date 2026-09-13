"""Build a self-contained first-boot executable without target-side downloads."""
import base64
import json
from pathlib import Path
import urllib.parse


def render_bootstrap(config: dict) -> str:
    required = {"api_url", "run_id", "enrollment_secret", "identities", "manifest_digest"}
    if not required.issubset(config):
        raise ValueError(f"Missing bootstrap values: {', '.join(sorted(required - config.keys()))}")
    if urllib.parse.urlsplit(config["api_url"]).scheme != "https":
        raise ValueError("Bootstrap requires a certificate-validated HTTPS API URL")
    runner = base64.b64encode(Path(__file__).with_name("runner.py").read_bytes()).decode()
    settings = base64.b64encode(json.dumps(config, ensure_ascii=False, sort_keys=True).encode()).decode()
    script = f'''#!/bin/bash
set -euo pipefail
umask 077
test "$(id -u)" = 0
command -v python3 >/dev/null
command -v openssl >/dev/null
command -v systemctl >/dev/null
# Persist every input before enabling the first network operation.
python3 - <<'PVE_BOOTSTRAP_PY'
import base64, json, os, pathlib, tempfile
etc = pathlib.Path('/etc/pve-provisioner')
state = pathlib.Path('/var/lib/pve-provisioner')
etc.mkdir(mode=0o700, parents=True, exist_ok=True)
state.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(etc, 0o700)
os.chmod(state, 0o700)
config = base64.b64decode('{settings}')
settings = json.loads(config)
existing = state / 'state.json'
if existing.exists() and json.loads(existing.read_text())['run_id'] != json.loads(config)['run_id']:
    raise SystemExit('Another run owns this host; explicit local state archival is required')
def persist(path, content, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)
if settings.get('ca_pem'):
    persist(etc / 'trusted-ca.pem', settings.pop('ca_pem').encode())
    settings['ca_file'] = str(etc / 'trusted-ca.pem')
    config = json.dumps(settings, sort_keys=True).encode()
persist(etc / 'config.json', config)
persist(etc / 'runner.py', base64.b64decode('{runner}'))
service = b"""[Unit]
Description=Proxmox one-run provisioner
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=3600
StartLimitBurst=120

[Service]
Type=simple
ExecStart=/usr/bin/python3 /etc/pve-provisioner/runner.py
Restart=on-failure
RestartSec=30
TimeoutStopSec=14500
KillMode=mixed
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
persist(pathlib.Path('/etc/systemd/system/pve-provisioner.service'), service, 0o644)
PVE_BOOTSTRAP_PY
systemctl daemon-reload
systemctl enable --now pve-provisioner.service
'''
    if len(script.encode()) >= 1024 * 1024:
        raise ValueError("Bootstrap exceeds installer size limit")
    return script
