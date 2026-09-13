"""Exercise publication gates with a fake Docker CLI, never a real daemon."""
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
SHA = "1234567890abcdef1234567890abcdef12345678"
REGISTRY_IMAGE = "ghcr.io/team/proxmox-ais"
DIGEST = f"{REGISTRY_IMAGE}@sha256:{'a' * 64}"
PASSWORD = "dummy-ci-token-$()-do-not-print"


def posix_shell():
    if os.name == "nt":
        shell = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if not shell.is_file():
            pytest.skip("CI shell tests need Git Bash on Windows")
    else:
        shell = shutil.which("sh")
        if not shell:
            pytest.skip("CI shell tests need a POSIX shell")
    return str(shell)


@pytest.fixture
def container_ci(tmp_path):
    shell = posix_shell()
    (tmp_path / "ci").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "tmp").mkdir()
    shutil.copyfile(ROOT / "ci/container.sh", tmp_path / "ci/container.sh")
    shutil.copyfile(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    smoke_source = (ROOT / "tests/container_smoke.py").read_bytes()
    (tmp_path / "tests/container_smoke.py").write_bytes(smoke_source)
    docker = tmp_path / "bin/docker"
    docker.write_text(
        """#!/bin/sh
set -eu
{
    printf '%s' "$1"
    for argument in "$@"; do printf '\\t%s' "$argument"; done
    printf '\\n'
} >> "$MOCK_DOCKER_LOG"
if [ "$1" = "${MOCK_FAIL_COMMAND:-}" ]; then exit 37; fi
case "$1" in
    run)
        cat > "$MOCK_SMOKE_STDIN"
        exit "${MOCK_RUN_EXIT:-0}"
        ;;
    login)
        cat > "$MOCK_LOGIN_STDIN"
        printf '%s' "$DOCKER_CONFIG" > "$MOCK_DOCKER_CONFIG"
        printf '{"auths":{"test":"dummy-credentials"}}' > "$DOCKER_CONFIG/config.json"
        ;;
    image)
        printf '%s\\n' "$MOCK_REPO_DIGESTS"
        ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"],
        "TMPDIR": (tmp_path / "tmp").as_posix(),
        "GITHUB_SHA": SHA,
        "GITHUB_RUN_ID": "210",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "container_publish",
        "GITHUB_REPOSITORY": "Team/Proxmox-AIS",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_REF_NAME": "main",
        "DEFAULT_BRANCH": "main",
        "GITHUB_ACTOR": "ci-user",
        "GHCR_TOKEN": PASSWORD,
        "MOCK_DOCKER_LOG": (tmp_path / "docker.log").as_posix(),
        "MOCK_SMOKE_STDIN": (tmp_path / "smoke.stdin").as_posix(),
        "MOCK_LOGIN_STDIN": (tmp_path / "login.stdin").as_posix(),
        "MOCK_DOCKER_CONFIG": (tmp_path / "docker-config.path").as_posix(),
        "MOCK_REPO_DIGESTS": DIGEST,
    }

    def run(mode="publish", **overrides):
        effective_environment = {**environment, **overrides}
        for name in [name for name, value in effective_environment.items() if value is None]:
            del effective_environment[name]
        result = subprocess.run(
            [str(shell), "ci/container.sh", mode],
            cwd=tmp_path,
            env=effective_environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        log = tmp_path / "docker.log"
        calls = [line.split("\t")[1:] for line in log.read_text().splitlines()] if log.exists() else []
        return SimpleNamespace(result=result, calls=calls, root=tmp_path, smoke_source=smoke_source)

    return run


def test_default_branch_publishes_only_after_hardened_image_smoke(container_ci):
    outcome = container_ci()
    assert outcome.result.returncode == 0, outcome.result.stderr
    commands = [call[0] for call in outcome.calls]
    assert commands == ["build", "run", "login", "tag", "push", "tag", "push", "image", "rm", "image"]
    build_image = f"{REGISTRY_IMAGE}:ci-210-1-container_publish"
    assert outcome.calls[-1] == ["image", "rm", build_image]
    build = outcome.calls[0]
    assert build[build.index("--platform") + 1] == "linux/amd64"
    assert "--provenance=false" in build and "--sbom=false" in build
    assert f"org.opencontainers.image.revision={SHA}" in build
    assert "org.opencontainers.image.source=https://github.com/Team/Proxmox-AIS" in build
    assert build[build.index("--tag") + 1] == build_image
    smoke = outcome.calls[1]
    for option in ["--read-only", "--cap-drop", "ALL", "no-new-privileges:true", "/tmp:rw,noexec,nosuid,size=128m"]:
        assert option in smoke
    assert smoke[smoke.index("--entrypoint") + 1] == "python"
    assert smoke[smoke.index("--entrypoint") + 2] == build_image
    assert all(call[1] == build_image for call in outcome.calls if call[0] == "tag")
    assert "--volume" not in smoke and "-v" not in smoke
    assert (outcome.root / "smoke.stdin").read_bytes() == outcome.smoke_source
    assert [call[1] for call in outcome.calls if call[0] == "push"] == [
        f"{REGISTRY_IMAGE}:sha-{SHA}", f"{REGISTRY_IMAGE}:edge"
    ]
    assert (outcome.root / "deploy.env").read_text() == f"PROVISIONER_IMAGE={DIGEST}\n"
    assert "ci-210-1-container_publish" in (outcome.root / "build.env").read_text()
    assert outcome.calls[2] == ["login", "ghcr.io", "--username", "ci-user", "--password-stdin"]
    assert (outcome.root / "login.stdin").read_text() == PASSWORD
    assert PASSWORD not in outcome.result.stdout + outcome.result.stderr + (outcome.root / "docker.log").read_text()
    config_path = Path((outcome.root / "docker-config.path").read_text())
    assert not config_path.exists(), "Temporary registry credentials must be removed"


def test_release_tag_matches_project_version_and_does_not_move_edge(container_ci):
    outcome = container_ci(GITHUB_REF_TYPE="tag", GITHUB_REF_NAME=f"v{VERSION}")
    assert outcome.result.returncode == 0, outcome.result.stderr
    assert [call[1] for call in outcome.calls if call[0] == "push"] == [
        f"{REGISTRY_IMAGE}:sha-{SHA}", f"{REGISTRY_IMAGE}:{VERSION}"
    ]


def test_pull_request_verifies_without_registry_credentials(container_ci):
    outcome = container_ci(
        "verify", GITHUB_EVENT_NAME="pull_request", GITHUB_REF_NAME="1/merge",
        GITHUB_REF_PROTECTED="false", GITHUB_ACTOR=None, GHCR_TOKEN=None,
    )
    assert outcome.result.returncode == 0, outcome.result.stderr
    assert [call[0] for call in outcome.calls] == ["build", "run", "rm", "image"]
    assert "--provenance=false" in outcome.calls[0] and "--sbom=false" in outcome.calls[0]
    assert outcome.calls[-1] == ["image", "rm", f"{REGISTRY_IMAGE}:ci-210-1-container_publish"]
    assert (outcome.root / "build.env").is_file()
    assert not (outcome.root / "deploy.env").exists()


@pytest.mark.parametrize("overrides", [
    {"GITHUB_REF_NAME": "feature/ci"},
    {"GITHUB_REF_NAME": f"v{VERSION}"},
    {"GITHUB_REF_NAME": None},
    {"DEFAULT_BRANCH": ""},
    {"GITHUB_REF_PROTECTED": "false"},
    {"GITHUB_REF_PROTECTED": None},
    {"GITHUB_REF_PROTECTED": "TRUE"},
    {"GITHUB_EVENT_NAME": "pull_request"},
    {"GITHUB_EVENT_NAME": "pull_request_target"},
    {"GITHUB_EVENT_NAME": "workflow_run"},
    {"GITHUB_EVENT_NAME": "schedule"},
    {"GITHUB_EVENT_NAME": "web"},
    {"GITHUB_EVENT_NAME": None},
    {"GITHUB_REF_TYPE": None},
    {"GITHUB_REF_TYPE": "pull_request"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "main"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v9999.9999.9999"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "nightly"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.1.0-rc1"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v00.1.0"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.01.0"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.1.00"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.1"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.1.0.0"},
    {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": f"v{VERSION}", "GITHUB_REF_PROTECTED": "false"},
    {"GHCR_TOKEN": None},
    {"GITHUB_ACTOR": None},
    {"GITHUB_SHA": "1234"},
    {"GITHUB_RUN_ID": "invalid"},
    {"GITHUB_RUN_ATTEMPT": "../2"},
    {"GITHUB_RUN_ATTEMPT": None},
    {"GITHUB_JOB": "../invalid"},
    {"GITHUB_JOB": "has space"},
    {"GITHUB_JOB": "-invalid"},
    {"GITHUB_JOB": ""},
    {"GITHUB_REPOSITORY": "team"},
    {"GITHUB_REPOSITORY": "team/repo/extra"},
    {"GITHUB_REPOSITORY": "team/repo:tag"},
])
def test_unauthorized_or_invalid_publication_fails_before_build(container_ci, overrides):
    outcome = container_ci(**overrides)
    assert outcome.result.returncode != 0
    assert not outcome.calls
    assert not (outcome.root / "deploy.env").exists()


@pytest.mark.parametrize("ref_type, ref_name", [("branch", "main"), ("tag", f"v{VERSION}")])
def test_manual_run_can_publish_protected_default_branch_or_release(container_ci, ref_type, ref_name):
    outcome = container_ci(
        GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF_TYPE=ref_type, GITHUB_REF_NAME=ref_name,
    )
    assert outcome.result.returncode == 0, outcome.result.stderr
    assert (outcome.root / "deploy.env").is_file()


def test_manual_feature_branch_cannot_publish(container_ci):
    outcome = container_ci(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF_NAME="feature/ci")
    assert outcome.result.returncode != 0
    assert not outcome.calls


@pytest.mark.parametrize("attempt, job", [("2", "container_publish"), ("1", "container-verify")])
def test_container_names_include_run_attempt_and_job(container_ci, attempt, job):
    outcome = container_ci("verify", GITHUB_RUN_ATTEMPT=attempt, GITHUB_JOB=job)
    assert outcome.result.returncode == 0, outcome.result.stderr
    build_image = f"{REGISTRY_IMAGE}:ci-210-{attempt}-{job}"
    build, smoke = outcome.calls[:2]
    assert build[build.index("--tag") + 1] == build_image
    assert smoke[smoke.index("--name") + 1] == f"ais-smoke-210-{attempt}-{job}"
    assert outcome.calls[-1] == ["image", "rm", build_image]


@pytest.mark.parametrize("overrides", [
    {"MOCK_FAIL_COMMAND": "build"},
    {"MOCK_RUN_EXIT": "19"},
])
def test_failed_build_or_smoke_never_logs_in_or_pushes(container_ci, overrides):
    outcome = container_ci(**overrides)
    assert outcome.result.returncode != 0
    assert not any(call[0] in {"login", "tag", "push"} for call in outcome.calls)
    assert not (outcome.root / "build.env").exists()
    assert not (outcome.root / "deploy.env").exists()
    if "MOCK_RUN_EXIT" in overrides:
        assert outcome.calls[-2] == ["rm", "--force", "ais-smoke-210-1-container_publish"]
    assert outcome.calls[-1] == ["image", "rm", f"{REGISTRY_IMAGE}:ci-210-1-container_publish"]


def test_push_failure_does_not_create_deployment_artifact_and_cleans_credentials(container_ci):
    outcome = container_ci(MOCK_FAIL_COMMAND="push")
    assert outcome.result.returncode != 0
    assert not (outcome.root / "deploy.env").exists()
    assert not Path((outcome.root / "docker-config.path").read_text()).exists()
    assert len([call for call in outcome.calls if call[0] == "push"]) == 1


@pytest.mark.parametrize("digests", ["", "other.example.test/image@sha256:" + "a" * 64, REGISTRY_IMAGE + "@sha256:invalid"])
def test_missing_or_invalid_registry_digest_fails_without_artifact(container_ci, digests):
    outcome = container_ci(MOCK_REPO_DIGESTS=digests)
    assert outcome.result.returncode != 0
    assert not (outcome.root / "deploy.env").exists()


@pytest.fixture
def python_ci(tmp_path):
    shell = posix_shell()
    (tmp_path / "ci").mkdir()
    (tmp_path / "bin").mkdir()
    shutil.copyfile(ROOT / "ci/python-tests.sh", tmp_path / "ci/python-tests.sh")
    shutil.copyfile(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    venv_python = tmp_path / "venv-python"
    venv_python.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$MOCK_PYTHON_LOG"
case "$2" in
    pip) exit "${MOCK_PIP_EXIT:-0}" ;;
    pytest)
        printf '<testsuite tests="1" />\\n' > reports/pytest.xml
        exit "${MOCK_PYTEST_EXIT:-0}"
        ;;
    *) exit 65 ;;
esac
""", encoding="utf-8", newline="\n",
    )
    python = tmp_path / "bin/python3"
    python.write_text(
        """#!/bin/sh
set -eu
case "$1" in
    -c) exit "${MOCK_PYTHON_VERSION_EXIT:-0}" ;;
    -m)
        [ "$2" = venv ]
        printf '%s' "$3" > "$MOCK_VENV_PATH"
        mkdir -p "$3/bin"
        cp "$MOCK_VENV_PYTHON" "$3/bin/python"
        chmod +x "$3/bin/python"
        ;;
    *) exit 65 ;;
esac
""", encoding="utf-8", newline="\n",
    )
    python.chmod(0o755)
    for name in ["bash", "openssl", "ssh-keygen"]:
        executable = tmp_path / "bin" / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
        executable.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"],
        "GITHUB_WORKSPACE": tmp_path.as_posix(),
        "GITHUB_RUN_ID": "543",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "python_tests",
        "MOCK_PYTHON_LOG": (tmp_path / "python.log").as_posix(),
        "MOCK_VENV_PATH": (tmp_path / "venv.path").as_posix(),
        "MOCK_VENV_PYTHON": venv_python.as_posix(),
    }

    def run(missing_tool=None, **overrides):
        effective_environment = {**environment, **overrides}
        effective_environment = {name: value for name, value in effective_environment.items() if value is not None}
        command = [shell, "ci/python-tests.sh"]
        if missing_tool:
            (tmp_path / "bin" / missing_tool).unlink()
            # Git Bash adds its own tools to PATH on Windows startup. Limit
            # PATH inside the shell to model a genuinely missing system tool.
            command = [shell, "-c", 'PATH="$(pwd -P)/bin"\nexport PATH\n. ci/python-tests.sh']
        result = subprocess.run(
            command, cwd=tmp_path, env=effective_environment,
            capture_output=True, text=True, timeout=30,
        )
        log = tmp_path / "python.log"
        calls = log.read_text().splitlines() if log.exists() else []
        return SimpleNamespace(result=result, calls=calls, root=tmp_path)

    return run


@pytest.mark.parametrize("overrides, expected_code, expected_calls", [
    ({}, 0, ["-m pip install .[dev]", "-m pytest --junitxml=reports/pytest.xml"]),
    ({"MOCK_PYTEST_EXIT": "1"}, 1, ["-m pip install .[dev]", "-m pytest --junitxml=reports/pytest.xml"]),
    ({"MOCK_PIP_EXIT": "42"}, 42, ["-m pip install .[dev]"]),
])
def test_python_job_isolates_dependencies_and_cleans_venv(python_ci, overrides, expected_code, expected_calls):
    outcome = python_ci(**overrides)
    assert outcome.result.returncode == expected_code, outcome.result.stderr
    assert outcome.calls == expected_calls
    assert (outcome.root / "venv.path").is_file()
    assert ".venv-ci-543-1-python_tests." in (outcome.root / "venv.path").read_text()
    assert not list(outcome.root.glob(".venv-ci-*")), "The job must clean its virtual environment even when tests or installation fail"
    assert (outcome.root / "reports/pytest.xml").exists() == ("MOCK_PIP_EXIT" not in overrides)


@pytest.mark.parametrize("required_tool", ["python3", "bash", "openssl", "ssh-keygen"])
def test_python_job_requires_system_tools_instead_of_skipping_tests(python_ci, required_tool):
    outcome = python_ci(missing_tool=required_tool)
    assert outcome.result.returncode != 0
    assert f"Required runner tool is missing: {required_tool}" in outcome.result.stderr
    assert not outcome.calls
    assert not list(outcome.root.glob(".venv-ci-*"))


@pytest.mark.parametrize("overrides", [
    {"GITHUB_RUN_ID": "../invalid"},
    {"GITHUB_RUN_ATTEMPT": "invalid"},
    {"GITHUB_RUN_ATTEMPT": None},
    {"GITHUB_JOB": "../invalid"},
    {"GITHUB_JOB": "has space"},
    {"GITHUB_JOB": "-invalid"},
    {"GITHUB_JOB": ""},
    {"MOCK_PYTHON_VERSION_EXIT": "1"},
])
def test_python_job_rejects_invalid_job_or_python_version_before_installing(python_ci, overrides):
    outcome = python_ci(**overrides)
    assert outcome.result.returncode != 0
    assert not outcome.calls
    assert not list(outcome.root.glob(".venv-ci-*"))


def test_python_job_names_venv_for_the_run_attempt_and_job(python_ci):
    outcome = python_ci(GITHUB_RUN_ATTEMPT="2", GITHUB_JOB="python-tests")
    assert outcome.result.returncode == 0, outcome.result.stderr
    assert ".venv-ci-543-2-python-tests." in (outcome.root / "venv.path").read_text()
    assert not list(outcome.root.glob(".venv-ci-*"))
