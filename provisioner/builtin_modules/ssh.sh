#!/bin/bash
set -euo pipefail
case "${1:-}" in check|apply|verify) ;; *) exit 2 ;; esac
exec python3 - "$1" "$2" <<'PY'
import base64, json, os, pathlib, pwd, re, subprocess, sys, tempfile
def validate_public_key(key):
    if not isinstance(key, str) or '\n' in key or '\r' in key or len(key) > 16384:
        raise SystemExit('Invalid SSH public key')
    parts = key.split()
    if len(parts) < 2 or parts[0] not in ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'):
        raise SystemExit('Unsupported SSH public key format')
    try:
        blob = base64.b64decode(parts[1], validate=True)
        size = int.from_bytes(blob[:4], 'big')
        if blob[4:4 + size].decode() != parts[0] or len(blob) <= 4 + size:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise SystemExit('Invalid SSH public key encoding')
    # sshd -t does not parse authorized_keys. Validate the complete public key.
    fd, key_file = tempfile.mkstemp(prefix='pve-public-key-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(key + '\n')
        parsed = subprocess.run(['ssh-keygen', '-l', '-f', key_file], capture_output=True, text=True, timeout=15)
        if parsed.returncode != 0:
            raise SystemExit('OpenSSH rejected the configured public key')
    finally:
        os.unlink(key_file)

mode, params_file = sys.argv[1:]
p = json.loads(pathlib.Path(params_file).read_text())
users = p.get('users', [])
if not isinstance(users, list) or len(users) > 50:
    raise SystemExit('Invalid SSH users')
changes = []
for entry in users:
    name = entry.get('name', '')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', name):
        raise SystemExit('Invalid SSH account name')
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        raise SystemExit('SSH module requires an existing user account')
    keys = entry.get('authorized_keys', [])
    if not isinstance(keys, list) or not keys or len(keys) > 100:
        raise SystemExit('At least one authorized key per configured user is required')
    for key in keys:
        validate_public_key(key)
    home = pathlib.Path(user.pw_dir)
    if not home.is_dir() or home.stat().st_uid not in (0, user.pw_uid) or home.stat().st_mode & 0o022:
        raise SystemExit('Account home must have safe ownership and must not be writable by group or others')
    if user.pw_shell in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false'):
        raise SystemExit('SSH account requires an interactive login shell')
    effective = subprocess.run(['/usr/sbin/sshd', '-T', '-C', f'user={name},host=localhost,addr=127.0.0.1'], check=True, capture_output=True, text=True, timeout=30)
    settings = dict(line.split(None, 1) for line in effective.stdout.splitlines() if ' ' in line)
    if settings.get('pubkeyauthentication') != 'yes' or (name == 'root' and settings.get('permitrootlogin') not in ('yes', 'prohibit-password', 'without-password')):
        raise SystemExit('Effective SSH policy does not permit public-key login for this account')
    key_paths = settings.get('authorizedkeysfile', '').split()
    accepted = {'.ssh/authorized_keys', '%h/.ssh/authorized_keys', str(home / '.ssh/authorized_keys')}
    if not accepted.intersection(key_paths):
        raise SystemExit('Effective SSH policy does not use the managed authorized_keys file')
    directory = pathlib.Path(user.pw_dir) / '.ssh'
    target = directory / 'authorized_keys'
    if directory.is_symlink() or target.is_symlink():
        raise SystemExit('Refusing symlinked SSH paths')
    existing = target.read_text() if target.exists() else ''
    missing = [key for key in keys if key not in existing.splitlines()]
    correct_permissions = directory.exists() and target.exists() and directory.stat().st_mode & 0o777 == 0o700 and target.stat().st_mode & 0o777 == 0o600 and target.stat().st_uid == user.pw_uid and directory.stat().st_uid == user.pw_uid
    if missing or not correct_permissions:
        changes.append((user, directory, target, existing, missing))
if mode == 'check':
    sys.exit(1 if changes else 0)
if mode == 'apply':
    for user, directory, target, existing, missing in changes:
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        os.chown(directory, user.pw_uid, user.pw_gid)
        content = existing.rstrip('\n') + ('\n' if existing else '') + '\n'.join(missing) + ('\n' if missing else '')
        fd, name = tempfile.mkstemp(prefix='.authorized-', dir=directory)
        try:
            os.fchmod(fd, 0o600)
            os.fchown(fd, user.pw_uid, user.pw_gid)
            with os.fdopen(fd, 'w') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)
subprocess.run(['/usr/sbin/sshd', '-t'], check=True, timeout=30)
subprocess.run(['systemctl', 'is-active', '--quiet', 'ssh.service'], check=True, timeout=30)
if mode == 'verify' and changes:
    raise SystemExit(1)
print(json.dumps({'passed': True, 'accounts': len(users), 'sshd': 'validated'}))
PY
