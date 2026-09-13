from typing import Any, Literal
from ipaddress import ip_interface
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Identity(Model):
    kind: Literal["uuid", "serial", "mac"]
    value: str = Field(min_length=1, max_length=200)

    @field_validator("value")
    @classmethod
    def meaningful(cls, value):
        if value.lower() in {"unknown", "none", "not specified", "default string", "to be filled by o.e.m."}:
            raise ValueError("Identität enthält einen Hersteller-Platzhalter.")
        return value


def normalize_identity(kind, value):
    value = value.strip().lower()
    if kind == "mac":
        value = value.replace("-", ":")
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value) or value in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
            raise ValueError("Ungültige MAC-Adresse.")
    if kind == "uuid":
        parsed = uuid.UUID(value)
        if parsed.int in {0, 2**128 - 1}:
            raise ValueError("Ungültige System-UUID.")
        value = str(parsed)
    return value


class HostCreate(Model):
    fqdn: str = Field(min_length=3, max_length=253)
    site: str = Field(min_length=1, max_length=80)
    management_ip: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=30)
    identities: list[Identity] = Field(min_length=1, max_length=32)
    installation_profile_id: str | None = None
    postinstall_profile_id: str | None = None
    iso_id: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    blocked: bool = False

    @field_validator("fqdn")
    @classmethod
    def hostname(cls, value):
        value = value.lower().rstrip(".")
        if "." not in value or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in value.split(".")):
            raise ValueError("Ein gültiger FQDN ist erforderlich.")
        return value

    @field_validator("management_ip")
    @classmethod
    def address(cls, value):
        return str(ip_interface(value)) if value else None


class HostUpdate(Model):
    expected_version: int = Field(ge=1)
    fqdn: str | None = None
    site: str | None = None
    management_ip: str | None = None
    tags: list[str] | None = None
    identities: list[Identity] | None = None
    installation_profile_id: str | None = None
    postinstall_profile_id: str | None = None
    iso_id: str | None = None
    overrides: dict | None = None
    blocked: bool | None = None


class StepSpec(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
    module_id: str
    parameters: dict = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    required: bool = True


class ProfileCreate(Model):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["installation", "postinstall"]
    values: dict = Field(default_factory=dict)
    steps: list[StepSpec] = Field(default_factory=list, max_length=50)
    target_builds: list[str] = Field(default_factory=list, max_length=30)
    locked_fields: list[str] = Field(default_factory=lambda: ["disk_setup", "global.root-password", "global.root-password-hashed"])
    reboot_budget: int = Field(default=1, ge=0, le=5)
    reason: str = Field(default="", max_length=1000)


class ModuleCreate(Model):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=10, max_length=262144)
    parameters_schema: dict = Field(default_factory=lambda: {"type": "object", "additionalProperties": False})
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    target_builds: list[str] = Field(default_factory=list, max_length=30)
    timeout_seconds: int = Field(default=600, ge=1, le=7200)
    retry_safe: bool = False
    test_evidence: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=1000)


class Publish(Model):
    test_evidence: str = Field(min_length=5, max_length=2000)
    reason: str = Field(min_length=3, max_length=1000)


class Approval(Model):
    expected_version: int = Field(ge=1)
    valid_minutes: int = Field(default=30, ge=1, le=1440)
    confirmation: str
    disks_confirmed: bool
    reason: str = Field(min_length=5, max_length=1000)


class RunAction(Model):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=1000)


class RunReconcile(RunAction):
    confirmation: str
    execution_stopped: bool


class GroupCreate(Model):
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    site: str = Field(min_length=1, max_length=80)
    valid_hours: int = Field(default=720, ge=1, le=8760)


class IsoCreate(Model):
    name: str = Field(min_length=1, max_length=120)
    build: str = Field(pattern=r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?-[0-9]+$")
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    assistant_version: str = Field(min_length=1, max_length=80)
    fingerprint: str = Field(pattern=r"^(?:[a-fA-F0-9]{64}|(?:[a-fA-F0-9]{2}:){31}[a-fA-F0-9]{2})$")
    group_id: str
    test_status: Literal["draft", "passed"] = "draft"
    test_evidence: str = Field(default="", max_length=3000)
    native_token_support: bool = False


class UserCreate(Model):
    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}$")
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["reader", "operator", "author", "admin", "developer"]


class SecretCreate(Model):
    name: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=16384)


class Enroll(Model):
    run_id: str
    enrollment_secret: str = Field(min_length=20, max_length=200)
    public_key: str = Field(min_length=40, max_length=100)
    identities: list[Identity] = Field(min_length=1, max_length=32)
    boot_id: str = Field(min_length=1, max_length=80)


class Event(Model):
    sequence: int = Field(ge=1)
    boot_id: str = Field(min_length=1, max_length=80)
    step_id: str | None = None
    type: Literal["step.started", "step.succeeded", "step.failed", "run.needs_review", "run.reboot_pending", "run.resumed", "run.cancelled", "heartbeat"]
    occurred_at: str = Field(max_length=80)
    exit_code: int | None = None
    verification: dict = Field(default_factory=dict)


class EventBatch(Model):
    events: list[Event] = Field(min_length=1, max_length=100)


class LogChunk(Model):
    sequence: int = Field(ge=1)
    step_id: str | None = None
    text: str = Field(max_length=16384)


class LogBatch(Model):
    chunks: list[LogChunk] = Field(min_length=1, max_length=32)


class Completion(Model):
    verification: dict


class LeaseRequest(Model):
    run_id: str
