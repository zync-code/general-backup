# Architecture

## Why git instead of bundling project source?

The server hosts ~20 projects, totalling 11 GB on disk. If bundled, the snapshot would be slow to produce and slow to transfer. More importantly, the source is already in GitHub: every project has a registered `github_repo` in `~/.orchestrator/config/projects.json`. The bundle captures only the *difference* between what's in git and what a running server needs — roughly 90% of the size savings come from this one decision.

The git pre-sync phase (`git-sync`) is the enforcement mechanism. Before any data capture, every project is pushed to its remote. The manifest records the exact SHA that was pushed. On restore, `git clone <url> && git checkout <sha>` reproduces the exact tree without any data living in the bundle.

## Why a stateful-delta bundle?

Some state genuinely cannot live in git:

- **PostgreSQL data** — 10 databases with user content; `pg_dump` output is not committed to any repo.
- **Redis state** — runtime cache and job queues.
- **Secrets** — `.env` files, SSH keys, GitHub tokens, Postgres role passwords. These must never appear in git history, even in private repos.
- **nginx config** — lives under `/etc/nginx/`, not in any project repo.
- **System users** — `bot`'s uid, shadow lines, sudoers entries.
- **Orchestrator + agent config** — `~/.orchestrator/` and `~/.claude/` contain command definitions, plugin configs, and project registries that are not project-specific.

The bundle is a `tar.zst` archive. `zstd` at level 19 gives good compression with acceptable speed. The choice over `gzip` or `bz2` is wall-clock time at the compression level that matters: database dumps compress well and `zstd -19` is fast enough for the < 5 min target.

## Why age for encryption?

[age](https://github.com/FiloSottile/age) (Actually Good Encryption) is:

- Simple: one recipient public key → one encrypted blob. No keyring management.
- Auditable: small codebase, no footguns around modes or padding.
- Apt-installable: `apt-get install age` on Ubuntu 24.04.
- Supports recipient (X25519) and passphrase modes; we use recipient.

The alternative (GPG) adds complexity with trust models, keyservers, and expiration. `age` does exactly what's needed: encrypt-to-public-key, decrypt-with-private-key.

## Why an agent for restore?

A monolithic shell script restoring 20 projects, 10 databases, and 5 nginx vhosts is brittle. Each project may need different steps: some need `pnpm install && pnpm build`, others just need env files and a PM2 entry. Some databases might already exist on the target. Some PM2 processes might conflict with existing entries.

An agent (Claude Code via the orchestrator already used by `bot`) can:

- Read the manifest and adapt per project.
- Retry a failed `pnpm install` with a different strategy.
- Ask the operator what to do when a database already exists with different schema.
- Produce a readable restore-report.md with a summary of what worked and what didn't.

Script mode (`general-backup restore`) remains the deterministic baseline. Agent mode is a layer on top that adds judgment for first-time or unusual restores.

## Phase design

Both capture and restore are decomposed into phases. Each phase:

- Receives a `Context` object with a staging directory, manifest reference, and parsed args.
- Writes its output to `ctx.staging/` (or `ctx.secrets_staging/` for sensitive files).
- Raises `PhaseError` on failure; the orchestrator in `commands/capture.py` / `commands/restore.py` decides whether to abort or continue.
- Is independently re-runnable (idempotent).

Restore phases additionally write done-markers under `/var/lib/general-backup/state/<phase>.ok`. The orchestrator skips a phase if its marker exists. This enables resumability after a partial restore.

## Manifest as the contract

`manifest.json` is the single source of truth between capture and restore. Its schema (version 2) is defined in `lib/manifest.py`:

- `projects[]` — what to clone, where, at what SHA, which env files, which PM2 apps, which databases.
- `components` — summary counts (db count, pm2 process count, vhost count) used for post-restore verification.
- `toolchain` — pinned versions that `bootstrap.sh` must install.
- `schema_version` — restore refuses if the bundle's version exceeds the running tool's.
- `checksums_file` — path to `checksums.sha256` which covers every file in the bundle except the checksum file itself.

The `restore-runbook.md` embedded in the bundle is the version that was current at capture time. This ensures the agent running restore reads instructions consistent with the bundle's manifest, even if the repo has evolved.

## Trust boundaries

```
┌─────────────────────────────────────────────┐
│ secrets.age (encrypted at-rest)             │
│   .env files, SSH keys, gh tokens,          │
│   pg role passwords, shadow, sudoers         │
└─────────────────────────────────────────────┘
          ↑ age public key (safe to expose)
          ↓ age private key (never in bundle)

┌─────────────────────────────────────────────┐
│ rest of bundle (cleartext, inspectable)     │
│   manifest.json, checksums.sha256           │
│   postgres dumps, redis rdb                 │
│   nginx config, cron, system users delta    │
│   state tarballs, package lists             │
└─────────────────────────────────────────────┘
```

Anything in the cleartext portion is still private but not secret: it reveals database names, project names, nginx vhost names, and crontab schedules — operational metadata that doesn't grant access to running services.
