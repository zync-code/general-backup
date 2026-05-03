# general-backup restore runbook

You are a Claude Code agent running on a fresh Ubuntu 24.04 server. Your task
is to restore a server from a `general-backup` bundle. Follow this runbook
exactly. Do not skip phases. Do not ask the operator unless you reach a
decision point marked **[DECISION]**.

## Environment

The following environment variables are injected by `general-backup restore-agent`:

- `BUNDLE_PATH` — absolute path to the `.tar.zst` bundle
- `MANIFEST_PATH` — absolute path to `manifest.json` (already extracted)
- `AGE_IDENTITY` — path to the age private key file (may be empty if not provided)
- `GB_BIN` — path to the `general-backup` binary
- `TARGET_USER` — the user to restore as (default: `bot`)

## Phase order

Run phases in this exact order. Each phase is a `general-backup phase <name>`
subcommand. If a phase fails:
- Check `/var/log/general-backup-restore.log` for the error.
- Attempt to fix the underlying cause.
- Re-run the phase — each phase is idempotent and done-markers prevent re-work.

```
bootstrap → packages → users → state-extract → secrets-decrypt →
projects-clone → postgres → redis → nginx → pm2 → cron → postcheck
```

## Reading the manifest

```bash
python3 -c "import json; m=json.load(open('${MANIFEST_PATH}')); print(json.dumps(m, indent=2))"
```

Key fields:
- `manifest.projects[]` — list of projects to clone + wire up
- `manifest.components.pm2.process_count` — expected PM2 process count for postcheck
- `manifest.components.postgres.databases` — expected database list
- `manifest.toolchain` — pinned tool versions installed by bootstrap

## Phase instructions

### bootstrap

```bash
${GB_BIN} phase bootstrap
```

This invokes `./bootstrap.sh` which installs the full toolchain: apt packages,
Node (NodeSource), pnpm (corepack), pm2 (npm -g), PostgreSQL 16, Redis, and
the `claude-code` CLI.

The bootstrap reads `manifest.toolchain` to pin versions. If the host already
has matching versions, each step is a no-op.

Expected: exits 0. If it fails with a package installation error, retry after
running `sudo apt-get update`.

### packages

```bash
${GB_BIN} phase packages --bundle "${BUNDLE_PATH}"
```

Applies the full `dpkg --get-selections` list from the bundle and runs
`apt-get dselect-upgrade -y`. This replays all manually installed apt packages.

Expected: exits 0. Network failures are transient — retry.

### users

```bash
${GB_BIN} phase users --bundle "${BUNDLE_PATH}"
```

Ensures `${TARGET_USER}` (uid 1000) exists. Applies the `passwd.delta` and
`group.delta` from the bundle for any non-system users captured at source.

Shadow lines and sudoers entries are in `secrets.age` and are applied in
`secrets-decrypt`.

Expected: exits 0.

### state-extract

```bash
${GB_BIN} phase state-extract --bundle "${BUNDLE_PATH}"
```

Extracts the four state tarballs into their home directories:
- `state/orchestrator.tar.zst` → `~/.orchestrator/`
- `state/claude.tar.zst` → `~/.claude/`
- `state/config.tar.zst` → `~/.config/`
- `state/home-dotfiles.tar.zst` → `$HOME/` (.bashrc, .profile, .gitconfig)

Chowns everything to `${TARGET_USER}:${TARGET_USER}`.

Expected: exits 0.

### secrets-decrypt

```bash
AGE_ID="${AGE_IDENTITY}"
${GB_BIN} phase secrets-decrypt --bundle "${BUNDLE_PATH}" ${AGE_ID:+--age-identity "${AGE_ID}"}
```

Decrypts `secrets.age` into a tmpfs staging directory and installs:
- `~/.ssh/*` with chmod 600
- Each project's `.env*` files at `manifest.projects[].env_paths`
- `~/.config/gh/hosts.yml` (GitHub token)
- PostgreSQL role password hashes loaded for the postgres phase
- Shadow lines + sudoers.d entries

[DECISION] If `AGE_IDENTITY` is empty or the identity does not match:
- The operator must provide the age private key.
- Ask: "I need the age private key to decrypt secrets.age. Please provide the
  path to the key file." Pause and wait for the response.

Expected: exits 0. If it fails with "no identity matched", the identity file
is wrong — ask the operator for the correct key.

### projects-clone

```bash
${GB_BIN} phase projects-clone --bundle "${BUNDLE_PATH}"
```

For each entry in `manifest.projects[]`:
1. Creates `project_dir` if missing.
2. `git clone <git_url> <project_dir>` or, if already exists:
   `git -C <project_dir> fetch origin && git -C <project_dir> reset --hard <sha>`
3. `git -C <project_dir> checkout <sha>`
4. Places env files from secrets staging at `env_paths`.
5. Runs `pnpm install --frozen-lockfile` (best-effort).

**Decision rules:**
- If `pnpm install` fails: log the error, mark the project as `degraded`,
  continue to the next project. Do NOT abort.
- If `git clone` fails (repository not found, no auth): log the error, mark
  as `degraded`, continue.
- If a project's `pm2_apps` list is empty in the manifest and the project
  directory is now present, check `data/pm2/dump.pm2` in the bundle for
  matching entries to seed pm2 for that project.

Expected: exits 0 even if some projects are degraded. Degraded projects are
listed in the output — you will need to fix them manually after the run.

### postgres

```bash
${GB_BIN} phase postgres --bundle "${BUNDLE_PATH}"
```

1. Starts `pg_ctlcluster 16 main` if not running.
2. `psql -U postgres -f data/postgres/globals.sql` — restores roles and tablespaces.
3. For each role with a captured password hash: `ALTER ROLE <name> PASSWORD '<hash>'`.
4. For each database in `data/postgres/`: `createdb -O postgres <name>` (skip if exists).
5. `pg_restore --format=custom -d <db> data/postgres/<db>.dump`

**Decision rules:**
- If a database already exists with data: by default, `pg_restore` will fail
  on duplicate objects. Check if the database should be dropped first. If
  in doubt, ask the operator before dropping.
- If a `.dump` file is missing for a database listed in the manifest: log a
  warning and continue.

Expected: exits 0 for all databases, or 3 (partial) if some failed.

### redis

```bash
${GB_BIN} phase redis --bundle "${BUNDLE_PATH}"
```

1. Stops redis-server.
2. Copies `data/redis/dump.rdb` to `/var/lib/redis/dump.rdb`.
3. Sets ownership: `chown redis:redis /var/lib/redis/dump.rdb`.
4. Applies non-default CONFIG values from `data/redis/config.json`.
5. Starts redis-server.

Expected: exits 0.

### nginx

```bash
${GB_BIN} phase nginx --bundle "${BUNDLE_PATH}"
```

1. Copies `data/nginx/nginx.conf` to `/etc/nginx/nginx.conf`.
2. Copies `data/nginx/sites-available/` to `/etc/nginx/sites-available/`.
3. Copies `data/nginx/conf.d/` to `/etc/nginx/conf.d/`.
4. Recreates symlinks in `/etc/nginx/sites-enabled/` from `data/nginx/sites-enabled.txt`.
5. Runs `nginx -t && systemctl reload nginx`.

**Decision rule:** If `nginx -t` fails:
- Inspect the error. A missing SSL certificate is the most common cause.
  SSL certificates are not in the bundle.
- Obtain certificates via `sudo certbot --nginx -d <domain>` for each vhost.
- Re-run this phase after certificates are in place.

Expected: exits 0 with nginx active and reloaded.

### pm2

```bash
sudo -u ${TARGET_USER} ${GB_BIN} phase pm2 --bundle "${BUNDLE_PATH}"
```

Runs as `${TARGET_USER}` (not root):
1. `pm2 resurrect` from captured `dump.pm2`.
2. Verifies `pm2 jlist | length` matches `manifest.components.pm2.process_count`.
3. `pm2 save && pm2 startup systemd`.

**Decision rule:** If `pm2 jlist | length` is less than expected:
- Check `dump.pm2` for the missing process definitions.
- Manually register missing processes: `pm2 start <ecosystem.config.js> --only <name>`.
- Re-run `pm2 save`.

Expected: exits 0. If count does not match after manual fix, note the
discrepancy in the restore report.

### cron

```bash
${GB_BIN} phase cron --bundle "${BUNDLE_PATH}"
```

1. Installs `data/cron/bot.crontab` via `crontab -u ${TARGET_USER}`.
2. Copies `data/cron/etc-cron.d/*` to `/etc/cron.d/`.

Expected: exits 0.

### postcheck

```bash
${GB_BIN} phase postcheck --bundle "${BUNDLE_PATH}"
```

Runs all post-restore assertions:
- `pm2 jlist` count matches manifest
- `nginx -t` passes
- All `manifest.components.postgres.databases` are listable
- All `manifest.projects[].project_dir` exist and have `.git/`

Produces `restore-report.md` in the current directory.

Expected: exits 0. If it exits non-zero, read `restore-report.md` for the
list of failed assertions and remediation steps.

---

## After the runbook completes

1. Read `restore-report.md` and address any items listed as `FAILED` or `degraded`.
2. For each degraded project: `cd <project_dir> && pnpm install && pnpm build`.
3. For missing SSL certificates: `sudo certbot --nginx -d <domain>`.
4. Notify the operator that restore is complete.
5. If `--auto-confirm` was NOT set: pause here and wait for the operator to
   confirm before exiting.

---

## Quick reference

```bash
# Check phase done-markers
ls /var/lib/general-backup/state/

# Force a phase to re-run (delete its marker)
rm /var/lib/general-backup/state/<phase>.ok

# View restore log
tail -f /var/log/general-backup-restore.log

# Check PM2
pm2 jlist | python3 -c "import json,sys; procs=json.load(sys.stdin); [print(p['name'],p['pm2_env']['status']) for p in procs]"

# Check nginx
sudo nginx -t && sudo systemctl status nginx

# Check postgres databases
psql -U postgres -lqt | awk -F'|' '{print $1}' | grep -v '^\s*$'
```
