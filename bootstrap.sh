#!/usr/bin/env bash
# bootstrap.sh — install the full toolchain on a fresh Ubuntu 24.04 host.
#
# Idempotent: every step checks whether the required version is already present
# before installing. Re-running after a partial install is safe.
#
# Usage:
#   sudo bash bootstrap.sh
#
# Environment overrides:
#   GB_FORCE_OS=1     — skip the Ubuntu 24.04 check
#   GB_NODE_VERSION   — Node.js major version to install (default: 18)
#   PNPM_VERSION      — pnpm version (default: from manifest.json or 'latest')
#   PM2_VERSION       — pm2 version  (default: from manifest.json or 'latest')

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Helpers ───────────────────────────────────────────────────────────────────

green()  { printf '\033[32m✓\033[0m %s\n' "$*"; }
yellow() { printf '\033[33m→\033[0m %s\n' "$*"; }
red()    { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }
die()    { red "$*"; exit 1; }

version_ge() {
    # Returns 0 if $1 >= $2 (semver-ish comparison on major.minor)
    local have want
    have="$(echo "$1" | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    want="$(echo "$2" | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    [[ "$(printf '%s\n%s' "$want" "$have" | sort -V | head -1)" == "$want" ]]
}

# ── OS check ──────────────────────────────────────────────────────────────────

if [[ "${GB_FORCE_OS:-0}" != "1" ]]; then
    if ! grep -qi "ubuntu 24.04" /etc/os-release 2>/dev/null; then
        die "This script targets Ubuntu 24.04. Set GB_FORCE_OS=1 to override."
    fi
fi

# Must run as root (or via sudo)
if [[ "$(id -u)" -ne 0 ]]; then
    die "bootstrap.sh must be run as root (use: sudo bash bootstrap.sh)"
fi

green "OS check passed"

# ── Read versions from manifest (if available) ───────────────────────────────

MANIFEST="${REPO_ROOT}/manifest.json"
PNPM_VERSION="${PNPM_VERSION:-}"
PM2_VERSION="${PM2_VERSION:-}"
GB_NODE_VERSION="${GB_NODE_VERSION:-18}"

if [[ -f "${MANIFEST}" ]]; then
    _tc_pnpm="$(python3 -c "import json; m=json.load(open('${MANIFEST}')); print(m.get('toolchain',{}).get('pnpm',''))" 2>/dev/null || echo "")"
    _tc_pm2="$(python3 -c  "import json; m=json.load(open('${MANIFEST}')); print(m.get('toolchain',{}).get('pm2',''))" 2>/dev/null || echo "")"
    [[ -n "${_tc_pnpm}" ]] && PNPM_VERSION="${_tc_pnpm}"
    [[ -n "${_tc_pm2}"  ]] && PM2_VERSION="${_tc_pm2}"
fi

# ── Step 1: apt packages ──────────────────────────────────────────────────────

yellow "apt: updating package lists"
apt-get update -q

APT_PACKAGES=(
    tar zstd age curl git build-essential
    nginx redis-server
    python3 python3-venv python3-pip
    ca-certificates gnupg lsb-release
    tmux sudo
)

# PostgreSQL 16 (from PostgreSQL apt repository if needed)
if dpkg -l "postgresql-16" &>/dev/null; then
    green "postgresql-16 already installed"
else
    yellow "apt: adding PostgreSQL 16 repository"
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/keyrings/postgresql-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/postgresql-archive-keyring.gpg] \
        https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    apt-get update -q
    APT_PACKAGES+=(postgresql-16 postgresql-client-16)
fi

yellow "apt: installing packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${APT_PACKAGES[@]}"
green "apt packages installed"

# ── Step 2: Node.js via NodeSource ───────────────────────────────────────────

WANT_NODE_MAJOR="${GB_NODE_VERSION}"
HAVE_NODE_MAJOR="$(node --version 2>/dev/null | grep -oE '[0-9]+' | head -1 || echo 0)"

if [[ "${HAVE_NODE_MAJOR}" == "${WANT_NODE_MAJOR}" ]]; then
    green "Node.js $(node --version) already installed"
else
    yellow "Node.js: installing v${WANT_NODE_MAJOR}.x via NodeSource"
    curl -fsSL "https://deb.nodesource.com/setup_${WANT_NODE_MAJOR}.x" | bash -
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q nodejs
    green "Node.js $(node --version) installed"
fi

# ── Step 3: pnpm via corepack ─────────────────────────────────────────────────

if ! command -v corepack &>/dev/null; then
    yellow "corepack: enabling"
    corepack enable
fi

PNPM_SPEC="${PNPM_VERSION:-latest}"
HAVE_PNPM="$(pnpm --version 2>/dev/null || echo '')"

if [[ -z "${HAVE_PNPM}" ]] || [[ "${PNPM_SPEC}" != "latest" && "${HAVE_PNPM}" != "${PNPM_VERSION}" ]]; then
    yellow "pnpm: installing ${PNPM_SPEC}"
    corepack prepare "pnpm@${PNPM_SPEC}" --activate
    green "pnpm $(pnpm --version) installed"
else
    green "pnpm ${HAVE_PNPM} already installed"
fi

# ── Step 4: pm2 via npm ───────────────────────────────────────────────────────

PM2_SPEC="${PM2_VERSION:-latest}"
HAVE_PM2="$(pm2 --version 2>/dev/null || echo '')"

if [[ -z "${HAVE_PM2}" ]] || [[ "${PM2_SPEC}" != "latest" && "${HAVE_PM2}" != "${PM2_VERSION}" ]]; then
    yellow "pm2: installing ${PM2_SPEC}"
    npm install -g "pm2@${PM2_SPEC}" --quiet || true   # best effort
    green "pm2 $(pm2 --version 2>/dev/null || echo '?') installed"
else
    green "pm2 ${HAVE_PM2} already installed"
fi

# ── Step 5: claude-code CLI ───────────────────────────────────────────────────

if command -v claude &>/dev/null; then
    green "claude-code CLI already installed ($(claude --version 2>/dev/null || echo '?'))"
else
    yellow "claude-code: installing via official installer"
    curl -fsSL https://claude.ai/install.sh | bash || {
        yellow "claude-code: primary installer failed — trying npm"
        npm install -g @anthropic-ai/claude-code --quiet || true
    }
    if command -v claude &>/dev/null; then
        green "claude-code installed"
    else
        yellow "claude-code: could not install — agent-mode restore will not work"
    fi
fi

# ── Step 6: symlink bin/general-backup → /usr/local/bin ──────────────────────

GB_BIN="${REPO_ROOT}/bin/general-backup"
GB_LINK="/usr/local/bin/general-backup"

if [[ -x "${GB_BIN}" ]]; then
    if [[ -L "${GB_LINK}" ]] || [[ -f "${GB_LINK}" ]]; then
        green "general-backup already in /usr/local/bin"
    else
        ln -sf "${GB_BIN}" "${GB_LINK}"
        green "general-backup symlinked to /usr/local/bin"
    fi
else
    yellow "general-backup binary not found at ${GB_BIN} — skipping symlink"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
green "bootstrap complete"
echo ""
echo "  Node.js:  $(node --version 2>/dev/null || echo N/A)"
echo "  pnpm:     $(pnpm --version 2>/dev/null || echo N/A)"
echo "  pm2:      $(pm2 --version 2>/dev/null || echo N/A)"
echo "  postgres: $(psql --version 2>/dev/null | head -1 || echo N/A)"
echo "  redis:    $(redis-server --version 2>/dev/null | head -1 || echo N/A)"
echo "  claude:   $(claude --version 2>/dev/null || echo N/A)"
echo ""
echo "Next step: general-backup restore-agent <bundle.tar.zst> --age-identity <key.txt>"
