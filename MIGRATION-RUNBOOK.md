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
