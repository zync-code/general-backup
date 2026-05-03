# general-backup

Stateful-delta snapshot and agent-driven restore for Ubuntu 24.04 servers.

`general-backup` rebuilds a server from two ingredients:
1. **Git remotes** — every project tree lives on GitHub. Source code is never bundled.
2. **One bundle file** — the stateful delta that cannot live in git: PostgreSQL dumps, Redis snapshot, encrypted secrets, nginx config, PM2 ecosystem, system users, package lists, orchestrator state.

---

## 60-second quickstart

```bash
# On a fresh Ubuntu 24.04 box:
git clone https://github.com/zync-code/general-backup.git
cd general-backup
./bootstrap.sh                              # installs all toolchain dependencies
general-backup restore-agent ~/snapshot.tar.zst --age-identity ~/.config/age/key.txt
```

That's it. A Claude agent reads the bundle's `manifest.json` and `docs/restore-runbook.md`, then orchestrates the full restore: clones each project from GitHub at the captured SHA, applies env files, restores databases, reloads nginx, and resurrects PM2 processes.

For a non-agent (scripted) restore:

```bash
general-backup restore ~/snapshot.tar.zst --age-identity ~/.config/age/key.txt
```

---

## Capture walkthrough

```bash
# 1. Generate an age key (once, store the private key outside the bundle)
age-keygen -o ~/.config/age/key.txt
cat ~/.config/age/key.txt | grep "public key" | awk '{print $NF}' > ~/.config/age/key.pub

# 2. Produce a bundle on the source server
general-backup capture --age-recipient $(cat ~/.config/age/key.pub)
# Output: general-backup-<host>-<timestamp>.tar.zst

# 3. Transfer to the target server
scp general-backup-*.tar.zst new-host:/tmp/

# 4. Verify integrity (no age key needed for this step)
general-backup verify /tmp/general-backup-*.tar.zst

# 5. Restore
general-backup restore-agent /tmp/general-backup-*.tar.zst --age-identity ~/.config/age/key.txt
```

What capture does, in order:
1. **preflight** — checks required tools, disk space, loads `projects.json`.
2. **git-sync** — pushes every registered project to its GitHub remote. Dirty working trees get an automatic snapshot commit (disable with `--no-snapshot-commit`).
3. **inventory** — records host, OS, toolchain versions.
4. **packages** — apt manual list, dpkg selections, pnpm/npm/pip globals.
5. **system** — passwd/group delta; shadow + sudoers staged for encryption.
6. **nginx** — `/etc/nginx/` config + sites-enabled symlink map.
7. **cron** — bot crontab + `/etc/cron.d/`.
8. **postgres** — globals SQL + per-DB custom dumps + role password hashes (encrypted).
9. **redis** — `SAVE`, copy `dump.rdb`, record non-default CONFIG.
10. **pm2** — `pm2 save`, copy `dump.pm2`, `pm2 jlist`.
11. **state** — tar+zstd of `~/.orchestrator`, `~/.claude` (filtered), `~/.config`, dotfiles.
12. **secrets** — all `.env*` files, `~/.ssh/*`, gh tokens, role passwords, shadow lines → `age -r <recipient>` → `secrets.age`.
13. **checksums** — sha256 every bundle file.
14. **package** — final `tar -I zstd` output, prints path + size + sha256.

---

## Restore walkthrough

### Script mode (deterministic, no LLM)

```bash
general-backup restore snapshot.tar.zst --age-identity ~/.config/age/key.txt
```

Phases run in order: bootstrap → packages → users → state-extract → secrets-decrypt → projects-clone → postgres → redis → nginx → pm2 → cron → postcheck.

Each phase writes a done-marker under `/var/lib/general-backup/state/<phase>.ok`. If restore is interrupted, re-running it resumes from the last incomplete phase.

### Agent mode (recommended for first-time / unusual restores)

```bash
general-backup restore-agent snapshot.tar.zst --age-identity ~/.config/age/key.txt
```

This extracts the bundle, verifies integrity, then spawns a Claude Code agent in a tmux session. The agent reads `docs/restore-runbook.md` (which is also embedded in every bundle) and orchestrates the restore, handling per-project quirks and logging every decision to `/var/log/general-backup-restore.log`.

---

## What's IN vs OUT of the bundle

```
IN THE BUNDLE (stateful delta)          NOT IN THE BUNDLE (comes from git)
─────────────────────────────           ────────────────────────────────────
PostgreSQL globals.sql                  /home/bot/projects/* source trees
Per-DB .dump files (pg_dump custom)     package.json, lockfiles, configs
Redis dump.rdb + non-default CONFIG     ecosystem.config.js
PM2 dump.pm2 + jlist.json              project CLAUDE.md / PRD.md
secrets.age (env files, SSH keys,       .claude/settings.json (committed)
  gh tokens, role passwords,
  shadow, sudoers)
nginx config + sites-enabled map
cron (bot crontab + /etc/cron.d/)
System users delta (passwd/group)
~/.orchestrator (configs, lib, cmds)
~/.claude (settings, plugins, cmds)
~/.config (gh, pnpm, turborepo)
Home dotfiles (.bashrc, .profile)
Package lists (apt, pnpm, npm, pip)
manifest.json + checksums.sha256
restore-runbook.md (snapshot)
```

---

## Bundle layout

```
general-backup-<host>-<UTCstamp>/
├── manifest.json                  # version, projects map, components, checksums ref
├── restore-runbook.md             # the runbook version this bundle was built against
├── README.txt                     # human-readable bundle summary
├── secrets.age                    # age-encrypted vault (all plaintext secrets)
├── data/
│   ├── postgres/
│   │   ├── globals.sql            # roles + tablespaces (no plaintext passwords)
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
│   │   └── etc-cron.d/
│   └── system/
│       ├── passwd.delta
│       └── group.delta
├── state/
│   ├── orchestrator.tar.zst
│   ├── claude.tar.zst
│   ├── config.tar.zst
│   ├── home-dotfiles.tar.zst
│   └── projects.json
├── packages/
│   ├── apt-manual.txt
│   ├── apt-selections.txt
│   ├── npm-global.json
│   ├── pnpm-global.json
│   └── pip3-freeze.txt
└── checksums.sha256
```

---

## Security

All sensitive material passes through [age](https://github.com/FiloSottile/age) before touching the bundle:

- `.env*` files from all project trees
- `~/.ssh/*`
- `~/.config/gh/*` (GitHub tokens)
- PostgreSQL role password hashes (from `pg_authid`)
- `/etc/shadow` lines for restored users
- `/etc/sudoers.d/*`
- `~/.orchestrator/config/settings.json`

Everything outside `secrets.age` is inspectable in the clear — useful for auditing what's in a bundle without the private key.

**Age key management**

```bash
age-keygen -o ~/.config/age/key.txt   # generates private + public key
```

Store `~/.config/age/key.txt` **outside the bundle**, in a password manager or offline key store. Without it, `secrets.age` is unrecoverable. The public key (`key.pub`) is safe to embed in scripts and CI.

See [docs/threat-model.md](./docs/threat-model.md) for the full attacker model.

---

## CLI reference

```bash
general-backup capture [OPTIONS]
  --out PATH                 bundle output directory (default: current dir)
  --age-recipient RECIPIENT  X25519 public key for secrets encryption
  --allow-snapshot-commit    auto-commit dirty projects (default: on)
  --no-snapshot-commit       abort if any project has uncommitted changes
  --include-logs             include ~/.orchestrator/logs/ (default: off)
  --dry-run                  print plan without executing
  --include LIST             comma-separated phase names
  --exclude LIST             comma-separated phase names to skip

general-backup restore BUNDLE [OPTIONS]
  --age-identity FILE        age private key for secrets decryption
  --phases LIST              run only these phases
  --skip-phases LIST         skip these phases
  --dry-run                  print plan without executing
  --force                    overwrite existing project directories

general-backup restore-agent BUNDLE [OPTIONS]
  --age-identity FILE        age private key
  --auto-confirm             skip final agent confirmation prompt

general-backup verify BUNDLE [--age-identity FILE]
general-backup diff BUNDLE
general-backup install-cron [--retain N] [--out-dir PATH]
general-backup phase NAME [--bundle PATH]
```

Exit codes: `0` ok · `1` user error · `2` integrity error · `3` partial restore (resumable) · `4` permission error · `5` git-sync conflict.

---

## Failure-mode FAQ

**Q: Capture failed mid-run. Do I lose everything?**
No. Each phase is independently re-runnable. Re-run the same command; phases that completed are skipped.

**Q: A project has uncommitted changes. Will capture skip it?**
By default, capture creates a snapshot commit (`snapshot: pre-backup capture <timestamp>`) and pushes it. Disable this with `--no-snapshot-commit` (capture exits with code 5 listing dirty repos).

**Q: Restore failed on the projects-clone phase. How do I resume?**
Fix the problem (check network, GitHub credentials) then re-run `general-backup restore`. The done-marker at `/var/lib/general-backup/state/projects-clone.ok` will be absent, so it resumes from there.

**Q: One project failed `pnpm install`. Does restore abort?**
No. Failed installs mark the project as `degraded` and continue. The final `restore-report.md` lists all degraded projects with the error.

**Q: I lost the age private key. Can I recover secrets?**
No. `secrets.age` uses recipient-key encryption; without the private key it is unrecoverable. Store the key outside the bundle.

**Q: The bundle manifest says schema_version 3 but my tool is version 2. What happens?**
Restore refuses with exit code 2. Update `general-backup` first: `cd /home/bot/projects/general-backup && git pull`.

**Q: How do I add a daily automatic capture?**
```bash
general-backup install-cron --retain 7 --out-dir /var/backups/general-backup
```

---

## Repo layout

```
bin/general-backup      CLI entrypoint
lib/
  cli.py                argument parser
  manifest.py           schema v2 dataclass + validator
  log.py                structured logging
  commands/             subcommand implementations
  phases/               capture + restore phase modules
docs/
  architecture.md       design rationale
  restore-runbook.md    canonical agent restore prompt
  threat-model.md       attacker model
  extending.md          adding new components
  operator-faq.md       operational troubleshooting
tests/
  smoke-capture.sh
  git-sync.sh
  restore-in-docker.sh
bootstrap.sh            fresh Ubuntu 24.04 toolchain installer
PRD.md                  product requirements
```

---

## License

MIT — see [LICENSE](./LICENSE).
