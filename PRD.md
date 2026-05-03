# general-backup — Full-Server Snapshot & Restore Toolkit
## Product Requirements Document (PRD)

| | |
|---|---|
| **Version** | 1.0.0 |
| **Date** | May 2026 |
| **Status** | DRAFT — implementation start |
| **Platform** | Bash + Python 3 CLI, Linux (Ubuntu 24.04 reference target) |
| **Public link** | https://github.com/zync-code/general-backup (README is the public docs) |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Goals & Non-Goals](#3-goals--non-goals)
4. [Reference Server Inventory](#4-reference-server-inventory)
5. [Scope — What Is Captured / Out of Scope](#5-scope--what-is-captured--out-of-scope)
6. [Architecture & Bundle Format](#6-architecture--bundle-format)
7. [CLI Surface](#7-cli-surface)
8. [Capture Pipeline](#8-capture-pipeline)
9. [Restore / Bootstrap Pipeline](#9-restore--bootstrap-pipeline)
10. [Secrets & Encryption](#10-secrets--encryption)
11. [Idempotency, Safety, Verification](#11-idempotency-safety-verification)
12. [Public Documentation](#12-public-documentation)
13. [Implementation Roadmap (Issues)](#13-implementation-roadmap-issues)
14. [Test Plan](#14-test-plan)
15. [Risks & Mitigation](#15-risks--mitigation)
16. [Success Metrics](#16-success-metrics)

---

## 1. Executive Summary

`general-backup` is a single-binary-style CLI toolkit that produces a **complete, restorable snapshot of a Linux server**, and replays that snapshot on a fresh box so the new machine becomes byte-for-byte equivalent to the source.

It targets the operator who runs many small projects on one VPS: project source trees, PostgreSQL/Redis data, nginx vhosts, PM2 apps, system users, SSH keys, cron, agent/skill orchestration directories. A backup is one tar bundle plus a manifest. A restore is one command on a clean Ubuntu install.

The deliverable is:

1. A GitHub repo `zync-code/general-backup` with the toolkit and thorough README — this is the **public documentation link**.
2. Two executables: `general-backup capture` and `general-backup restore`.
3. A `bootstrap.sh` for the fresh box, plus a `verify` mode for round-trip testing.

---

## 2. Problem Statement

The current server hosts ~20 projects, 10 PostgreSQL databases, a Redis with 16k+ keys, 20 PM2 processes, 5 nginx vhosts, an orchestrator with logs/state, and a Telegram bot. There is **no automated way** to:

- Move all of this to a new VPS in a single command.
- Take a periodic full snapshot for disaster recovery.
- Reproduce the box deterministically (apt packages, node version, pnpm globals, PM2 ecosystem).
- Audit *what* would actually be restored before running a restore.

Manual recovery would mean re-running a year's worth of ad-hoc setup steps. This tool is the codified version of those steps.

---

## 3. Goals & Non-Goals

### Goals (P0)

- **Single capture command** that produces one bundle file containing everything required to rebuild the server.
- **Single restore command** that, given a bundle and a fresh Ubuntu 24.04 host, brings up an identical server: same projects on disk, same DB contents, same nginx config, same PM2 apps online, same users, same agent/skill setup.
- **Idempotent restore** — running it twice yields the same end state; partial failures are resumable.
- **Encrypted secrets** — `.env`, SSH private keys, API tokens never appear in the bundle in plaintext.
- **Public README** on GitHub describing exactly how the tool works and how to use it.
- **Self-contained** — no cloud dependency. Bundle is a local file the user can scp anywhere.

### Goals (P1)

- **Diff mode** — show what would change on the target before applying.
- **Selective capture/restore** — `--only projects,postgres` style filters.
- **Compression + checksumming** for bundle integrity.
- **Cron-installable** for periodic snapshots.

### Non-Goals (v1)

- Continuous replication or streaming backup.
- Cross-distro restore (Debian, Alpine, RHEL — Ubuntu 24.04 only in v1).
- Backing up arbitrary system-wide state outside the documented scope (kernel modules, custom systemd units beyond a documented allowlist).
- Multi-node cluster restore.
- Web UI.

---

## 4. Reference Server Inventory

This is the inventory of the source server. Restore must reproduce all of it.

| Layer | What lives here |
|---|---|
| **OS** | Ubuntu 24.04.1 LTS (noble), single regular user `bot` (uid 1000) |
| **Filesystem footprint** | `/home/bot` 18G — `projects/` 11G, `.claude/` 153M, `.orchestrator/` 152M |
| **Services (systemd)** | `nginx`, `postgresql@16-main`, `redis-server`, `ssh`, `cron`, `xinetd`, `rsyslog` |
| **PostgreSQL 16** | 10 user databases: `asap`, `automotive_dev`, `bar7`, `bar7_dev`, `custom_linear`, `devpulse`, `mojpausal`, `pnlmaker`, `procvat`, `qubit` (each with its own role) |
| **Redis** | db0 (~16,540 keys), db1 (~13 keys) |
| **PM2 (ecosystem)** | ~20 processes including `automotive-{web,api,scraper}`, `dev-pulse-{web,api,worker}`, `asap-{api,web}`, `restoran-{api,web}`, `lekopis-web`, `moj-pausal-web`, `pnl-maker-backend`, `custom-linear-api`, `qa-tool-web`, `qr-scanner-qr-generator`, `procvat`, `command-center`, telegram bot worker |
| **nginx** | `/etc/nginx/sites-enabled/`: `landlify.com`, `lekopis.com`, `main`, `mojpausal.com`, `viewermd.com`. `/etc/nginx/conf.d/`: `security.conf`, `ws-upgrade-map.conf`. Per-app includes under `~/.orchestrator/nginx/{sites,locations}/` |
| **Project trees** | 20 directories under `/home/bot/projects/` (Automotive, Bar7, Lekopis, Lokica, Perica, TestFlow, ajjej, asap, command-center, copy-trading, custom-linear, dev-pulse, godswill-presentation, moj-pausal, nikola, pnl-maker, procvat, qa-tool, qr-scanner, restoran). Each has `.env`, `node_modules` (excludable), source code, and a git remote |
| **Orchestrator** | `~/.orchestrator/`: commands/, lib/, bot/, config/projects.json, logs/, claude/commands/ — full multi-agent automation runtime |
| **Claude config** | `~/.claude/`: settings.json, settings.local.json, plugins/, commands/, plus state dirs (projects, sessions, history, plans) — capture configs and plugins, exclude history caches |
| **System config** | `~/.config/{gh,pnpm,turborepo,...}` — token-bearing; treat as secrets |
| **SSH** | `~/.ssh/id_ed25519{,.pub}`, `known_hosts`, `authorized_keys` |
| **Cron** | bot crontab (currently empty, but include in capture); root crontab if accessible |
| **Toolchain** | Node 18.19.1, pnpm, pm2 6.0.14, Python 3.12.3, ~1067 apt packages |
| **No-op** | No Docker workloads. No letsencrypt certs in `/etc/letsencrypt/live/`. No MySQL/MongoDB/ES. |

A full live inventory is regenerated at capture time — this table is the **starting reference**, not a hardcoded list.

---

## 5. Scope — What Is Captured / Out of Scope

### IN scope (must be in the bundle)

- All directories under `/home/bot/projects/` excluding `node_modules`, `.next`, `dist`, `build`, `.turbo`, `.cache`, `coverage` (regenerable from `pnpm install`).
- `~/.orchestrator/` in full (configs, logs optional via flag, code).
- `~/.claude/` (settings, plugins, commands, plans) — exclude `cache/`, `paste-cache/`, `shell-snapshots/`, `telemetry/`, `file-history/`, `history.jsonl`.
- `~/.config/` (gh tokens, pnpm config, turborepo).
- `~/.ssh/` (full directory, encrypted in the secrets vault).
- `~/.bashrc`, `~/.profile`, `~/.bash_history` (optional).
- `pg_dumpall --globals-only` + per-database `pg_dump --format=custom` for every non-template DB.
- `redis-cli --rdb` snapshot of running Redis, plus `CONFIG GET *` capture for non-default settings.
- `pm2 save` output (`~/.pm2/dump.pm2`) and `pm2 jlist` JSON for verification.
- nginx: `/etc/nginx/nginx.conf`, `/etc/nginx/sites-available/*`, `/etc/nginx/sites-enabled/*` (as symlink list), `/etc/nginx/conf.d/*`.
- Cron: `crontab -l` for `bot`; `/etc/cron.d/*`, `/etc/cron.{daily,hourly,weekly,monthly}/*` (root-readable subset best-effort).
- System users: `/etc/passwd`, `/etc/group`, `/etc/shadow` (encrypted), `/etc/sudoers.d/*` — sufficient to recreate non-system users and their groups.
- Package lists: `dpkg --get-selections`, `apt-mark showmanual`, `pnpm ls -g --json`, `pip3 freeze`, `npm ls -g --json`.
- All `.env*` files discovered under projects (encrypted).
- A `manifest.json` describing source hostname, OS, captured version, file checksums, capture timestamp.

### OUT of scope (v1)

- Full `/etc` snapshot (only documented files above).
- `/var/log` (regenerable; pollutes bundle).
- Kernel/initramfs/grub.
- Mail spool, printer config.
- Hardware-specific tunables (`/etc/sysctl.d` only if user opts in via `--include-sysctl`).
- Anything outside the documented allowlist — exotic state must be added via PR.

---

## 6. Architecture & Bundle Format

### High-level flow

```
[ source server ]                       [ target server ]
       |                                       |
       | general-backup capture                | general-backup restore bundle.tar.zst
       v                                       v
  bundle.tar.zst  --- scp/usb/cloud --->   bootstrap.sh + restore phases
       |                                       |
   manifest.json                          identical state
   secrets.age (encrypted)
   data/{pg,redis,files,...}
```

### Bundle layout (inside the tarball)

```
general-backup-<host>-<UTCstamp>/
├── manifest.json                  # version, source host, OS, package counts, sha256 of every file
├── README.txt                     # human-readable summary
├── secrets.age                    # age-encrypted vault (env files, ssh keys, tokens, /etc/shadow)
├── data/
│   ├── postgres/
│   │   ├── globals.sql            # roles, tablespaces (no passwords in plain — pulled from secrets.age)
│   │   └── <dbname>.dump          # pg_dump --format=custom per database
│   ├── redis/
│   │   ├── dump.rdb               # snapshot
│   │   └── config.json            # captured non-default CONFIG values
│   ├── pm2/
│   │   ├── dump.pm2               # pm2 save output
│   │   └── jlist.json             # full process metadata for diff
│   ├── nginx/
│   │   ├── nginx.conf
│   │   ├── sites-available/
│   │   ├── sites-enabled.txt      # list of symlink targets
│   │   └── conf.d/
│   ├── cron/
│   │   ├── bot.crontab
│   │   └── etc-cron.d/...
│   └── system/
│       ├── passwd.delta           # only non-system users
│       ├── group.delta
│       └── sudoers.d/
├── files/
│   ├── home-bot.tar.zst           # /home/bot tree, with documented exclusions
│   ├── orchestrator.tar.zst       # ~/.orchestrator
│   ├── claude.tar.zst             # ~/.claude (filtered)
│   └── config.tar.zst             # ~/.config
├── packages/
│   ├── apt-manual.txt             # apt-mark showmanual
│   ├── apt-selections.txt         # dpkg --get-selections
│   ├── npm-global.json
│   ├── pnpm-global.json
│   └── pip3-freeze.txt
└── checksums.sha256               # SHA-256 of every file above; signed if --sign provided
```

### Manifest schema (manifest.json)

```json
{
  "schema_version": 1,
  "tool_version": "1.0.0",
  "captured_at": "2026-05-03T18:42:11Z",
  "source": {
    "hostname": "5t3i.c.time4vps.cloud",
    "os": "Ubuntu 24.04.1 LTS",
    "kernel": "6.8.0",
    "user": "bot",
    "uid": 1000
  },
  "components": {
    "postgres": { "databases": ["asap", "automotive_dev", "..."], "version": "16" },
    "redis": { "db_count": 2, "key_count": 16553 },
    "pm2": { "process_count": 20 },
    "nginx": { "vhost_count": 5 },
    "files": { "home_bot_size_bytes": 19327352832 },
    "packages": { "apt_manual": 412, "pnpm_global": 8 }
  },
  "exclusions": ["node_modules", ".next", "dist", "build", ".cache", ".turbo", ".claude/cache", ".claude/paste-cache"],
  "checksums_file": "checksums.sha256",
  "secrets_encrypted": true
}
```

---

## 7. CLI Surface

```bash
# Capture
general-backup capture \
    [--out PATH]                  # default: ./general-backup-<host>-<stamp>.tar.zst
    [--age-recipient RECIPIENT]   # required for secrets encryption (or --age-passphrase)
    [--include LIST]              # comma list: postgres,redis,files,pm2,nginx,cron,packages,system,all (default: all)
    [--exclude LIST]              # subtract from --include
    [--dry-run]                   # print plan, do nothing
    [--sign KEYFILE]              # sign checksums.sha256
    [--quiet | --verbose]

# Restore
general-backup restore <bundle> \
    [--target-user bot]
    [--age-identity FILE]         # required if bundle has secrets.age
    [--phases LIST]               # bootstrap,packages,users,files,postgres,redis,nginx,pm2,cron (default: all in order)
    [--skip-phases LIST]
    [--dry-run]                   # show diff, no changes
    [--force]                     # overwrite existing data; required if target is non-empty

# Verify
general-backup verify <bundle>
    # Checks: tarball integrity, sha256 matches, manifest schema, secrets decryptable

# Diff (planned-vs-current)
general-backup diff <bundle>
    # For each component: show what restore would change on this host

# Bootstrap-only (fresh box, no bundle yet)
general-backup install
    # Installs apt deps, node, pnpm, pm2, postgres, redis, nginx, age — minimum to receive a bundle
```

Exit codes: `0` ok, `1` user error, `2` integrity error, `3` partial restore (resumable), `4` permission error.

---

## 8. Capture Pipeline

Phases run in this order; each phase is independently re-runnable.

1. **preflight** — confirm source identity, free disk for staging, required tools (`pg_dump`, `redis-cli`, `tar`, `zstd`, `age`).
2. **inventory** — write `manifest.json` skeleton (hostname, OS, sizes).
3. **packages** — `apt-mark showmanual`, `dpkg --get-selections`, `pnpm ls -g --json`, `npm ls -g --json`, `pip3 freeze`.
4. **system** — extract non-system entries from `/etc/passwd`, `/etc/group`; copy `/etc/sudoers.d/*` into `secrets.age` (sudoers can leak); shadow lines for non-system users into `secrets.age`.
5. **nginx** — copy `/etc/nginx/nginx.conf`, `sites-available/`, `conf.d/`; record `sites-enabled` link map.
6. **cron** — `crontab -l -u bot`, `/etc/cron.d/`, `/etc/cron.{daily,hourly,weekly,monthly}/`.
7. **postgres** — `pg_dumpall --globals-only --no-role-passwords` (passwords pulled separately into secrets), then per-DB `pg_dump --format=custom --compress=9`.
8. **redis** — `redis-cli SAVE` then copy the rdb; capture `CONFIG GET *` to JSON.
9. **pm2** — `pm2 save`, copy `~/.pm2/dump.pm2`, store `pm2 jlist` for verification.
10. **files** — tar+zstd `/home/bot` with documented exclusions; tar `~/.orchestrator`, `~/.claude` (filtered), `~/.config`.
11. **secrets** — collect all `.env*` from project tree, `~/.ssh/*`, `~/.config/gh/*`, postgres role passwords (extracted from a controlled query), shadow entries — pipe through `age -r <recipient>` into `secrets.age`.
12. **checksums** — sha256 every file, write `checksums.sha256`. Optional sign with `--sign`.
13. **package** — single tarball `<bundle>.tar.zst`. Print path + size + sha256.

A capture must complete in O(minutes) on this server's data volume. PostgreSQL dumps and the home tree dominate; everything else is sub-second.

---

## 9. Restore / Bootstrap Pipeline

Designed for a **fresh Ubuntu 24.04 install** with sudo access.

Phases:

1. **bootstrap** — install: `tar`, `zstd`, `age`, `curl`, `git`, `build-essential`, `nginx`, `redis-server`, `postgresql-16`, `python3`, `nodejs` (NodeSource 18.x), `pnpm` (corepack), `pm2` (npm -g). Match versions from `manifest.json` where pinned.
2. **packages** — `apt-mark` and `dpkg --set-selections` to recreate the manual package set; `apt-get update && apt-get dselect-upgrade`.
3. **users** — create user `bot` with same uid (1000) if absent; restore non-system /etc/passwd/group entries from delta; restore shadow from secrets vault; restore sudoers.d/*.
4. **files** — extract `home-bot.tar.zst` into `/home/bot/`; extract `orchestrator.tar.zst` and `claude.tar.zst` to `/home/bot/.orchestrator` and `/home/bot/.claude`; extract `config.tar.zst` to `/home/bot/.config`; chown `-R bot:bot /home/bot`.
5. **secrets** — decrypt `secrets.age` to a temp dir, install `~/.ssh/*` with `chmod 600`, install env files at the project paths recorded in the manifest, install `gh` token, install postgres role passwords for the next phase.
6. **postgres** — `pg_ctlcluster 16 main start`; restore globals (`globals.sql`); `ALTER ROLE` the captured passwords; `createdb -O <owner>` per DB; `pg_restore --format=custom -d <db>` per DB.
7. **redis** — stop redis; copy `dump.rdb` into `/var/lib/redis/`; `chown redis:redis`; apply non-default CONFIG; start redis.
8. **nginx** — copy `/etc/nginx/{nginx.conf,sites-available/,conf.d/}`; recreate `sites-enabled` symlinks per the captured map; `nginx -t && systemctl reload nginx`.
9. **pm2** — as user bot: `pm2 resurrect` from the captured `dump.pm2`; verify with `pm2 jlist` count matches manifest; `pm2 save && pm2 startup systemd`.
10. **cron** — install bot crontab and `/etc/cron.d/*` files.
11. **postcheck** — run `general-backup verify --live` to compare manifest snapshot vs current host.

Each phase logs to `/var/log/general-backup-restore.log`. Phases are resumable — on re-run, a phase is skipped if its done-marker file exists in `/var/lib/general-backup/state/`.

---

## 10. Secrets & Encryption

- **Encryption tool**: [age](https://github.com/FiloSottile/age). Apt-installable.
- **Mode**: recipient-based (X25519 public key) by default; passphrase mode supported via `--age-passphrase`.
- **What goes into secrets.age** (and is *never* in plaintext anywhere else in the bundle):
  - All `.env*` files (project + system)
  - `~/.ssh/` entire directory
  - `~/.config/gh/*` (GitHub tokens)
  - Postgres role passwords (extracted via SECURITY DEFINER from `pg_authid`, dumped to a JSON map: `{role: pwhash}`)
  - Shadow lines for restored users
  - `/etc/sudoers.d/*`
  - `~/.orchestrator/config/settings.json` (telegram bot token, etc)
- **Plaintext bundle inspection**: `general-backup verify <bundle>` works without the identity; only `restore` and `secrets-show` need the identity.
- **Key management**: tool does NOT generate keys. README documents `age-keygen -o ~/.config/age/key.txt` and reminds to back the key up *outside the bundle*.

---

## 11. Idempotency, Safety, Verification

- **Dry-run** for both capture and restore, listing every action that would happen.
- **Diff mode** (`general-backup diff bundle.tar.zst`) — show, per component, what differs between the bundle and the current host (DB list diff, package diff, file tree diff via `rsync --dry-run --itemize-changes`, etc).
- **Done-markers** under `/var/lib/general-backup/state/<phase>.ok` so a partially failed restore resumes from the failed phase, not from scratch.
- **Refusal of destructive overwrite** by default — restore aborts if `/home/bot` already has content unless `--force` or `--target-user other-user`.
- **Checksum verification** before any restore phase reads a data file.
- **Schema versioning** — manifest `schema_version` field; restore refuses bundles with newer schema than the running tool understands.

---

## 12. Public Documentation

- The **GitHub repo README** is the public link the user can read. It must contain:
  - Quickstart (3 commands).
  - Full inventory of what's captured.
  - Restore walkthrough with screenshots/text logs.
  - Bundle layout diagram.
  - Security notes (encryption, what's in secrets.age, how to manage age keys).
  - Failure-mode FAQ.
- `docs/` folder in the repo with deeper material:
  - `docs/architecture.md` — design rationale.
  - `docs/restore-runbook.md` — operator runbook for fresh-server restore.
  - `docs/threat-model.md` — secrets handling, what an attacker with the bundle can/can't do.
  - `docs/extending.md` — how to add a new component (e.g. backing up a future MongoDB).
- README links to the latest release tarball under GitHub Releases for one-line install.

---

## 13. Implementation Roadmap (Issues)

The agent should create the following GitHub Issues from this PRD and implement them in order. Each is sized for one PR.

**Epic A — Foundations**
1. Initialize repo skeleton: `bin/general-backup` entrypoint, `lib/` for shared bash/python, Makefile, `.gitignore`, MIT LICENSE, baseline README.
2. Add CLI argument parser (Python, argparse) with `capture`, `restore`, `verify`, `diff`, `install` subcommands stubbed out.
3. Implement `manifest.py` — dataclass + JSON schema + sha256 helper + writer/reader.

**Epic B — Capture pipeline**
4. Implement `inventory` phase — collect hostname, OS, kernel, sizes; write manifest skeleton.
5. Implement `packages` phase — apt + pnpm + npm + pip lists.
6. Implement `system` phase — passwd/group/sudoers delta extraction.
7. Implement `nginx` phase — copy nginx config tree + sites-enabled symlink map.
8. Implement `cron` phase — capture user + system cron.
9. Implement `postgres` phase — globals + per-DB custom-format dumps + role-password extraction into secrets staging.
10. Implement `redis` phase — SAVE + rdb copy + CONFIG GET diff.
11. Implement `pm2` phase — pm2 save + jlist capture.
12. Implement `files` phase — tar+zstd of `/home/bot` and friends, with documented exclusions.
13. Implement `secrets` phase — gather sensitive files, age-encrypt to `secrets.age`.
14. Implement `checksums` phase — sha256 every bundle file, optional signing.
15. Implement bundle packaging — final tar.zst, print summary.

**Epic C — Restore / Bootstrap pipeline**
16. Implement `bootstrap.sh` — apt install of base toolchain on fresh Ubuntu 24.04.
17. Implement `restore packages` phase.
18. Implement `restore users` phase (uid-preserving).
19. Implement `restore files` phase.
20. Implement `restore secrets` phase (decrypt + place files at recorded paths).
21. Implement `restore postgres` phase (createdb + pg_restore + ALTER ROLE passwords).
22. Implement `restore redis` phase.
23. Implement `restore nginx` phase + reload.
24. Implement `restore pm2` phase + systemd integration.
25. Implement `restore cron` phase.
26. Implement done-markers + resumable phases.

**Epic D — Safety & UX**
27. Implement `dry-run` for capture and restore.
28. Implement `verify` subcommand (manifest schema + checksum + age decryptability).
29. Implement `diff` subcommand against a live host.
30. Implement progress UI (per-phase status lines).

**Epic E — Documentation**
31. Write README quickstart + full feature description.
32. Write `docs/architecture.md`.
33. Write `docs/restore-runbook.md` with a worked example.
34. Write `docs/threat-model.md`.
35. Write `docs/extending.md`.

**Epic F — Tests**
36. Add `tests/smoke-capture.sh` — capture against current host into a temp file, verify integrity.
37. Add `tests/restore-in-docker.sh` — spin up a fresh Ubuntu 24.04 docker container, restore the captured bundle, run assertions (postgres dbs exist, pm2 list matches, nginx reloads).
38. Add CI workflow that runs the smoke + docker round-trip on every PR.

**Epic G — Release**
39. Tag v1.0.0; create GitHub Release with prebuilt tarball.
40. Add install one-liner to README.

Tasks 1–15 are P0 (capture works end-to-end). 16–26 are P0 (restore works). 27–30 P1. 31–35 P1 (docs are the public link — ship before tagging). 36–38 P1. 39–40 close-out.

---

## 14. Test Plan

- **Unit-ish**: each phase module exposes a `run(ctx)` function and has a corresponding `test_<phase>.py` with a temp-dir fixture.
- **Smoke test**: `tests/smoke-capture.sh` runs `general-backup capture --dry-run` against the current host and asserts the planned actions match a golden file. A second test runs a real capture into `/tmp` and asserts: bundle exists, `verify` passes, manifest counts match a live re-inventory.
- **Round-trip test**: `tests/restore-in-docker.sh` builds a Docker image based on `ubuntu:24.04`, copies in the bundle, runs `general-backup restore`, then asserts:
  - All databases listed in manifest are restorable + one row count check per DB.
  - `pm2 jlist | jq '.[].name'` matches manifest.
  - `nginx -t` succeeds.
  - `systemctl is-active redis-server postgresql@16-main nginx` all `active`.
  - `~/.orchestrator/config/projects.json` is byte-identical to source.
- CI runs both on push.

---

## 15. Risks & Mitigation

| Risk | Mitigation |
|---|---|
| Bundle leaks secrets | Hard rule: anything sensitive goes through `secrets.age`. CI test grep-asserts no plaintext token in non-encrypted files. |
| Restore order bug bricks fresh server | Phases idempotent + resumable; restore in disposable Docker first; `--dry-run` documented in README. |
| Postgres role passwords lost | Extracted via authenticated SECURITY DEFINER function during capture; stored in secrets.age; restored via `ALTER ROLE` immediately after `globals.sql`. |
| pm2 dump format changes between versions | Pin pm2 version in manifest; bootstrap installs that version; `verify` warns on mismatch. |
| Bundle too large | Default exclude `node_modules`, `.next`, `dist`; zstd level 19; print bundle size after capture. |
| Two captures racing on same Redis | `redis-cli SAVE` is blocking + fast; document not to run capture during heavy write traffic, or use BGSAVE + wait. |
| Restoring on non-Ubuntu host silently fails | `bootstrap.sh` checks `/etc/os-release`; refuses on non-Ubuntu unless `--force-os`. |

---

## 16. Success Metrics

- **Round-trip restore in Docker** completes green in CI.
- **Bundle size** < 2× the source `du` of the kept files (zstd on text + node-free trees).
- **Capture wall time** < 5 min on the reference server.
- **Restore wall time** < 15 min on a fresh 4-core VPS.
- **Public README** clearly explains all 16 sections of this PRD in a more digestible form, with a runnable quickstart.
- **One-command quickstart** works: `curl -fsSL .../install.sh | bash && general-backup capture --age-recipient $KEY`.
