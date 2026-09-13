"""Operational commands; no administrative password is saved in clear text."""

from __future__ import annotations

import argparse
from contextlib import closing, suppress
from dataclasses import asdict
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from uuid import uuid4


class ServiceLock:
    """A process-lifetime lock shared by the web service and offline operations."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / ".service.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Data directory is in use. Stop the service before this operation.") from exc
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def load_settings():
    """Read optional TOML, then environment overrides, with safe defaults."""
    from provisioner.config import Settings

    return Settings.from_env()


def initialize(settings, username: str | None = None) -> None:
    from cryptography.fernet import Fernet
    from provisioner.db import Database
    from provisioner.security import Security

    data_dir = Path(settings.data_dir).resolve()
    key_path = Path(settings.master_key_file).resolve()
    if key_path == data_dir or data_dir in key_path.parents:
        raise ValueError("MASTER_KEY_FILE must be outside DATA_DIR")
    with ServiceLock(data_dir):
        database = Database(settings)
        database.initialize()
        with database.connection() as connection:
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("Already initialized; use the administrator interface to manage users")
        username = username or input("Administrator username: ").strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}", username):
            raise ValueError("Administrator username requires 2 to 64 letters, digits, dots, underscores or hyphens")
        password = getpass.getpass("Administrator password (at least 12 characters): ")
        if len(password) < 12:
            raise ValueError("Use a password with at least 12 characters")
        if password != getpass.getpass("Repeat administrator password: "):
            raise ValueError("Passwords do not match")
        if not key_path.exists():
            key_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(Fernet.generate_key())
        security = Security(settings)
        with database.connection() as connection:
            connection.execute(
                "INSERT INTO users(id, username, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid4()), username, security.hash_password(password), "admin", datetime.now(timezone.utc).isoformat()),
            )
        print(f"Initialized {data_dir}. Administrator: {username}")
        print(f"Back up the encryption key separately: {key_path}")


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def backup(settings, destination: Path) -> None:
    """Snapshot SQLite, then immutable referenced artifacts; never include keys."""
    from provisioner.db import Database

    destination = destination.resolve()
    data_dir = Path(settings.data_dir).resolve()
    if destination == data_dir or data_dir in destination.parents:
        raise ValueError("Backup destination must be outside DATA_DIR")
    if destination.exists():
        raise ValueError("Backup destination already exists; use a new directory")
    database = Database(settings)
    if not database.path.exists():
        raise ValueError("No initialized database found")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".ais-backup-", dir=destination.parent))
    try:
        database.backup(temporary / "database.sqlite3")
        artifact_dir = data_dir / "artifacts"
        if artifact_dir.exists():
            if artifact_dir.is_symlink():
                raise ValueError("Refusing symlink in artifact storage")
            # Artifacts are immutable; copying after the database snapshot includes
            # every artifact referenced by that snapshot, plus possibly newer ones.
            for source in artifact_dir.rglob("*"):
                if source.is_symlink():
                    raise ValueError("Refusing symlink in artifact storage")
                if source.is_file():
                    target = temporary / "artifacts" / source.relative_to(artifact_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        config = {
            "public_url": settings.public_url,
            "secure_cookies": settings.secure_cookies,
            "key_included": False,
            "master_key_sha256": _digest(Path(settings.master_key_file)),
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "configuration": {
                name: value for name, value in asdict(settings).items()
                if name not in {"data_dir", "master_key_file", "bootstrap_username", "bootstrap_password", "testing"}
            },
        }
        (temporary / "settings.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        hashes = {path.relative_to(temporary).as_posix(): _digest(path) for path in temporary.rglob("*") if path.is_file()}
        (temporary / "manifest.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        temporary.rename(destination)
    except BaseException:
        if temporary.resolve().parent == destination.parent and temporary.name.startswith(".ais-backup-"):
            with suppress(OSError):
                shutil.rmtree(temporary)
        raise
    print(f"Backup complete: {destination}")
    print("Encryption key is excluded. Save it separately and test restoration.")


def _invalidate_restored_state(connection: sqlite3.Connection) -> None:
    """Prevent rollback of the database from resurrecting machine credentials."""
    connection.execute("DELETE FROM sessions")
    connection.execute("DELETE FROM nonces")
    connection.execute("UPDATE approvals SET status='revoked' WHERE status='approved'")
    connection.execute("UPDATE groups SET revoked=1")
    connection.execute("UPDATE hosts SET status='needs_review' WHERE id IN (SELECT host_id FROM runs WHERE status NOT IN ('succeeded','failed','cancelled','expired'))")
    connection.execute("UPDATE runs SET status='needs_review' WHERE status NOT IN ('succeeded','failed','cancelled','expired')")
    connection.execute(
        "UPDATE runs SET device_key=NULL, bootstrap_hash=NULL, enrollment_hash=NULL, report_hash=NULL, "
        "answer_until=0, enroll_until=0, lease_until=0, answer_ciphertext=NULL, bootstrap_ciphertext=NULL"
    )
    connection.execute(
        "INSERT INTO audit(id,actor,action,object_id,reason,data,created_at) VALUES(?,?,?,?,?,?,?)",
        (str(uuid4()), "system", "backup.restore", "database", "Offline restore; credentials revoked and active runs require review", "{}", datetime.now(timezone.utc).isoformat()),
    )


def restore(settings, source: Path) -> None:
    """Restore into a new data directory; the original data is never overwritten."""
    from cryptography.fernet import Fernet
    from provisioner.db import Database

    source = source.resolve()
    data_dir = Path(settings.data_dir).resolve()
    if not Path(settings.master_key_file).is_file():
        raise ValueError("Restore the separately protected MASTER_KEY_FILE first")
    Fernet(Path(settings.master_key_file).read_bytes().strip())
    if not (source / "manifest.json").is_file():
        raise ValueError("Missing backup manifest")
    hashes = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if "database.sqlite3" not in hashes or "settings.json" not in hashes:
        raise ValueError("Incomplete backup manifest")
    for name, expected in hashes.items():
        original = source / name
        candidate = original.resolve()
        if source not in candidate.parents or original.is_symlink() or any(parent.is_symlink() for parent in original.parents if parent != source.parent) or not candidate.is_file():
            raise ValueError("Unsafe or missing backup file")
        if _digest(candidate) != expected:
            raise ValueError(f"Backup checksum mismatch: {name}")
    config = json.loads((source / "settings.json").read_text(encoding="utf-8"))
    if config.get("format") != 1:
        raise ValueError("Unsupported backup format")
    if _digest(Path(settings.master_key_file)) != config.get("master_key_sha256"):
        raise ValueError("MASTER_KEY_FILE does not match this backup")
    with ServiceLock(data_dir):
        existing = [path for path in data_dir.iterdir() if path.name != ".service.lock"]
        if existing:
            raise ValueError("Restore requires an empty DATA_DIR. Keep the old directory until validation completes.")
        database = Database(settings)
        shutil.copy2(source / "database.sqlite3", database.path)
        try:
            with closing(sqlite3.connect(database.path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup database failed integrity check")
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("Unsupported backup database schema")
                _invalidate_restored_state(connection)
            for name in hashes:
                if name.startswith("artifacts/"):
                    target = data_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source / name, target)
        except BaseException:
            # Leave failed restoration for inspection; never start it silently.
            (data_dir / "RESTORE_FAILED").write_text("Restore failed. Do not start this data directory.\n", encoding="utf-8")
            raise
    print(f"Restored into {data_dir}; active runs need review and previous machine credentials are invalid.")
    print("Review settings.json, revoke/reissue installation media, and reconcile hosts before new approvals.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proxmox-ais")
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init", help="Create external encryption key and initial administrator")
    init_parser.add_argument("--username")
    serve_parser = commands.add_parser("serve", help="Start the HTTP service behind a trusted TLS proxy")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--tls-cert", type=Path)
    serve_parser.add_argument("--tls-key", type=Path)
    backup_parser = commands.add_parser("backup", help="Create a consistent database and artifact snapshot")
    backup_parser.add_argument("destination", type=Path)
    restore_parser = commands.add_parser("restore", help="Restore offline into an empty DATA_DIR")
    restore_parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if args.command == "init":
            initialize(settings, args.username)
        elif args.command == "backup":
            backup(settings, args.destination)
        elif args.command == "restore":
            restore(settings, args.source)
        elif args.command == "serve":
            import uvicorn
            from provisioner.app import create_app

            if bool(args.tls_cert) != bool(args.tls_key):
                raise ValueError("Provide both --tls-cert and --tls-key")
            if (Path(settings.data_dir) / "RESTORE_FAILED").exists():
                raise ValueError("This data directory contains a failed restore")
            uvicorn.run(
                create_app(settings), host=args.host, port=args.port,
                proxy_headers=False, access_log=False,
                ssl_certfile=str(args.tls_cert) if args.tls_cert else None,
                ssl_keyfile=str(args.tls_key) if args.tls_key else None,
            )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
