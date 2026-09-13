import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets

from cryptography.fernet import Fernet


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    if not isinstance(value, bytes):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def token():
    return secrets.token_urlsafe(32)


class Security:
    def __init__(self, settings):
        path = settings.master_key_file
        if not path.exists():
            if not settings.testing:
                raise RuntimeError("Master-Key fehlt. Zuerst 'provisioner init' ausführen.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(Fernet.generate_key())
            path.chmod(0o600)
        self.fernet = Fernet(path.read_bytes().strip())

    @staticmethod
    def hash_password(password):
        if len(password) < 12 or len(password) > 1024:
            raise ValueError("Passwörter benötigen 12 bis 1024 Zeichen.")
        salt = os.urandom(16)
        result = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(result).decode()

    @staticmethod
    def verify_password(password, stored):
        try:
            algorithm, salt, expected = stored.split("$")
            if algorithm != "scrypt" or len(password) > 1024:
                return False
            result = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=1)
            return hmac.compare_digest(result, base64.b64decode(expected))
        except (ValueError, TypeError):
            return False

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        return self.fernet.decrypt(value.encode()).decode()


def redact(text, values=()):
    text = str(text)
    for value in sorted({str(v) for v in values if v}, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
    text = re.sub(r"(/(?:bootstrap/v1|installer/v1/report)/)[A-Za-z0-9_-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:password|secret|token|authorization)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    return text


def atomic_artifact(directory: Path, source: str):
    content = source.encode("utf-8")
    checksum = digest(content)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / checksum
    if not target.exists():
        temporary = directory / (".tmp-" + token())
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    return checksum
