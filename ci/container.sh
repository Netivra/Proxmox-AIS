#!/bin/sh
# Build and smoke-test the same image that is optionally published to GHCR.
set -eu

fail() {
    printf '%s\n' "ERROR: $*" >&2
    exit 1
}

mode=${1:-}
case "$mode" in
    verify|publish) ;;
    *) fail 'Usage: sh ci/container.sh verify|publish' ;;
esac

: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
: "${GITHUB_JOB:?GITHUB_JOB is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_SERVER_URL:?GITHUB_SERVER_URL is required}"
printf '%s\n' "$GITHUB_SHA" | grep -Eq '^[0-9a-f]{40}$' || fail 'Expected a full Git commit SHA'
for run_identifier in "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT"; do
    case "$run_identifier" in
        ''|*[!0-9]*) fail 'GitHub run ID and attempt must be numeric' ;;
    esac
done
case "$GITHUB_JOB" in
    ''|[!A-Za-z_]*|*[!A-Za-z0-9_-]*) fail 'GITHUB_JOB must be a safe job identifier' ;;
esac
printf '%s\n' "$GITHUB_REPOSITORY" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$' || fail 'Expected GITHUB_REPOSITORY in owner/repository form'
registry_image="ghcr.io/$(printf '%s' "$GITHUB_REPOSITORY" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
project_url="${GITHUB_SERVER_URL%/}/${GITHUB_REPOSITORY}"
job_identifier="${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${GITHUB_JOB}"

# Read the literal PEP 621 version without needing the test job's virtual
# environment, installing dependencies, or evaluating project code.
project_version=$(awk '
    /^\[project\][[:space:]]*$/ { in_project = 1; next }
    /^\[/ { in_project = 0 }
    in_project && /^[[:space:]]*version[[:space:]]*=/ {
        value = $0
        sub(/^[^=]*=[[:space:]]*"/, "", value)
        sub(/"[[:space:]]*(#.*)?$/, "", value)
        print value
        exit
    }
' pyproject.toml)
[ -n "$project_version" ] || fail 'Cannot read [project].version from pyproject.toml'

publish_tag=
if [ "$mode" = publish ]; then
    [ "${GITHUB_REF_PROTECTED:-}" = true ] || fail 'Publication requires a protected branch or tag'
    case "${GITHUB_EVENT_NAME:-}" in
        push|workflow_dispatch) ;;
        *) fail 'Publication is allowed only from push or workflow_dispatch events' ;;
    esac
    case "${GITHUB_REF_TYPE:-}" in
        tag)
            printf '%s\n' "${GITHUB_REF_NAME:-}" | grep -Eq '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' || fail 'Release tags must have the form vX.Y.Z'
            publish_tag=${GITHUB_REF_NAME#v}
            [ "$publish_tag" = "$project_version" ] || fail "Release tag $GITHUB_REF_NAME does not match project version $project_version"
            ;;
        branch)
            [ -n "${DEFAULT_BRANCH:-}" ] && [ "${GITHUB_REF_NAME:-}" = "$DEFAULT_BRANCH" ] || fail 'Only the default branch or a version tag may publish'
            publish_tag=edge
            ;;
        *) fail 'Publication requires a branch or tag ref' ;;
    esac
    : "${GITHUB_ACTOR:?GITHUB_ACTOR is required for publication}"
    : "${GHCR_TOKEN:?GHCR_TOKEN is required for publication}"
fi

BUILD_IMAGE="${registry_image}:ci-${job_identifier}"
smoke_container="ais-smoke-${job_identifier}"
smoke_started=false
build_started=false
docker_config_dir=
cleanup() {
    if [ "$smoke_started" = true ]; then
        docker rm --force "$smoke_container" >/dev/null 2>&1 || true
    fi
    if [ "$build_started" = true ]; then
        # A self-hosted runner may use a persistent daemon. Remove only this job's
        # unique tag; published SHA/channel tags may be used by other jobs.
        docker image rm "$BUILD_IMAGE" >/dev/null 2>&1 || true
    fi
    if [ -n "$docker_config_dir" ]; then
        rm -f "$docker_config_dir/config.json"
        rmdir "$docker_config_dir" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
rm -f build.env deploy.env

build_started=true
# Load one single-platform image locally so the smoke test and both published
# tags all use the exact image built here, without separate manifest artifacts.
docker build --pull --platform linux/amd64 \
    --provenance=false --sbom=false \
    --label "org.opencontainers.image.source=$project_url" \
    --label "org.opencontainers.image.revision=$GITHUB_SHA" \
    --label "org.opencontainers.image.version=$project_version" \
    --tag "$BUILD_IMAGE" .

# Stream the smoke script through stdin, so no checkout bind mount is needed.
smoke_started=true
docker run --rm --interactive --name "$smoke_container" \
    --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --network none --tmpfs /tmp:rw,noexec,nosuid,size=128m \
    --entrypoint python "$BUILD_IMAGE" - < tests/container_smoke.py

printf 'BUILD_IMAGE=%s\nIMAGE_REVISION=%s\nIMAGE_VERSION=%s\n' \
    "$BUILD_IMAGE" "$GITHUB_SHA" "$project_version" > build.env

if [ "$mode" = publish ]; then
    docker_config_dir=$(mktemp -d "${TMPDIR:-/tmp}/ais-ci-docker.XXXXXXXX")
    DOCKER_CONFIG=$docker_config_dir
    export DOCKER_CONFIG
    printf '%s' "$GHCR_TOKEN" | docker login ghcr.io \
        --username "$GITHUB_ACTOR" --password-stdin

    commit_image="${registry_image}:sha-${GITHUB_SHA}"
    channel_image="${registry_image}:${publish_tag}"
    docker tag "$BUILD_IMAGE" "$commit_image"
    docker push "$commit_image"
    docker tag "$BUILD_IMAGE" "$channel_image"
    docker push "$channel_image"

    # Tags can be reassigned by a later rebuild; use the registry digest for
    # a deployment reference that keeps identifying precisely this image.
    repo_digests=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$BUILD_IMAGE")
    image_digest=
    while IFS= read -r candidate; do
        case "$candidate" in
            "$registry_image"@sha256:*) image_digest=$candidate; break ;;
        esac
    done <<EOF
$repo_digests
EOF
    [ -n "$image_digest" ] || fail 'Published image has no registry digest'
    printf '%s\n' "${image_digest#*@sha256:}" | grep -Eq '^[0-9a-f]{64}$' || fail 'Registry returned an invalid image digest'
    printf 'PROVISIONER_IMAGE=%s\n' "$image_digest" > deploy.env
    printf 'Published %s and %s\nDeployment image: %s\n' "$commit_image" "$channel_image" "$image_digest"
fi
