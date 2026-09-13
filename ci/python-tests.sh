#!/bin/sh
# Run tests in an isolated, disposable environment on a Linux Actions runner.
set -eu

fail() {
    printf '%s\n' "ERROR: $*" >&2
    exit 1
}

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
: "${GITHUB_JOB:?GITHUB_JOB is required}"
for run_identifier in "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT"; do
    case "$run_identifier" in
        ''|*[!0-9]*) fail 'GitHub run ID and attempt must be numeric' ;;
    esac
done
case "$GITHUB_JOB" in
    ''|[!A-Za-z_]*|*[!A-Za-z0-9_-]*) fail 'GITHUB_JOB must be a safe job identifier' ;;
esac
job_identifier="${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${GITHUB_JOB}"

for required_tool in python3 bash openssl ssh-keygen; do
    command -v "$required_tool" >/dev/null 2>&1 || fail "Required runner tool is missing: $required_tool"
done
python3 -c 'import sys; sys.version_info >= (3, 12) or sys.exit("Python 3.12 or newer is required")'

cd "$GITHUB_WORKSPACE"
project_root=$(pwd -P)
[ -f pyproject.toml ] || fail 'GITHUB_WORKSPACE does not contain pyproject.toml'
# Resolve the checkout first and allocate a fresh directory inside it. The
# cleanup target is never taken from an arbitrary environment-provided path.
venv_dir=$(mktemp -d "$project_root/.venv-ci-${job_identifier}.XXXXXXXX")
cleanup() {
    cleanup_status=$?
    trap - EXIT
    case "$venv_dir" in
        "$project_root"/.venv-ci-"$job_identifier".*) rm -rf -- "$venv_dir" ;;
        *) printf '%s\n' 'ERROR: Refusing to clean an unexpected virtual environment path' >&2 ;;
    esac
    exit "$cleanup_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install '.[dev]'
mkdir -p reports
"$venv_dir/bin/python" -m pytest --junitxml=reports/pytest.xml
