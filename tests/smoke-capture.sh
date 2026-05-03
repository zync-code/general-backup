#!/usr/bin/env bash
# Smoke test for 'general-backup capture'.
#
# Tests:
#   1. dry-run exits 0 and prints each expected phase
#   2. real capture produces a valid .tar.zst bundle
#   3. 'general-backup verify' passes on the bundle
#   4. No plaintext secret patterns appear in the cleartext bundle files
#
# Usage:
#   bash tests/smoke-capture.sh
#
# Environment:
#   GB_AGE_RECIPIENT  — X25519 public key for encryption. If unset, a
#                       throw-away key is generated for the test run.
#   GB_BIN            — path to general-backup binary (default: bin/general-backup)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GB_BIN="${GB_BIN:-${REPO_ROOT}/bin/general-backup}"
TMPDIR_BASE="$(mktemp -d /tmp/gb-smoke-XXXXXX)"
KEY_FILE="${TMPDIR_BASE}/test.key"
BUNDLE_DIR="${TMPDIR_BASE}/bundles"

pass() { printf '\033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$1"; exit 1; }
info() { printf '     %s\n' "$1"; }

cleanup() { rm -rf "${TMPDIR_BASE}"; }
trap cleanup EXIT

# ── Setup ──────────────────────────────────────────────────────────────────────

mkdir -p "${BUNDLE_DIR}"

if [[ -z "${GB_AGE_RECIPIENT:-}" ]]; then
    info "No GB_AGE_RECIPIENT set — generating ephemeral age key for test"
    age-keygen -o "${KEY_FILE}" 2>/dev/null
    GB_AGE_RECIPIENT="$(age-keygen -y "${KEY_FILE}" 2>/dev/null)"
    GB_AGE_IDENTITY="${KEY_FILE}"
else
    GB_AGE_IDENTITY="${GB_AGE_IDENTITY:-}"
fi

EXPECTED_PHASES=(
    preflight git-sync inventory packages system nginx cron
    postgres redis pm2 state secrets checksums package
)

# ── Test 1: dry-run exits 0 and mentions all capture phases ───────────────────

info "Running: capture --dry-run"
DRY_OUTPUT="$("${GB_BIN}" capture --dry-run --age-recipient "${GB_AGE_RECIPIENT}" 2>&1)" || \
    fail "dry-run exited non-zero"

for phase in "${EXPECTED_PHASES[@]}"; do
    echo "${DRY_OUTPUT}" | grep -qi "${phase}" || \
        fail "dry-run output missing phase: ${phase}"
done
pass "dry-run exits 0 and mentions all phases"

# ── Test 2: real capture produces a .tar.zst bundle ──────────────────────────

info "Running: real capture into ${BUNDLE_DIR}"
"${GB_BIN}" capture \
    --age-recipient "${GB_AGE_RECIPIENT}" \
    --out "${BUNDLE_DIR}" \
    2>&1 | tee "${TMPDIR_BASE}/capture.log"

BUNDLE="$(find "${BUNDLE_DIR}" -name 'general-backup-*.tar.zst' | head -1)"
[[ -n "${BUNDLE}" ]] || fail "No bundle produced in ${BUNDLE_DIR}"
[[ -f "${BUNDLE}" ]] || fail "Bundle path is not a file: ${BUNDLE}"
pass "real capture produced bundle: $(basename "${BUNDLE}")"

# ── Test 3: verify passes ─────────────────────────────────────────────────────

info "Running: verify on $(basename "${BUNDLE}")"
VERIFY_ARGS=("${BUNDLE}")
[[ -n "${GB_AGE_IDENTITY:-}" ]] && VERIFY_ARGS+=("--age-identity" "${GB_AGE_IDENTITY}")

"${GB_BIN}" verify "${VERIFY_ARGS[@]}" 2>&1 | tee "${TMPDIR_BASE}/verify.log"
VERIFY_EXIT="${PIPESTATUS[0]}"
[[ "${VERIFY_EXIT}" -eq 0 ]] || fail "verify exited ${VERIFY_EXIT}"
pass "verify passes on produced bundle"

# ── Test 4: no plaintext secrets in the unencrypted portion ──────────────────

SECRET_PATTERN='ghp_|gho_|sk-[a-zA-Z0-9]{20,}|password\s*=|BEGIN [A-Z]+ PRIVATE KEY'
EXTRACT_DIR="${TMPDIR_BASE}/extracted"
mkdir -p "${EXTRACT_DIR}"

info "Extracting bundle to scan for plaintext secrets"
tar -xf "${BUNDLE}" -C "${EXTRACT_DIR}" 2>/dev/null

HITS=()
while IFS= read -r -d '' f; do
    # Skip secrets.age — that's where secrets are supposed to live
    [[ "${f}" == *secrets.age ]] && continue
    # Binary files can contain random byte patterns; scan only text-like files
    if file "${f}" | grep -qE 'text|JSON|script'; then
        if grep -qEi "${SECRET_PATTERN}" "${f}" 2>/dev/null; then
            HITS+=("${f#${EXTRACT_DIR}/}")
        fi
    fi
done < <(find "${EXTRACT_DIR}" -type f -print0)

if [[ ${#HITS[@]} -gt 0 ]]; then
    info "Plaintext secret patterns found in:"
    for h in "${HITS[@]}"; do
        info "  ${h}"
    done
    fail "Secret patterns found outside secrets.age (${#HITS[@]} file(s))"
fi
pass "No plaintext secret patterns outside secrets.age"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "All smoke-capture tests passed."
