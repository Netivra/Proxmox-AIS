from dataclasses import dataclass, field, fields
import os
from pathlib import Path
import tomllib
from urllib.parse import urlsplit


@dataclass
class Settings:
    data_dir: Path = Path("data")
    master_key_file: Path = Path("secrets/master.key")
    public_url: str = "https://localhost:8080"
    secure_cookies: bool = True
    bootstrap_username: str | None = None
    bootstrap_password: str | None = None
    testing: bool = False
    session_hours: int = 8
    answer_window_seconds: int = 300
    enrollment_hours: int = 4
    lease_seconds: int = 900
    heartbeat_unknown_seconds: int = 180
    log_retention_days: int = 30
    audit_retention_days: int = 180
    max_request_bytes: int = 1048576
    four_eyes: bool = True
    maintenance: bool = False
    trusted_proxy_ips: str = "127.0.0.1"
    runner_ca_file: str | None = None
    defaults: dict = field(default_factory=dict)
    sites: dict = field(default_factory=dict)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.master_key_file = Path(self.master_key_file)
        self.public_url = self.public_url.rstrip("/")
        url = urlsplit(self.public_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment or url.path:
            raise ValueError("PUBLIC_URL muss eine absolute Basis-URL ohne Pfad sein.")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "testserver"}):
            raise ValueError("PUBLIC_URL benötigt HTTPS; HTTP ist nur für lokale Entwicklung erlaubt.")
        if self.master_key_file.resolve().is_relative_to(self.data_dir.resolve()):
            raise ValueError("Der Master-Key muss außerhalb des Datenverzeichnisses liegen.")
        for name in ("session_hours", "answer_window_seconds", "enrollment_hours", "lease_seconds", "max_request_bytes"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} muss positiv sein.")

    @classmethod
    def from_env(cls):
        values = {}
        if os.environ.get("APP_CONFIG"):
            with open(os.environ["APP_CONFIG"], "rb") as stream:
                loaded = tomllib.load(stream)
            values.update(loaded.get("app", loaded))
        bools = {"secure_cookies", "testing", "four_eyes", "maintenance"}
        ints = {f.name for f in fields(cls) if f.type is int}
        allowed = {f.name for f in fields(cls)}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unbekannte Konfiguration: {', '.join(sorted(unknown))}")
        for key in allowed:
            value = os.environ.get(key.upper())
            if value is not None:
                values[key] = value.lower() in {"true", "1", "yes"} if key in bools else int(value) if key in ints else value
        return cls(**values)
