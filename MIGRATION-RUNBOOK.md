# Migration runbook: 5t3i.c.time4vps.cloud → new Time4VPS server

Snapshot of the exact commands for migrating this server using `general-backup`.
Written 2026-06-18. Source server has 24 registered projects, ~12 Postgres DBs,
Redis, 7 PM2 processes, 11 nginx vhosts/certs, 3 self-hosted GitHub Actions runners.

## Phase 0 — OLD server (this one): generate key + capture

```bash
# 1. Generate age keypair (once). Private key MUST leave this server immediately.
age-keygen -o ~/.config/age/key.txt
grep "public key" ~/.config/age/key.txt | awk '{print $NF}' > ~/.config/age/key.pub
cat ~/.config/age/key.pub

# 2. Download the PRIVATE key off-server NOW (run from your local machine, not here):
#    scp bot@5t3i.c.time4vps.cloud:~/.config/age/key.txt ~/secure-storage/general-backup-key.txt
#    Then store it in a password manager. Without this file, the bundle is unrecoverable.

# 3. Dry run — see the plan, no side effects
cd ~/projects/general-backup
./bin/general-backup capture --dry-run --age-recipient "$(cat ~/.config/age/key.pub)"

# 4. Real capture (pushes dirty repos to GitHub, dumps Postgres/Redis/PM2/nginx/cron/state)
./bin/general-backup capture --age-recipient "$(cat ~/.config/age/key.pub)" --out /home/bot/backups

# 5. Verify integrity (no key needed)
./bin/general-backup verify /home/bot/backups/general-backup-*.tar.zst
```

Output: `/home/bot/backups/general-backup-5t3i.c.time4vps.cloud-<timestamp>.tar.zst`

### Status (2026-06-18, actual run)
- Bundle captured: `/home/bot/backups/general-backup-5t3i-20260618T135716.tar.zst` (9.0 MB, verified ok)
- Age private key: `~/.config/age/key.txt` — **download this off-server before anything else**, public key is `age1gkgjszk5cgsjm03534rm9t5g4lg4qapjrkajtxjp6w33f5qjdaas5v0jg7`
- Fixed during capture: `dev-pulse` was behind remote (fast-forwarded), `procvat` had a bad historical commit with `.next/`, `node_modules/`, and a leaked `.env.local` committed to git — rewrote it to a clean commit + added `.gitignore`, force-free push succeeded.
- **`nikola` project: GitHub repo `zync-code/nikola` does not exist** (never created or deleted). Git-sync for this project will keep failing. Per decision: **do not auto-create the repo** — restore this project's source manually:
  ```bash
  # On OLD server, before decommissioning:
  tar czf /home/bot/backups/nikola-source.tar.gz -C /home/bot/projects nikola
  scp /home/bot/backups/nikola-source.tar.gz bot@<NEW_SERVER_IP>:/home/bot/projects/
  # On NEW server:
  cd /home/bot/projects && tar xzf nikola-source.tar.gz
  ```
  Everything else for `nikola` (postgres db if any, nginx vhost, pm2 entry, env file) is still inside the main bundle — only the git-clone step in the automated restore will skip/fail for this one project.

### Status (2026-06-18, full run on 5vdb.c.time4vps.cloud / 94.176.233.104) — DONE
Restore completed: 37/39 postcheck assertions passed. The general-backup tool had
several real bugs, all fixed and pushed to main during this run (see git log
2026-06-18 commits). Summary of what was found and fixed:

- `restore` crashed immediately: missing `import subprocess` in `lib/commands/restore.py`.
- `extract_tar` never stripped the wrapper directory capture always adds — every
  archive (`.orchestrator`, `.claude`, `.config`, dotfiles) landed one level too
  deep (e.g. `~/.orchestrator/.orchestrator/...`). Fixed with `--strip-components=1`.
- All `psql`/`createdb`/`pg_restore` calls used `-U postgres` while running as
  root — Postgres peer-auth rejects that. Fixed to run via `sudo -u postgres`.
- `bootstrap.sh` never added the GitHub CLI apt repo, so `gh` (needed as the git
  credential helper for https clones) was never installed. Added the repo, same
  pattern as the Postgres repo setup.
- `chown -R` of `$HOME` ran too early (in `state-extract`), before
  `secrets-decrypt` (.ssh, .config/gh) and `projects-clone` (cloned repos) wrote
  more files — those ended up root-owned. Added a second chown at the end of
  `projects-clone`.
- git clone/pnpm install ran as root instead of the target user, so the
  restored `.gitconfig`'s `gh auth git-credential` helper was never consulted
  (root has no `~/.gitconfig`) — every private-repo clone failed with exit 128.
  Fixed to run via `sudo -u <user> -H --`.
- The extracted bundle staging dir was `mkdtemp`'d at mode 0700 by root, so the
  `postgres` user couldn't read `globals.sql`/dumps. Fixed: `chmod -R a+rX` the
  staging dir right after extraction (safe — `secrets.age` stays encrypted
  regardless of file permissions).
- `restore_nginx.py` raised a fatal (exit_code=1) error when `nginx -t` failed,
  aborting the rest of restore (pm2/cron/postcheck never ran). Missing SSL certs
  are expected at this stage (certs are deliberately excluded from the bundle —
  see Phase 3 below), so this is now `exit_code=3` (non-fatal/resumable).
- `restore_pm2.py`'s `.pm2` directory chown only covered the single `dump.pm2`
  file, not the directory — `pm2 resurrect` then failed with
  `EACCES: mkdir '/home/bot/.pm2/logs'`. Fixed to chown the whole dir.
- `pm2 resurrect`/`save`/`startup` via `sudo -u <user> --` hit
  `spawn /usr/bin/node EACCES` on this host (a sudo-session artifact — pm2
  double-forks/daemonizes and the detached child's later `spawn(node)` calls
  got rejected). `su -l <user> -c "..."` does not have this problem — verified
  empirically. All pm2 invocations (including the postcheck PM2 check) switched
  to `su -l`.
- `postcheck`'s project-SHA check ran `git rev-parse` as root against
  bot-owned repos → git's "dubious ownership" protection silently produced
  empty output ("got ?" for every single project). Fixed to run as the target
  user.
- **`git-sync` (capture phase) bug, found via this run**: it only checks
  `git status --porcelain` for "dirty" before pushing. If the local branch has
  *diverged* from origin (push rejected, non-fast-forward) it just **warns and
  still records the local HEAD sha in the manifest** — baking in a commit that
  may not exist on GitHub at all. This is exactly what happened to `Bar7`: the
  old server's local checkout was a stale May-8 snapshot commit, 23 commits and
  ~15k lines behind the real `origin/main`. Fixed `git_sync.py` to verify the
  recorded sha is actually an ancestor of `origin/<branch>` after pushing, and
  to raise a proper git-sync-conflict error (exit 5) instead of lying. For
  *this* migration, `Bar7` was manually reset to `origin/main` on the new
  server (the old local snapshot had nothing of value beyond what's already
  upstream) — this shows as an expected `Bar7 SHA` mismatch in
  `restore-report.md`.

**Repos that don't exist on GitHub at all** (registered in projects.json,
`github_repo` 404s): `nikola`, `Lokica`, `Perica`. These are lightweight
test/scaffold repos (no `package.json`, not PM2-deployed) created during early
orchestrator testing — not production services. Restored via manual tar+scp
(see command block above) rather than git clone.

**`.next` build output and Prisma clients are not in the bundle** (by design —
build artifacts). Every PM2 app crash-loops on first restore until you run, per
project: `pnpm install` (without `--frozen-lockfile` if no lockfile is
committed — true for `asap`, `pnl-maker`, `procvat`), `pnpm prisma generate`
(or `pnpm -F <pkg> prisma generate`) for any package with a `prisma/schema.prisma`,
then `pnpm build`, then `pm2 restart all`. Also create any directories the app
expects at runtime but doesn't commit (e.g. `girls-x-venues/uploads`).

Final state: all 7 PM2 processes online and stable (gxv-admin/api/venue/web,
qa-tool-web/worker/hocuspocus), all 14 Postgres databases restored, Redis
restored, all 23 git-backed projects at the correct (verified-on-remote) SHA.

## Phase 3 — actually executed (2026-06-18)

### DNS cutover
All 10 A records + 1 AAAA (lekopis.com) switched at the registrar to
94.176.233.104 / 2a02:7b40:5eb0:e968::1. Propagated within ~10 min (TTL 600s).
Verified every subdomain referenced by existing certs (www.*, tax.*,
hdbp-admin/api/ussd.*, see-admin/api.*) had already followed (registrar-level
wildcard/zone behavior) — no extra records needed beyond the 10 documented.

### Certbot — chicken-and-egg problem hit and resolved
`certbot --nginx` cannot run on a fresh box here: the restored nginx vhosts
already reference `/etc/letsencrypt/live/<domain>/...` and
`options-ssl-nginx.conf` paths (captured from the old server), so `nginx -t`
fails before certbot's nginx plugin can even attempt the HTTP-01 challenge —
and nginx won't start to serve the challenge either. Fix used:
1. Copy `/etc/letsencrypt/options-ssl-nginx.conf` and `ssl-dhparams.pem`
   (generic, no secrets) from the old server first.
2. Stop nginx (frees port 80), then for each cert group use
   **`certbot certonly --standalone`** (runs its own temp listener, doesn't
   need nginx config to be valid):
   ```bash
   certbot certonly --standalone --non-interactive --agree-tos -m <email> \
     -d dibly.me -d www.dibly.me
   # ... repeated per group, SANs matched exactly to the old certs (checked
   # via: openssl x509 -in /etc/letsencrypt/live/<name>/cert.pem -noout -text)
   ```
3. Once all `live/` dirs have real certs, `nginx -t` passes and
   `systemctl start nginx` works normally.

Cert groups issued (10, all expire 2026-09-16): dibly.me+www, landlify.com+www,
lekopis.com+www, mojpausal.com+www, viewermd.com+www, hdbp.thinkn.cloud+admin+api+ussd,
hdbp-docs.thinkn.cloud, recs.thinkn.cloud+tax.thinkn.cloud,
see.thinkn.cloud+admin+api, recs.db.delivery+tax.db.delivery.

**Not issued — needs manual DNS-01**: `landlify.com-0001` was a **wildcard**
(`*.landlify.com`) cert on the old server. Wildcards require a DNS-01 TXT
challenge, which can't be automated without DNS provider API access (this
registrar is managed by hand). Workaround applied so nginx could start: the
`~^.+\.landlify\.com$` server block's `ssl_certificate`/`ssl_certificate_key`
in `/etc/nginx/sites-available/landlify.com` were repointed to the regular
`landlify.com` cert. This means actual `*.landlify.com` subdomains (if any are
used) will show a cert mismatch warning until the wildcard is reissued
properly: `certbot certonly --manual --preferred-challenges dns -d '*.landlify.com' -d landlify.com`
(walks through adding a `_acme-challenge.landlify.com` TXT record by hand).

**`bootstrap.sh` gap found+fixed**: `certbot`/`python3-certbot-nginx` were
never actually installed (they were in the captured apt-selections list, but
`dpkg --set-selections` + `apt-get dselect-upgrade` didn't pull them in
practice). Added both directly to `bootstrap.sh`'s `APT_PACKAGES`.

**Default/catch-all vhost** (`sites-available/main`, `server_name
5t3i.c.time4vps.cloud 62.77.155.227 _`) referenced the *old* hostname's own
cert. Got a fresh cert for the new hostname (`5vdb.c.time4vps.cloud`, already
resolvable) and updated the vhost's `server_name`/`ssl_certificate`/IP
in-place to match.

### Post-cutover verification
`nginx -t` passes, nginx running. Checked all 10 domains over HTTPS:
- `viewermd.com` → 200 (working)
- `dibly.me`, `landlify.com`, `lekopis.com`, `mojpausal.com`,
  `hdbp.thinkn.cloud`, `hdbp-docs.thinkn.cloud`, `recs.thinkn.cloud`,
  `see.thinkn.cloud`, `recs.db.delivery` → 502
  **Verified this is pre-existing, not a migration regression**: same exact
  502 reproduced against the OLD server's IP for every one of these domains
  (`curl --resolve <domain>:443:62.77.155.227 ...`). These apps simply aren't
  running as services on either server (not nginx-deploy_type apps backed by
  a live process — `recs.db.delivery` specifically has no nginx server block
  at all on either server, just an orphaned unused certificate).

### What's left
1. Re-issue the `*.landlify.com` wildcard cert via DNS-01 (manual TXT record) if it's actually in use.
2. ~~GitHub Actions self-hosted runners (3x)~~ — DONE: re-registered via `gh api .../registration-token` + fresh runner download, all 3 online, old-server services stopped+disabled.
3. Dormant nginx-deploy_type apps (moj-pausal, lekopis, hdbp, recs, dibly, landlify) — confirmed via `ss -tlnp` that **nothing listens on their upstream ports on the OLD server either**, and none have a `pm2_apps` entry in `projects.json`. Not a migration gap — they were never running as live services on the old server. `hdbp` has a real-sounding description ("Harare Digital Billing Platform") despite being dormant — worth a deliberate decision (not a migration task) on whether it should be deployed.
4. Old server can stay as fallback for a few days, then decommission.

## Security/config audit (2026-06-18) — things general-backup does NOT capture by default

Asked explicitly "did we miss any security setup" after the main migration. Checked,
on the OLD server: firewall (none — no ufw/iptables/nft rules, no fail2ban/clamav/
rkhunter installed), sysctl.d (all stock Ubuntu hardening files, nothing custom),
swap (none), logrotate (stock), monitoring agents (none), rsyslog/journald remote
forwarding (none), VPN configs (none), root crontab (empty), pg_hba.conf and
redis.conf bind/protected-mode (both stock defaults) — **all of these had nothing
to migrate, new server already matches by virtue of being a fresh Ubuntu 24.04 +
same package installs**.

Two real gaps were found and fixed:

1. **`bot` user's unrestricted sudo was silently dropped.** `/etc/sudoers` itself
   (not `/etc/sudoers.d/`) had a hand-added line `bot ALL=(ALL) NOPASSWD: ALL` —
   `general-backup` only ever captured `/etc/sudoers.d/*`, never custom lines in
   the main file. Worse: even `/etc/sudoers.d/bot-nginx` (mode `r--r-----`
   root:root) failed to capture because the `system` phase read files directly
   instead of via `sudo`, and capture always runs as the unprivileged `bot` user
   — it silently warned and skipped, and that warning was easy to miss in the
   capture log. Manually fixed on `5vdb` (added `bot-nginx` and a new
   `bot-full-sudo` sudoers.d file, `visudo -c` validated). Tool fixed: `system.py`
   now reads `/etc/shadow` and `/etc/sudoers.d/*` via `sudo cat` with a
   direct-read fallback, and additionally extracts non-stock "User privilege
   specification" lines from the main `/etc/sudoers` into a new
   `sudoers_main.delta` secret, which restore installs as a separate
   `sudoers.d/99-restored-main-sudoers` file (validated with `visudo -c` before
   being trusted — if invalid, it's discarded with a warning rather than
   breaking sudo on the target).
2. **Redis `CONFIG GET` parsing bug corrupted any config key with an empty
   value.** The old capture filtered out blank lines before pairing up
   key/value pairs, so `requirepass` (legitimately empty — no Redis password
   on the old server) got the *next* key's value instead, recording
   `{"requirepass": "activedefrag"}` in `config.json`. Restore would have
   applied this — `CONFIG SET requirepass activedefrag` — locking every app
   out of Redis with a password none of them know. It happened to not matter
   here only because redis-server got restarted later in this session for an
   unrelated reason, which reverted the runtime-only `CONFIG SET` back to
   `redis.conf`'s real (passwordless) value. Fixed: stopped dropping blank
   lines when splitting the `CONFIG GET *` output.

**If you run another `capture` on the OLD server before decommissioning it,
both fixes apply automatically — no manual steps needed for future
migrations.**

## Second audit pass (2026-06-18) — "is it safe to retire the old server?"

Asked explicitly before decommissioning. Found **3 more real gaps**, all fixed:

### 1. Two real GitHub-backed projects were never in `projects.json` at all
`orchestr-ai` (the orchestrator's own source code — yes, the tool managing
this whole migration) and `restoran` had valid GitHub remotes but were never
registered, so `git-sync`/`projects-clone` never touched them — **they did
not exist on the new server at all** until this pass.
- `orchestr-ai` had 6 dirty files: substantial uncommitted work (new
  `coder`/`monitor`/`research` module scaffolding + a dashboard PRD, ~1140
  lines). Pushing hit merge conflicts against ~38 commits of unrelated work
  already on `origin/main` (including an overlapping PRD page) — **did not
  attempt to auto-resolve conflicting source code**. Instead pushed the
  uncommitted work to a new branch,
  `backup/uncommitted-modules-pre-migration-20260618`, so nothing is lost;
  reconciling it with main is a manual decision for whoever owns that work.
- Both repos cloned fresh from `origin/main` onto the new server.
- `restoran/apps/web/.env.local` (gitignored, not in any commit) copied over manually.
- Both added to `projects.json` (now 26 registered projects) on both servers
  so future captures cover them.

### 2. Telegram bot + watchdog were never running on the new server
`~/.bashrc` (already restored via dotfiles) auto-starts two tmux sessions on
every login — `telegram-bot` (long-polls the Telegram Bot API) and
`bot-watchdog` (restarts `telegram-bot` if it dies, checked every 30s). They
auto-started the moment the first `su -l bot` command ran during this
session — but `telegram_bot.py` crashed immediately every single time
(`ModuleNotFoundError: No module named 'telegram'`) and the watchdog
silently retried forever (the log showed ~1.5 hours of restart attempts,
every 30s, with no visible error since the watchdog itself doesn't surface
the crash reason).

**Root cause: `restore_packages.py` only restores apt packages —
pip-installed Python dependencies (captured in `packages/pip3-freeze.txt`,
123 packages) are never reinstalled on restore.** This is a generic gap, not
specific to the telegram bot — anything relying on a pip package would have
the same silent failure mode. Worked around manually this time:
`pip3 install --break-system-packages -r pip3-freeze.txt` (excluding 3 lines
that conflict with Debian-shipped packages: `gyp` — not a real installable
package, `urllib3`/`idna` — RECORD-file conflicts with apt's python3-urllib3
etc., harmless to skip, apt's version is close enough). Killed the old
server's `telegram-bot`/`bot-watchdog` sessions first (Telegram's
`getUpdates` long-poll is exclusive per bot token — running two pollers
causes HTTP 409 Conflict), then restarted them clean on the new server.
**TODO for the tool**: add a pip-restore phase mirroring the apt one.

### 3. PM2 boot persistence (`pm2 startup`) silently failed during the automated restore
`restore_pm2.py`'s `_configure_startup` ran `pm2 startup` via `su -l` and the
command printed instructions instead of registering anything (needs to run
the *suggested* `sudo env PATH=... pm2 startup ...` line as root, not as the
target user) — the systemd unit `pm2-bot.service` was never created. Means
a reboot would have lost all 7 PM2 apps. Fixed manually: ran
`pm2 startup systemd -u bot --hp /home/bot` directly as root, then `pm2 save`.
Confirmed `pm2-bot.service` now `enabled`. **Not yet fixed in the tool** —
`restore_pm2.py`'s `_configure_startup` needs the same kind of fix `_su()`
got, or to directly run the systemd-registration command as root instead of
parsing pm2's suggested-command text.

### Final answer: yes, safe to retire — with these notes
- All Postgres/Redis/PM2/projects/nginx/SSL/cron/runners state confirmed present and correct.
- `orchestr-ai`'s uncommitted work is safe on a GitHub branch, not lost, but **not yet merged** — someone needs to reconcile it with main.
- Telegram bot + watchdog confirmed running cleanly on the new server only (old server's instance killed to avoid the 409 conflict).
- Remaining tool gaps for next time (not blocking this migration): pip package restore, `pm2 startup` automation.
- The 40+ idle numbered tmux sessions on the old server are just empty leftover shells (no running processes inside) — nothing to migrate there.

## Phase 1 — Transfer bundle to NEW server

```bash
# From the OLD server, or from your local machine:
scp /home/bot/backups/general-backup-*.tar.zst bot@<NEW_SERVER_IP>:/tmp/
scp ~/.config/age/key.txt bot@<NEW_SERVER_IP>:/tmp/age-key.txt   # only if restoring directly there; otherwise transfer out-of-band
```

## Phase 2 — NEW server: bootstrap + restore

```bash
# 1. Clone the tool
git clone https://github.com/zync-code/general-backup.git ~/projects/general-backup
cd ~/projects/general-backup

# 2. Install toolchain (node 22, postgres 16, redis, nginx, age, zstd, pm2, pnpm...)
./bootstrap.sh

# 3. Verify the transferred bundle
./bin/general-backup verify /tmp/general-backup-*.tar.zst

# 4. Restore — agent mode (recommended, handles per-project quirks)
./bin/general-backup restore-agent /tmp/general-backup-*.tar.zst --age-identity /tmp/age-key.txt

#    OR deterministic script mode (no LLM):
./bin/general-backup restore /tmp/general-backup-*.tar.zst --age-identity /tmp/age-key.txt
```

Restore phases: bootstrap → packages → users → state-extract → secrets-decrypt →
projects-clone → postgres → redis → nginx → pm2 → cron → postcheck.
Resumable: re-run the same command if interrupted (done-markers under `/var/lib/general-backup/state/`).

## Phase 3 — Manual steps NOT covered by the bundle

### SSL certificates (11 domains)
Certs are intentionally excluded from the bundle. After DNS cutover, for each domain:
```bash
sudo certbot --nginx -d dibly.me
sudo certbot --nginx -d landlify.com -d www.landlify.com
sudo certbot --nginx -d lekopis.com
sudo certbot --nginx -d mojpausal.com
sudo certbot --nginx -d viewermd.com
sudo certbot --nginx -d hdbp.thinkn.cloud
sudo certbot --nginx -d hdbp-docs.thinkn.cloud
sudo certbot --nginx -d recs.thinkn.cloud -d recs.db.delivery
sudo certbot --nginx -d see.thinkn.cloud
```

### GitHub Actions self-hosted runners (3x — re-register, tokens are single-use)
For each repo, get a fresh token from GitHub UI (Settings → Actions → Runners → New runner) then:
```bash
# zync-automotive
cd ~/actions-runner-automotive   # or wherever configured
./config.sh remove --token <OLD_TOKEN_IF_STILL_VALID>   # on OLD server, to deregister cleanly
# on NEW server:
mkdir -p ~/actions-runner-automotive && cd ~/actions-runner-automotive
# follow GitHub's "New runner" instructions for zync-code/Automotive
./config.sh --url https://github.com/zync-code/Automotive --token <NEW_TOKEN>
sudo ./svc.sh install && sudo ./svc.sh start

# repeat for zync-dev-pulse (zync-code/dev-pulse) and zync-server (zync-code/orchestr-ai)
```

### DNS cutover
1. Test new server first via `/etc/hosts` overrides (point your local machine's hosts file at NEW_SERVER_IP for each domain) before touching DNS.
2. Confirm all 24 projects respond correctly (PM2 status, nginx -t, curl each domain).
3. Lower DNS TTL in advance if possible.
4. Update A/AAAA records for: dibly.me, landlify.com, lekopis.com, mojpausal.com,
   viewermd.com, hdbp.thinkn.cloud, hdbp-docs.thinkn.cloud, recs.thinkn.cloud,
   recs.db.delivery, see.thinkn.cloud (and any apex/www variants).
5. Run the certbot commands above once DNS resolves to the new server.
6. Keep the OLD server running read-only for a few days as fallback before decommissioning.

## Quick reference: what's in vs out of the bundle

IN: Postgres dumps+globals, Redis dump.rdb, PM2 dump.pm2+jlist, nginx config+sites,
cron, system users delta, `~/.orchestrator`, `~/.claude` (filtered), `~/.config`,
dotfiles, package lists, secrets.age (env files, SSH keys, gh tokens, role password
hashes, shadow/sudoers lines).

OUT (by design): project source code (comes from GitHub via projects.json), SSL
certificates, GitHub Actions runner registrations.
