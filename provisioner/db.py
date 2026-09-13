from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf_token TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS hosts(id TEXT PRIMARY KEY, fqdn TEXT NOT NULL UNIQUE COLLATE NOCASE, management_ip TEXT UNIQUE, site TEXT NOT NULL, status TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS host_identities(host_id TEXT NOT NULL REFERENCES hosts(id), kind TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(kind,value));
CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(name,kind,version));
CREATE TABLE IF NOT EXISTS modules(id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(name,version));
CREATE TABLE IF NOT EXISTS groups(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, site TEXT NOT NULL, token_hash TEXT NOT NULL, expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS iso_records(id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, ciphertext TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, host_id TEXT NOT NULL REFERENCES hosts(id), status TEXT NOT NULL, expires_at REAL NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_approval ON approvals(host_id) WHERE status='approved';
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, host_id TEXT NOT NULL REFERENCES hosts(id), approval_id TEXT NOT NULL UNIQUE REFERENCES approvals(id), status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL, answer_ciphertext TEXT, bootstrap_ciphertext TEXT, secrets_ciphertext TEXT, bootstrap_hash TEXT UNIQUE, enrollment_hash TEXT, report_hash TEXT UNIQUE, device_key TEXT, answer_until REAL, enroll_until REAL, lease_until REAL, last_seen REAL, completed_at REAL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(host_id) WHERE status NOT IN ('succeeded','failed','cancelled','expired');
CREATE TABLE IF NOT EXISTS run_steps(run_id TEXT NOT NULL REFERENCES runs(id), step_id TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0, verification TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id,step_id));
CREATE TABLE IF NOT EXISTS events(run_id TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,sequence));
CREATE TABLE IF NOT EXISTS logs(run_id TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,sequence));
CREATE TABLE IF NOT EXISTS nonces(run_id TEXT NOT NULL REFERENCES runs(id), nonce TEXT NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(run_id,nonce));
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL, reason TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discoveries(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, site TEXT NOT NULL, data TEXT NOT NULL, reason TEXT NOT NULL, last_seen REAL NOT NULL);
INSERT OR IGNORE INTO schema_migrations(version) VALUES(1);
"""


class Database:
    def __init__(self, settings):
        self.path = settings.data_dir / "provisioner.sqlite3"

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Datenbankschema ist neuer als diese Anwendung.")
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self, write=False):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if connection.in_transaction:
                connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def backup(self, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
