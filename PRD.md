# general-backup — Stateful-Delta Snapshot & Agent-Driven Restore
## Product Requirements Document (PRD)

| | |
|---|---|
| **Version** | 2.0.0 |
| **Date** | May 2026 |
| **Status** | DRAFT — implementation start |
| **Platform** | Bash + Python 3 CLI, Linux (Ubuntu 24.04 reference target) |
| **Public link** | https://github.com/zync-code/general-backup (this repo IS the bootstrap entry-point) |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Goals & Non-Goals](#3-goals--non-goals)
4. [Reference Server Inventory](#4-reference-server-inventory)
5. [Scope — What Lives in Git vs What Goes in the Bundle](#5-scope--what-lives-in-git-vs-what-goes-in-the-bundle)
6. [Architecture & Bundle Format](#6-architecture--bundle-format)
7. [Agent-Driven Restore](#7-agent-driven-restore)
8. [CLI Surface](#8-cli-surface)
9. [Capture Pipeline](#9-capture-pipeline)
10. [Restore / Bootstrap Pipeline](#10-restore--bootstrap-pipeline)
11. [Secrets & Encryption](#11-secrets--encryption)
12. [Idempotency, Safety, Verification](#12-idempotency-safety-verification)
13. [Public Documentation](#13-public-documentation)
14. [Implementation Roadmap (Issues)](#14-implementation-roadmap-issues)
15. [Test Plan](#15-test-plan)
16. [Risks & Mitigation](#16-risks--mitigation)
17. [Success Metrics](#17-success-metrics)

---

## 1. Executive Summary

`general-backup` rebuilds a Linux server from two ingredients:

1. **The git remotes** (GitHub) — every project tree lives there. Source code is never bundled.
2. **One bundle file** — the **stateful delta** that *cannot* live in git: PostgreSQL dumps, Redis snapshot, encrypted secrets (env files, SSH keys, API tokens), nginx config, pm2 ecosystem, system users, package lists, orchestrator/agent state.

This repo (`zync-code/general-backup`) is itself the **bootstrap entry point**. On a fresh Ubuntu 24.04 box:

```bash
git clone https://github.com/zync-code/general-backup.git
cd general-backup
./bootstrap.sh                       # installs apt + node + pnpm + pm2 + postgres + redis + age
general-backup restore <bundle.tar.zst>
```

A Claude agent (via the orchestrator already used by `bot`) then reads the bundle's `manifest.json` plus the shipped `restore-runbook.md` and orchestrates the restore: clones each project from its recorded GitHub URL at the captured SHA, applies its env file, hooks up its PM2 entry, restores its database, reloads nginx.

Result: a server that is functionally identical to the source — without ever shipping project source code through the bundle.

---

## 2. Problem Statement

The reference server hosts ~20 projects, 10 PostgreSQL databases, 20 PM2 processes, 5 nginx vhosts, and a multi-agent orchestrator. There is no automated way to:

- Push every project to its GitHub remote in one shot before a snapshot.
- Capture the *non-git* state (DBs, secrets, configs, system users) into one transferable artifact.
- Reproduce the box on a new VPS by combining "git pull all projects" + "apply state delta".
- Treat restore as an *agent-orchestrated* sequence rather than a brittle monolithic shell script.

`general-backup` is the codified, agent-aware version of all of that.

---

## 3. Goals & Non-Goals

### Goals (P0)

- **Pre-capture git-sync**: every registered project is committed (snapshot commit if dirty) and pushed to its GitHub remote before bundling. Manifest records each project's `{git_url, branch, sha, env_path, deploy_type, pm2_apps}`.
- **Lean bundle**: contains only state that cannot be reconstructed from git (DBs, secrets, configs, package lists, system users, orchestrator config + Claude config).
- **`general-backup` repo is the bootstrap**: cloning it onto a fresh server gives `bootstrap.sh`, the `general-backup` CLI, and `restore-runbook.md`. No external installer.
- **Agent-driven restore**: a Claude agent on the target box reads the manifest + runbook and orchestrates per-project restore (clone → install → env → DB → pm2). Shell scripts are building blocks; the agent decides ordering and handles per-project quirks via `~/.orchestrator/config/projects.json` semantics.
- **Idempotent and resumable**: re-running restore picks up where a failed phase left off via done-markers.
- **Encrypted secrets**: env files, SSH keys, tokens, role passwords pass through age before touching disk in the bundle.

### Goals (P1)

- **Selective capture/restore** via `--include/--exclude`.
- **Diff mode** (`general-backup diff <bundle>`) shows what restore would change on the current host.
- **Periodic capture** via `general-backup install-cron` (daily snapshot retention N=7 by default).
- **Push freshness check**: capture refuses if any project has unpushed commits and `--allow-snapshot-commit` was not given.

### Non-Goals (v1)

- Bundling project source code (it lives in GitHub — that's the whole point).
- Continuous replication / streaming.
- Cross-distro restore (Ubuntu 24.04 only in v1).
- Web UI.
- Multi-node clusters.

---

## 4. Reference Server Inventory

| Layer | What lives here |
|---|---|
| **OS** | Ubuntu 24.04.1 LTS, single regular user `bot` (uid 1000) |
| **Filesystem footprint** | `/home/bot` 18G — `projects/` 11G (will NOT go in bundle), `.claude/` 153M, `.orchestrator/` 152M |
| **Services** | nginx, postgresql@16-main (5432), redis-server (6379), ssh, cron |
| **PostgreSQL 16** | 10 user dbs: asap, automotive_dev, bar7, bar7_dev, custom_linear, devpulse, mojpausal, pnlmaker, procvat, qubit |
| **Redis** | db0 ~16,540 keys; db1 ~13 keys |
| **PM2** | ~20 processes (automotive-{web,api,scraper}, dev-pulse-{web,api,worker}, asap-{api,web}, restoran-{api,web}, lekopis-web, moj-pausal-{web,bot}, pnl-maker-backend, custom-linear-api, qa-tool-web, qr-scanner-qr-generator, procvat, command-center, copy-trading-trades-api, …) |
| **nginx** | sites-enabled: landlify.com, lekopis.com, main, mojpausal.com, viewermd.com. conf.d: security.conf, ws-upgrade-map.conf. Per-app includes under `~/.orchestrator/nginx/{sites,locations}/` |
| **Project trees** | 20 dirs under `/home/bot/projects/` — **each has a GitHub remote** registered in `~/.orchestrator/config/projects.json`. Source goes through git, not the bundle. |
| **Orchestrator** | `~/.orchestrator/` — multi-agent runtime (commands, lib, bot, config, logs). Goes in bundle (logs filterable). |
| **Claude config** | `~/.claude/` — settings, plugins, commands. Filtered subset goes in bundle. |
| **System config** | `~/.config/{gh,pnpm,turborepo,...}` — token-bearing → secrets vault. |
| **SSH** | `~/.ssh/id_ed25519{,.pub}`, `known_hosts` → secrets vault. |
| **Toolchain** | Node 18.19.1, pnpm, pm2 6.0.14, Python 3.12.3 — versions pinned in manifest, replayed by `bootstrap.sh`. |

The inventory is regenerated live at every capture; this table is the starting reference.

---

## 5. Scope — What Lives in Git vs What Goes in the Bundle

### LIVES IN GIT (NOT bundled — restored via `git clone`)

- Every directory under `/home/bot/projects/` whose registry entry has a `github_repo`.
- Project source, lockfiles, config (`PRD.md`, `CLAUDE.md`, `package.json`, `ecosystem.config.js`, etc).
- Project `.claude/settings.json` (committed in repo).

### IN THE BUNDLE (cannot live in git)

- **Per-project state**:
  - `.env*` files → secrets vault
  - PostgreSQL data (per-DB dumps + globals + role passwords)
  - Redis snapshot
  - PM2 `dump.pm2` + per-process metadata for resurrection
  - SQLite/file-based DBs found under project trees (none currently, but supported)
- **System / shared state**:
  - nginx full config (`/etc/nginx/{nginx.conf,sites-available/,conf.d/}`, sites-enabled symlink map)
  - cron (bot crontab + `/etc/cron.d/*`)
  - System users delta (`/etc/passwd`, `/etc/group` for non-system uids; `/etc/shadow`, `/etc/sudoers.d/*` → secrets vault)
  - apt manual package list, pnpm/npm globals, pip freeze
- **Operator state**:
  - `~/.orchestrator/` (configs, lib, commands, claude/commands; logs optional via `--include-logs`)
  - `~/.claude/` (settings, settings.local, plugins, commands, projects/, plans/) — exclude cache, paste-cache, shell-snapshots, telemetry, file-history, history.jsonl
  - `~/.config/` (gh, pnpm, turborepo)
  - `~/.ssh/` → secrets vault
  - `~/.bashrc`, `~/.profile`
- **Manifest & integrity**:
  - `manifest.json` (host info, project map, component sizes, checksums reference)
  - `checksums.sha256`
  - `restore-runbook.md` snapshot (the version that captured this bundle)

### Pre-capture sync rule

For every entry in `~/.orchestrator/config/projects.json` with a `github_repo`:

1. `cd <project_dir>`
2. If working tree clean → `git push origin <current-branch>` (no-op if already pushed).
3. If working tree dirty:
   - With `--allow-snapshot-commit` (default ON): `git add -A && git commit -m "snapshot: pre-backup capture YYYY-MM-DDTHH:MM:SSZ"` then push.
   - Without it: capture aborts with the list of dirty repos.
4. Record `{name, git_url, branch, sha (after push), deploy_type, env_paths, pm2_apps, db_names}` into `manifest.projects[]`.

---

## 6. Architecture & Bundle Format

### High-level flow

```
[ source server ]                                 [ fresh Ubuntu 24.04 target ]
       |                                                       |
   git push all projects                                        |
       |                                              git clone general-backup
   general-backup capture ───── bundle.tar.zst ─────────────────|
                                                                v
                                                       ./bootstrap.sh
                                                                v
                                                  general-backup restore bundle.tar.zst
                                                                v
                                              [agent reads manifest + runbook]
                                                  ├── git clone each project @ SHA
                                                  ├── apply env files (secrets.age)
                                                  ├── pg_restore each DB
                                                  ├── redis dump.rdb in place
                                                  ├── nginx config + reload
                                                  ├── pm2 resurrect
                                                  └── done-markers per phase
```

### Bundle layout (inside the tarball)

```
general-backup-<host>-<UTCstamp>/
├── manifest.json                  # version, source host, projects map, component summary, checksums ref
├── restore-runbook.md             # the runbook this bundle was built against
├── README.txt                     # human-readable summary of what's here
├── secrets.age                    # age-encrypted vault
├── data/
│   ├── postgres/
│   │   ├── globals.sql            # roles & tablespaces (no passwords in plaintext)
│   │   └── <dbname>.dump          # pg_dump --format=custom per database
│   ├── redis/
│   │   ├── dump.rdb
│   │   └── config.json            # non-default CONFIG values
│   ├── pm2/
│   │   ├── dump.pm2
│   │   └── jlist.json
│   ├── nginx/
│   │   ├── nginx.conf
│   │   ├── sites-available/
│   │   ├── sites-enabled.txt      # symlink target list
│   │   └── conf.d/
│   ├── cron/
│   │   ├── bot.crontab
│   │   └── etc-cron.d/...
│   └── system/
│       ├── passwd.delta
│       ├── group.delta
│       └── (sudoers, shadow → secrets.age)
├── state/
│   ├── orchestrator.tar.zst       # ~/.orchestrator (configs, lib, commands; logs optional)
│   ├── claude.tar.zst             # ~/.claude (filtered)
│   ├── config.tar.zst             # ~/.config
│   ├── home-dotfiles.tar.zst      # .bashrc, .profile, .gitconfig
│   └── projects.json              # ~/.orchestrator/config/projects.json (also embedded in manifest)
├── packages/
│   ├── apt-manual.txt             # apt-mark showmanual
│   ├── apt-selections.txt         # dpkg --get-selections
│   ├── npm-global.json
│   ├── pnpm-global.json
│   └── pip3-freeze.txt
└── checksums.sha256
```

Note: there is no `files/home-bot.tar.zst` of project trees. Projects come from git.

### Manifest schema (manifest.json)

```json
{
  "schema_version": 2,
  "tool_version": "2.0.0",
  "captured_at": "2026-05-03T18:42:11Z",
  "source": {
    "hostname": "5t3i.c.time4vps.cloud",
    "os": "Ubuntu 24.04.1 LTS",
    "kernel": "6.8.0",
    "user": "bot",
    "uid": 1000
  },
  "toolchain": {
    "node": "18.19.1",
    "pnpm": "...",
    "pm2": "6.0.14",
    "python3": "3.12.3",
    "postgres": "16",
    "redis": "..."
  },
  "projects": [
    {
      "name": "Automotive",
      "git_url": "https://github.com/zync-code/Automotive.git",
      "branch": "main",
      "sha": "abc123…",
      "project_dir": "/home/bot/projects/Automotive",
      "deploy_type": "nginx",
      "env_paths": [".env", "apps/web/.env.local", "apps/api/.env"],
      "pm2_apps": ["automotive-web", "automotive-api", "automotive-scraper"],
      "db_names": ["automotive_dev"],
      "post_install": ["pnpm install", "pnpm build"]
    }
  ],
  "components": {
    "postgres": { "version": "16", "databases": ["asap", "automotive_dev", "..."] },
    "redis":    { "db_count": 2, "key_count": 16553 },
    "pm2":      { "process_count": 20 },
    "nginx":    { "vhost_count": 5, "include_count": 16 },
    "system":   { "users": ["bot"], "uid_range": [1000, 1000] },
    "packages": { "apt_manual": 412, "pnpm_global": 8 }
  },
  "exclusions": ["node_modules", ".next", "dist", "build", ".cache", ".turbo", ".claude/cache", ".claude/paste-cache", ".claude/telemetry", ".claude/file-history"],
  "checksums_file": "checksums.sha256",
  "secrets_encrypted": true,
  "runbook_sha256": "..."
}
```

The `projects[]` array is the single source of truth restore reads to know what to clone, where to put it, what env to apply, which pm2 entries to expect, and which DBs belong to it.

---

## 7. Agent-Driven Restore

The repo ships `restore-runbook.md` — a structured prompt aimed at a Claude Code agent running on the target box. The runbook tells the agent:

1. Phase order (bootstrap → packages → users → state-extract → secrets-decrypt → projects-clone → postgres → redis → nginx → pm2 → cron → postcheck).
2. Which CLI subcommand handles each phase (`general-backup phase <name>`).
3. How to read `manifest.projects[]` and orchestrate per-project: clone @ sha, install deps, link env, register pm2 app.
4. Decision rules for ambiguity:
   - If a project's `pm2_apps` are missing locally, use `data/pm2/dump.pm2` to seed.
   - If `pnpm install` fails on a project, log it, mark project as `degraded`, continue to next; final report lists degraded projects.
   - If a database in `manifest.projects[].db_names` doesn't exist in `data/postgres/`, warn but continue.
5. Post-checks the agent must run: `pm2 jlist | length` matches manifest, `nginx -t` ok, all dbs listable, all projects directories exist with `.git`.

The runbook is plain Markdown, version-controlled, and the agent invocation is documented:

```bash
# Inside cloned general-backup, on target:
./bootstrap.sh                                   # base toolchain + claude-code CLI
general-backup restore-agent <bundle.tar.zst>    # spawns claude session with runbook + bundle
```

The `restore-agent` subcommand wraps:

1. Extract bundle to staging dir.
2. Verify bundle integrity (`general-backup verify`).
3. Spawn a tmux session running `claude --dangerously-skip-permissions -p "$(cat restore-runbook.md)"` with bundle path + manifest path injected.
4. Stream agent log to stdout + `/var/log/general-backup-restore.log`.

For operators who prefer scripts, `general-backup restore <bundle>` runs the same phases non-interactively (no LLM in the loop) — agent mode is opt-in but recommended for first-time / unusual restores.

---

## 8. CLI Surface

```bash
# Capture (always pre-syncs git first)
general-backup capture \
    [--out PATH]
    [--age-recipient RECIPIENT]
    [--include LIST]                  # git-sync,postgres,redis,files,pm2,nginx,cron,packages,system (default: all)
    [--exclude LIST]
    [--allow-snapshot-commit | --no-snapshot-commit]   # default: allow
    [--include-logs]                  # default: false (skip ~/.orchestrator/logs/)
    [--dry-run]
    [--sign KEYFILE]
    [--quiet | --verbose]

# Restore (script mode)
general-backup restore <bundle> \
    [--age-identity FILE]
    [--phases LIST]                   # default: all in order
    [--skip-phases LIST]
    [--dry-run]
    [--force]

# Restore (agent mode — recommended for first run)
general-backup restore-agent <bundle> \
    [--age-identity FILE]
    [--auto-confirm]                  # default: false (agent pauses for ack at end)

# Verify
general-backup verify <bundle>

# Diff (planned-vs-current)
general-backup diff <bundle>

# Bootstrap-only
general-backup install                # = ./bootstrap.sh
general-backup install-cron           # daily capture via cron, retention 7

# Per-phase (advanced)
general-backup phase capture-postgres
general-backup phase restore-projects --bundle <path>
```

Exit codes: 0 ok · 1 user error · 2 integrity error · 3 partial restore (resumable) · 4 permission error · 5 git-sync conflict (dirty repo with `--no-snapshot-commit`).

---

## 9. Capture Pipeline

Runs in this order. Each phase is independently re-runnable.

1. **preflight** — verify required tools (`gh`, `git`, `pg_dump`, `redis-cli`, `tar`, `zstd`, `age`); confirm staging disk space; load `~/.orchestrator/config/projects.json`.
2. **git-sync** — for each project with a `github_repo`:
   - `git -C <dir> fetch origin`
   - If clean: ensure all commits pushed.
   - If dirty + snapshot allowed: `git add -A && git commit -m "snapshot: ..."`; push.
   - If dirty + not allowed: collect into error list; abort if non-empty.
   - Capture final SHA, branch, remote URL into `manifest.projects[]`.
3. **inventory** — write manifest skeleton (host, OS, sizes, toolchain versions).
4. **packages** — apt manual, dpkg selections, pnpm `ls -g --json`, npm `ls -g --json`, pip3 freeze.
5. **system** — passwd/group delta for non-system uids; sudoers.d files staged for secrets.age; shadow lines for restored users staged for secrets.age.
6. **nginx** — copy `/etc/nginx/{nginx.conf,sites-available/,conf.d/}`; record `sites-enabled` symlink map.
7. **cron** — `crontab -l -u bot`, `/etc/cron.d/`, `/etc/cron.{daily,hourly,weekly,monthly}/`.
8. **postgres** — `pg_dumpall --globals-only --no-role-passwords`; `pg_dump --format=custom --compress=9` per DB; extract role password hashes from `pg_authid` (requires SUPERUSER) → `secrets.age`.
9. **redis** — `redis-cli SAVE`; copy rdb; `CONFIG GET *` → diff vs default → `config.json`.
10. **pm2** — `pm2 save`; copy `~/.pm2/dump.pm2`; `pm2 jlist > jlist.json`.
11. **state** — tar+zstd `~/.orchestrator` (excl `logs/` unless `--include-logs`), `~/.claude` (filtered), `~/.config`, dotfiles.
12. **secrets** — gather all `.env*` from project trees (paths recorded in `manifest.projects[].env_paths`), `~/.ssh/*`, `~/.config/gh/*`, postgres role passwords, sudoers, shadow → pipe through `age -r <recipient>` → `secrets.age`.
13. **checksums** — sha256 every bundle file → `checksums.sha256`. Optional sign with `--sign`.
14. **package** — final `tar -I zstd <bundle>.tar.zst`. Print path, size, sha256, capture duration.

Target wall time on the reference server: < 5 min.

---

## 10. Restore / Bootstrap Pipeline

Runs on a fresh Ubuntu 24.04 box with sudo access. Phases write done-markers under `/var/lib/general-backup/state/`.

1. **bootstrap** — install toolchain matching manifest: `tar`, `zstd`, `age`, `curl`, `git`, `build-essential`, `nginx`, `redis-server`, `postgresql-16`, `python3`, `nodejs` (NodeSource X), `pnpm` (corepack), `pm2` (npm -g), `claude-code` (curl install).
2. **packages** — `dpkg --set-selections` from `apt-selections.txt`; `apt-get update && apt-get dselect-upgrade -y`.
3. **users** — create `bot` with same uid (1000) if missing; restore non-system passwd/group entries from delta; restore shadow + sudoers.d/* from secrets.age.
4. **state-extract** — extract `state/orchestrator.tar.zst` → `~/.orchestrator`; `state/claude.tar.zst` → `~/.claude`; `state/config.tar.zst` → `~/.config`; dotfiles to `$HOME`. Chown `bot:bot`.
5. **secrets-decrypt** — decrypt `secrets.age` to a tmpfs staging dir; install `~/.ssh/*` (chmod 600), env files at recorded paths, gh token at `~/.config/gh/hosts.yml`, role passwords loaded for next phase.
6. **projects-clone** — for each entry in `manifest.projects[]`:
   - `mkdir -p <project_dir>` if missing
   - `git clone <git_url> <project_dir>` (or `git -C fetch + reset --hard <sha>` if exists)
   - `git -C <project_dir> checkout <sha>`
   - Place env files from secrets staging at `manifest.projects[i].env_paths`.
   - `pnpm install --frozen-lockfile` (best-effort; failures noted, project marked `degraded`, continue).
   - Record per-project status in restore log.
7. **postgres** — `pg_ctlcluster 16 main start`; `psql -f globals.sql`; `ALTER ROLE` each role with captured password hash; `createdb -O <owner>` per DB (skip if exists); `pg_restore --format=custom -d <db>` per DB.
8. **redis** — stop redis; copy `dump.rdb` → `/var/lib/redis/dump.rdb`; chown `redis:redis`; apply non-default CONFIG; start redis.
9. **nginx** — copy nginx config; recreate `sites-enabled` symlinks; `nginx -t && systemctl reload nginx`.
10. **pm2** — as user bot: `pm2 resurrect` from captured `dump.pm2`; verify count matches `manifest.components.pm2.process_count`; `pm2 save && pm2 startup systemd`.
11. **cron** — install bot crontab; copy `/etc/cron.d/*`.
12. **postcheck** — run `general-backup verify --live` + manifest comparison; produce `restore-report.md` with: phases ok/failed, degraded projects, action items.

Each phase emits structured progress to stdout and to `/var/log/general-backup-restore.log`. Idempotent: a failed phase resumes; a successful phase is a no-op.

---

## 11. Secrets & Encryption

- Tool: [age](https://github.com/FiloSottile/age). Apt-installable.
- Default mode: recipient-based (X25519 public key); passphrase mode supported.
- Inside `secrets.age`:
  - All `.env*` files (project + system)
  - `~/.ssh/` full contents
  - `~/.config/gh/*`
  - Postgres role passwords (`pg_authid` extract, JSON map `{role: pwhash}`)
  - Shadow lines + sudoers.d for restored users
  - `~/.orchestrator/config/settings.json` (telegram bot token, etc)
- `verify` works without identity. `restore` requires identity.
- README documents `age-keygen -o ~/.config/age/key.txt` and **explicitly warns**: store the age key OUTSIDE the bundle. Lose the key, lose the secrets.

---

## 12. Idempotency, Safety, Verification

- Dry-run for capture and restore (lists every action).
- `general-backup diff <bundle>` — per-component delta against current host: missing dbs, extra pm2 processes, missing project repos, package list diff, env-file presence map.
- Done-markers per phase under `/var/lib/general-backup/state/<phase>.ok` → resumable on retry.
- Restore refuses overwrite if `~/projects` is non-empty unless `--force`.
- Checksum verification before any restore phase reads a data file.
- Schema versioning on manifest; restore refuses bundles with newer schema than the running tool.
- Capture refuses if any registered project has unpushed commits and `--no-snapshot-commit` is set.

---

## 13. Public Documentation

The **GitHub repo itself is the public link**. README must include:

- 60-second quickstart (clone → bootstrap → restore).
- Inventory diagram of what's in vs out of the bundle.
- Capture walkthrough.
- Restore walkthrough (both script and agent modes).
- Bundle layout diagram.
- Security notes (encryption model, what an attacker with bundle can/can't do).
- Failure-mode FAQ.

`docs/` folder:

- `docs/architecture.md` — design rationale (why git, why agent, why age).
- `docs/restore-runbook.md` — the **canonical agent runbook** (also embedded in every bundle).
- `docs/threat-model.md`.
- `docs/extending.md` — adding a new component (e.g. MongoDB or a new project type).
- `docs/operator-faq.md`.

README links to the latest tagged release.

---

## 14. Implementation Roadmap (Issues)

Each item is sized for one PR.

**Epic A — Foundations**
1. Initialize repo skeleton: `bin/general-backup` Python entrypoint, `lib/`, `bootstrap.sh`, `.gitignore`, MIT LICENSE, baseline README.
2. CLI argument parser (Python argparse) with subcommands stubbed: `capture`, `restore`, `restore-agent`, `verify`, `diff`, `install`, `install-cron`, `phase`.
3. `manifest.py` — schema_version 2 dataclass, JSON schema validator, sha256 helper, writer/reader.

**Epic B — Capture pipeline**
4. `phase: preflight` — tool availability, staging disk check, projects.json load.
5. `phase: git-sync` — for each project, push current branch, snapshot-commit dirty trees, record git_url/branch/sha in manifest.projects[].
6. `phase: inventory` + `phase: packages`.
7. `phase: system` (passwd/group/sudoers/shadow delta).
8. `phase: nginx` (config + sites-enabled symlink map).
9. `phase: cron`.
10. `phase: postgres` (globals + per-db dump + role pw extraction → secrets staging).
11. `phase: redis` (SAVE + rdb copy + CONFIG diff).
12. `phase: pm2` (save + jlist).
13. `phase: state` (tar+zstd of `.orchestrator`, `.claude` filtered, `.config`, dotfiles).
14. `phase: secrets` (collect → age-encrypt → secrets.age).
15. `phase: checksums` + bundle packaging.

**Epic C — Restore pipeline (script mode)**
16. `bootstrap.sh` — apt + node + pnpm + pm2 + postgres + redis + age + claude-code.
17. `phase: restore-packages` (dpkg set-selections + dselect-upgrade).
18. `phase: restore-users` (uid-preserving).
19. `phase: restore-state-extract` (orchestrator/claude/config tarballs).
20. `phase: restore-secrets-decrypt` (age decrypt → place env, ssh, gh).
21. `phase: restore-projects` — per manifest.projects[]: clone, checkout sha, place env, pnpm install (best-effort with degraded marking).
22. `phase: restore-postgres` (globals + ALTER ROLE + createdb + pg_restore).
23. `phase: restore-redis`.
24. `phase: restore-nginx` (+ symlink map + reload).
25. `phase: restore-pm2` (resurrect + systemd startup).
26. `phase: restore-cron`.
27. Done-markers + resumability.

**Epic D — Agent-driven restore**
28. Author `docs/restore-runbook.md` (the agent prompt).
29. Implement `general-backup restore-agent <bundle>` — extract, verify, spawn claude session with runbook in tmux, stream log.
30. Author per-project decision rules (degraded handling, pm2 conflict, missing db).

**Epic E — Safety & UX**
31. Implement `--dry-run` for capture and restore.
32. Implement `verify` subcommand.
33. Implement `diff` subcommand against live host.
34. Progress UI per phase (status lines + duration + bytes).
35. `install-cron` subcommand — daily capture with N=7 retention.

**Epic F — Documentation**
36. Quickstart + full-feature README on main.
37. `docs/architecture.md`.
38. `docs/restore-runbook.md` (P0 — referenced by Epic D).
39. `docs/threat-model.md`.
40. `docs/extending.md`.
41. `docs/operator-faq.md`.

**Epic G — Tests**
42. `tests/smoke-capture.sh` — capture against current host, verify integrity, no plaintext secret leak.
43. `tests/git-sync.sh` — capture refuses on dirty + `--no-snapshot-commit`; succeeds with snapshot.
44. `tests/restore-in-docker.sh` — Docker `ubuntu:24.04`, run bootstrap, run restore (script mode), assert: every manifest.projects[].name has a `.git` at recorded SHA, all dbs restorable, pm2 jlist matches, nginx -t green.
45. `tests/restore-agent-in-docker.sh` — same but via `restore-agent` mode (claude-code installed in container, runs runbook).
46. CI workflow running smoke + git-sync + script-restore on every PR; agent-restore weekly (LLM cost).

**Epic H — Release**
47. Tag v2.0.0, GitHub Release with prebuilt tarball.
48. README install one-liner pointing at the release.

P0 = Epics A, B, C, D, F (#36, #38), G (#42, #44). P1 = the rest.

---

## 15. Test Plan

- **Unit-ish**: each phase module exposes `run(ctx)`; `tests/test_<phase>.py` with temp-dir fixtures.
- **Smoke** (`tests/smoke-capture.sh`): runs `capture --dry-run` against live host, asserts plan matches golden; runs real capture into `/tmp`, asserts `verify` passes and no plaintext token appears in non-`secrets.age` files (grep regex over checksummed files).
- **Git-sync semantics** (`tests/git-sync.sh`): seeds a temp project with a dirty change; asserts `--no-snapshot-commit` aborts, default mode commits + pushes (against a local bare-repo origin), manifest records the new SHA.
- **Round-trip in Docker** (`tests/restore-in-docker.sh`):
  - Build `ubuntu:24.04` image.
  - Copy bundle in.
  - Run `./bootstrap.sh`.
  - Run `general-backup restore <bundle>`.
  - Assert: each `manifest.projects[].name` exists at `project_dir`, `git rev-parse HEAD` matches `sha`; postgres dbs match; pm2 jlist count matches; `nginx -t` ok; `systemctl is-active` for postgres/redis/nginx all active.
- **Agent-restore in Docker** (`tests/restore-agent-in-docker.sh`): same image + `general-backup restore-agent`; assert agent log contains all phase markers and final report says "ok".

---

## 16. Risks & Mitigation

| Risk | Mitigation |
|---|---|
| Project has uncommitted local-only fix that's never pushed | Default `--allow-snapshot-commit` ensures it lands on origin before bundle freezes. Operator can opt out. |
| Git remote unreachable on restore | Restore phase logs the failure, marks project degraded, keeps going; final report lists all unreachable repos. |
| Snapshot commit pollutes history | Snapshot commits use a fixed prefix (`snapshot:`), are easy to grep & squash later. Operator can disable behavior. |
| Bundle leaks secrets | Hard rule: anything sensitive goes through secrets.age. CI grep-test on the unencrypted bundle contents fails on any plausible token pattern. |
| Restore order bug bricks fresh box | Phases idempotent + resumable; mandatory Docker round-trip in CI before merge. |
| Postgres role passwords lost | Captured from `pg_authid` into secrets.age; restored via ALTER ROLE right after globals.sql. |
| pm2 dump format drift | Pin pm2 version in manifest; `bootstrap.sh` installs that exact version; `verify` warns on mismatch. |
| Agent makes wrong call on restore | Runbook is explicit + machine-checkable; agent mode is opt-in; script mode is the deterministic baseline. |
| `pnpm install` flake on restore | Best-effort with degraded marking; final report lists projects needing manual `pnpm install`. |
| Two captures racing on Redis | `redis-cli SAVE` is fast; document not to capture during heavy write. |

---

## 17. Success Metrics

- **Round-trip in Docker** green in CI for both script and agent modes.
- **Bundle size** < 1 GB on the reference server (vs 11 GB if we bundled `projects/`).
- **Capture wall time** < 5 min (git-sync dominated by network, not bundle size).
- **Restore wall time** < 15 min on a fresh 4-core VPS (script mode); agent mode adds 1–2 min for LLM orchestration.
- **Public README** answers: what's in vs out, how restore works, how to manage age key, what fails when.
- **One-command quickstart** works:
  ```bash
  git clone https://github.com/zync-code/general-backup.git && cd general-backup && \
    ./bootstrap.sh && general-backup restore-agent ~/snapshot.tar.zst
  ```
