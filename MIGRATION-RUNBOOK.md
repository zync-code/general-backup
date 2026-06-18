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
Remaining manual step before cutover: nginx -t still fails until certbot runs
for each domain (expected — see Phase 3).

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
