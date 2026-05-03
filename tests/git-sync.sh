#!/usr/bin/env bash
# Test the git-sync phase semantics:
#
#   1. A project with uncommitted changes + --no-snapshot-commit → exit 5
#   2. A project with uncommitted changes + default (--allow-snapshot-commit) →
#      exit 0, manifest.projects[] records the new SHA, origin has that SHA
#
# The test wires a synthetic projects.json that points at a temp project with
# a local bare-repo origin so no real GitHub I/O is needed.
#
# Usage:
#   bash tests/git-sync.sh
#
# Environment:
#   GB_BIN  — path to general-backup binary (default: bin/general-backup)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GB_BIN="${GB_BIN:-${REPO_ROOT}/bin/general-backup}"
TMPDIR_BASE="$(mktemp -d /tmp/gb-git-sync-XXXXXX)"

pass()    { printf '\033[32mPASS\033[0m %s\n' "$1"; }
fail()    { printf '\033[31mFAIL\033[0m %s\n' "$1"; exit 1; }
info()    { printf '     %s\n' "$1"; }

cleanup() { rm -rf "${TMPDIR_BASE}"; }
trap cleanup EXIT

# ── Setup: bare-repo origin + working clone ───────────────────────────────────

BARE_REPO="${TMPDIR_BASE}/origin.git"
WORK_REPO="${TMPDIR_BASE}/work"
STAGING_DIR="${TMPDIR_BASE}/staging"
PROJECTS_JSON="${TMPDIR_BASE}/projects.json"

# Create bare remote
git init --bare "${BARE_REPO}" -q
git -C "${BARE_REPO}" config receive.denyCurrentBranch ignore

# Clone into working copy
git clone "${BARE_REPO}" "${WORK_REPO}" -q 2>/dev/null
git -C "${WORK_REPO}" config user.email "test@example.com"
git -C "${WORK_REPO}" config user.name "Test"

# Initial commit so the repo has a HEAD
echo "initial" > "${WORK_REPO}/readme.txt"
git -C "${WORK_REPO}" add readme.txt
git -C "${WORK_REPO}" commit -m "initial" -q
git -C "${WORK_REPO}" push origin HEAD -q 2>/dev/null

INITIAL_SHA="$(git -C "${WORK_REPO}" rev-parse HEAD)"

# Make a dirty change (tracked file, modified)
echo "dirty change" >> "${WORK_REPO}/readme.txt"

# Write a synthetic projects.json pointing at this project
cat > "${PROJECTS_JSON}" <<EOF
{
  "projects": {
    "test-project": {
      "name": "test-project",
      "github_repo": "file://${BARE_REPO}",
      "project_dir": "${WORK_REPO}",
      "deploy_type": "nginx",
      "task_backend": "github"
    }
  }
}
EOF

mkdir -p "${STAGING_DIR}"

# ── Test 1: --no-snapshot-commit → exit 5 ────────────────────────────────────

info "Running: capture --no-snapshot-commit on dirty project"

age-keygen -o "${TMPDIR_BASE}/key.txt" 2>/dev/null
AGE_RECIPIENT="$(age-keygen -y "${TMPDIR_BASE}/key.txt" 2>/dev/null)"

EXIT_CODE=0
"${GB_BIN}" capture \
    --no-snapshot-commit \
    --age-recipient "${AGE_RECIPIENT}" \
    --out "${STAGING_DIR}" \
    2>&1 || EXIT_CODE=$?

[[ "${EXIT_CODE}" -eq 5 ]] || \
    fail "--no-snapshot-commit on dirty repo should exit 5, got ${EXIT_CODE}"

pass "--no-snapshot-commit exits 5 on dirty project"

# ── Test 2: default (allow snapshot commit) → exit 0, SHA pushed ─────────────

info "Running: capture (default) on dirty project"

BUNDLE_DIR="${TMPDIR_BASE}/bundle-out"
mkdir -p "${BUNDLE_DIR}"

"${GB_BIN}" capture \
    --age-recipient "${AGE_RECIPIENT}" \
    --out "${BUNDLE_DIR}" \
    2>&1 | tee "${TMPDIR_BASE}/capture.log"

[[ "${PIPESTATUS[0]}" -eq 0 ]] || fail "Default capture exited non-zero"

pass "Default capture exits 0"

# ── Test 3: new SHA is recorded in manifest.projects[] ───────────────────────

BUNDLE="$(find "${BUNDLE_DIR}" -name 'general-backup-*.tar.zst' | head -1)"
[[ -n "${BUNDLE}" ]] || fail "No bundle produced"

EXTRACT_DIR="${TMPDIR_BASE}/extracted"
mkdir -p "${EXTRACT_DIR}"
tar -xf "${BUNDLE}" -C "${EXTRACT_DIR}" 2>/dev/null

MANIFEST="$(find "${EXTRACT_DIR}" -name 'manifest.json' | head -1)"
[[ -n "${MANIFEST}" ]] || fail "manifest.json not found in bundle"

MANIFEST_SHA="$(python3 -c "
import json, sys
m = json.load(open('${MANIFEST}'))
projects = m.get('projects', [])
for p in projects:
    if p.get('name') == 'test-project':
        print(p.get('sha', ''))
        sys.exit(0)
print('')
")"

[[ -n "${MANIFEST_SHA}" ]] || fail "test-project not found in manifest.projects[]"
[[ "${MANIFEST_SHA}" != "${INITIAL_SHA}" ]] || \
    fail "manifest SHA is still the initial SHA (snapshot commit was not made)"

pass "manifest.projects[] records new SHA after snapshot commit"

# ── Test 4: the new SHA exists on the origin ──────────────────────────────────

git -C "${BARE_REPO}" cat-file -t "${MANIFEST_SHA}" 2>/dev/null | grep -q "commit" || \
    fail "SHA ${MANIFEST_SHA} not found on origin"

pass "Snapshot commit SHA exists on origin"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "All git-sync tests passed."
