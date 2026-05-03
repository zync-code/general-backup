#!/usr/bin/env bash
# Round-trip restore test using Docker.
#
# Steps:
#   1. Build an ubuntu:24.04 image with this repo baked in
#   2. Run 'general-backup capture' on the current host to produce a bundle
#   3. Copy the bundle into a container
#   4. Inside the container: ./bootstrap.sh then general-backup restore <bundle>
#   5. Assert:
#      - Each manifest.projects[].name has a .git at recorded SHA
#      - All manifest databases are listable
#      - pm2 jlist count matches manifest.components.pm2.process_count
#      - nginx -t is green
#
# Requirements: docker, age, general-backup CLI, postgres, redis on source host
#
# Usage:
#   bash tests/restore-in-docker.sh [BUNDLE]
#
# If BUNDLE is not given, a fresh capture is run first.
#
# Environment:
#   GB_BIN              — path to general-backup binary (default: bin/general-backup)
#   GB_AGE_RECIPIENT    — X25519 public key (required if no BUNDLE given)
#   GB_AGE_IDENTITY     — path to age private key (required)
#   GB_DOCKER_IMAGE     — Docker image tag to build (default: gb-restore-test:latest)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GB_BIN="${GB_BIN:-${REPO_ROOT}/bin/general-backup}"
GB_DOCKER_IMAGE="${GB_DOCKER_IMAGE:-gb-restore-test:latest}"
TMPDIR_BASE="$(mktemp -d /tmp/gb-docker-XXXXXX)"

pass()    { printf '\033[32mPASS\033[0m %s\n' "$1"; }
fail()    { printf '\033[31mFAIL\033[0m %s\n' "$1"; exit 1; }
info()    { printf '     %s\n' "$1"; }

cleanup() { rm -rf "${TMPDIR_BASE}"; }
trap cleanup EXIT

# ── Validate prerequisites ────────────────────────────────────────────────────

command -v docker >/dev/null 2>&1 || fail "docker is not installed"
[[ -n "${GB_AGE_IDENTITY:-}" ]] || fail "GB_AGE_IDENTITY must be set to the age private key path"

# ── Step 1: Produce (or accept) a bundle ──────────────────────────────────────

if [[ -n "${1:-}" ]]; then
    BUNDLE="$1"
    info "Using provided bundle: ${BUNDLE}"
else
    [[ -n "${GB_AGE_RECIPIENT:-}" ]] || fail "GB_AGE_RECIPIENT must be set to produce a bundle"
    info "Producing fresh capture bundle"
    BUNDLE_DIR="${TMPDIR_BASE}/bundle"
    mkdir -p "${BUNDLE_DIR}"
    "${GB_BIN}" capture \
        --age-recipient "${GB_AGE_RECIPIENT}" \
        --out "${BUNDLE_DIR}" \
        2>&1 | tee "${TMPDIR_BASE}/capture.log"
    BUNDLE="$(find "${BUNDLE_DIR}" -name 'general-backup-*.tar.zst' | head -1)"
    [[ -n "${BUNDLE}" ]] || fail "Capture produced no bundle"
fi

[[ -f "${BUNDLE}" ]] || fail "Bundle not found: ${BUNDLE}"
pass "Bundle ready: $(basename "${BUNDLE}")"

# ── Step 2: Extract manifest for assertions ───────────────────────────────────

MANIFEST_DIR="${TMPDIR_BASE}/manifest"
mkdir -p "${MANIFEST_DIR}"
tar -xf "${BUNDLE}" -C "${MANIFEST_DIR}" --wildcards '*/manifest.json' 2>/dev/null
MANIFEST="$(find "${MANIFEST_DIR}" -name 'manifest.json' | head -1)"
[[ -n "${MANIFEST}" ]] || fail "manifest.json not found in bundle"

# Parse assertions from manifest
read -ra PROJECT_NAMES < <(python3 -c "
import json
m = json.load(open('${MANIFEST}'))
print(' '.join(p['name'] for p in m.get('projects', [])))
")
read -ra PROJECT_SHAS < <(python3 -c "
import json
m = json.load(open('${MANIFEST}'))
print(' '.join(p.get('sha','') for p in m.get('projects', [])))
")
read -ra PROJECT_DIRS < <(python3 -c "
import json
m = json.load(open('${MANIFEST}'))
print(' '.join(p.get('project_dir','') for p in m.get('projects', [])))
")
PM2_COUNT="$(python3 -c "
import json
m = json.load(open('${MANIFEST}'))
print(m.get('components', {}).get('pm2', {}).get('process_count', 0))
")"
DB_NAMES=($(python3 -c "
import json
m = json.load(open('${MANIFEST}'))
dbs = m.get('components', {}).get('postgres', {}).get('databases', [])
print(' '.join(dbs))
"))

info "Projects to assert: ${PROJECT_NAMES[*]:-none}"
info "Databases to assert: ${DB_NAMES[*]:-none}"
info "PM2 count to assert: ${PM2_COUNT}"

# ── Step 3: Build Docker image ────────────────────────────────────────────────

DOCKERFILE="${TMPDIR_BASE}/Dockerfile"
cat > "${DOCKERFILE}" <<'EOF'
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -q && apt-get install -y -q \
    sudo git curl ca-certificates lsb-release python3 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 -s /bin/bash bot && \
    echo "bot ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/bot && \
    chmod 0440 /etc/sudoers.d/bot
WORKDIR /home/bot/projects/general-backup
COPY . .
RUN chown -R bot:bot /home/bot
USER bot
EOF

info "Building Docker image ${GB_DOCKER_IMAGE}"
docker build \
    -t "${GB_DOCKER_IMAGE}" \
    -f "${DOCKERFILE}" \
    "${REPO_ROOT}" \
    2>&1 | tail -5

pass "Docker image built"

# ── Step 4: Run restore in container ─────────────────────────────────────────

CONTAINER_BUNDLE="/tmp/$(basename "${BUNDLE}")"
CONTAINER_KEY="/tmp/age-key.txt"

info "Running restore inside container"
RESTORE_LOG="${TMPDIR_BASE}/restore.log"

docker run --rm \
    -v "${BUNDLE}:${CONTAINER_BUNDLE}:ro" \
    -v "${GB_AGE_IDENTITY}:${CONTAINER_KEY}:ro" \
    --privileged \
    "${GB_DOCKER_IMAGE}" \
    bash -c "
        set -euo pipefail
        cd /home/bot/projects/general-backup
        sudo bash bootstrap.sh
        general-backup restore '${CONTAINER_BUNDLE}' --age-identity '${CONTAINER_KEY}'
    " 2>&1 | tee "${RESTORE_LOG}"

RESTORE_EXIT="${PIPESTATUS[0]}"
[[ "${RESTORE_EXIT}" -eq 0 ]] || fail "restore exited ${RESTORE_EXIT}"
pass "Restore completed with exit 0"

# ── Step 5: Assertions inside container ──────────────────────────────────────

info "Running post-restore assertions"

ASSERT_SCRIPT="${TMPDIR_BASE}/assert.sh"
{
    echo "#!/usr/bin/env bash"
    echo "set -euo pipefail"
    echo "FAILURES=()"

    # 5a: Each project has .git at recorded SHA
    for i in "${!PROJECT_NAMES[@]}"; do
        name="${PROJECT_NAMES[$i]}"
        sha="${PROJECT_SHAS[$i]}"
        dir="${PROJECT_DIRS[$i]}"
        echo "if [[ -d '${dir}/.git' ]]; then"
        echo "    actual=\$(git -C '${dir}' rev-parse HEAD 2>/dev/null || echo missing)"
        echo "    if [[ \"\$actual\" != '${sha}' ]]; then"
        echo "        FAILURES+=(\"${name}: HEAD=\$actual want ${sha}\")"
        echo "    fi"
        echo "else"
        echo "    FAILURES+=(\"${name}: .git missing at ${dir}\")"
        echo "fi"
    done

    # 5b: Databases listable
    for db in "${DB_NAMES[@]}"; do
        echo "if ! psql -U postgres -lqt 2>/dev/null | grep -qw '${db}'; then"
        echo "    FAILURES+=(\"database not listable: ${db}\")"
        echo "fi"
    done

    # 5c: PM2 process count matches
    if [[ "${PM2_COUNT}" -gt 0 ]]; then
        echo "actual_pm2=\$(pm2 jlist 2>/dev/null | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
        echo "if [[ \"\$actual_pm2\" -ne '${PM2_COUNT}' ]]; then"
        echo "    FAILURES+=(\"pm2 count: got \$actual_pm2 want ${PM2_COUNT}\")"
        echo "fi"
    fi

    # 5d: nginx -t
    echo "if ! nginx -t 2>/dev/null; then"
    echo "    FAILURES+=(\"nginx -t failed\")"
    echo "fi"

    echo "if [[ \${#FAILURES[@]} -gt 0 ]]; then"
    echo "    printf 'ASSERTION FAILED: %s\n' \"\${FAILURES[@]}\""
    echo "    exit 1"
    echo "fi"
    echo "echo 'All assertions passed'"
} > "${ASSERT_SCRIPT}"
chmod +x "${ASSERT_SCRIPT}"

docker run --rm \
    -v "${BUNDLE}:${CONTAINER_BUNDLE}:ro" \
    -v "${GB_AGE_IDENTITY}:${CONTAINER_KEY}:ro" \
    -v "${ASSERT_SCRIPT}:/tmp/assert.sh:ro" \
    --privileged \
    "${GB_DOCKER_IMAGE}" \
    bash -c "
        sudo bash bootstrap.sh >/dev/null 2>&1
        general-backup restore '${CONTAINER_BUNDLE}' --age-identity '${CONTAINER_KEY}' >/dev/null 2>&1
        sudo bash /tmp/assert.sh
    " 2>&1

ASSERT_EXIT="${PIPESTATUS[0]}"
[[ "${ASSERT_EXIT}" -eq 0 ]] || fail "Post-restore assertions failed (exit ${ASSERT_EXIT})"

pass "All post-restore assertions passed"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "All restore-in-docker tests passed."
