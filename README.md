# general-backup

Full-server snapshot and restore toolkit for Ubuntu 24.04.

`general-backup` produces a single bundle that captures everything required to
rebuild a Linux server: project trees, PostgreSQL/Redis data, nginx config,
PM2 ecosystem, system users, SSH keys, cron, agent/skill state, package
manifests. A restore on a fresh box brings it back online with one command.

## Quickstart

```bash
# 1. On the source server: produce a bundle
general-backup capture --age-recipient $(cat ~/.config/age/key.pub)

# 2. Copy the bundle to the new server
scp general-backup-*.tar.zst new-host:/tmp/

# 3. On the new server: restore
general-backup restore /tmp/general-backup-*.tar.zst --age-identity ~/.config/age/key.txt
```

Full docs live in [`docs/`](./docs) and the [PRD](./PRD.md).

## CLI surface

| Command  | Purpose                                                    |
|----------|------------------------------------------------------------|
| capture  | Produce a `<host>-<utcstamp>.tar.zst` bundle               |
| restore  | Replay a bundle on a fresh Ubuntu 24.04 host               |
| verify   | Check tarball integrity, manifest schema, age decryptable  |
| diff     | Compare a bundle against the live host                     |
| install  | Bootstrap a fresh box with the minimum toolchain           |

Run `general-backup <subcommand> --help` for full options.

## What's captured

- `/home/bot/projects/` (excluding `node_modules`, `.next`, `dist`, `build`, etc.)
- `~/.orchestrator`, `~/.claude` (filtered), `~/.config`
- All `.env*` files (encrypted), `~/.ssh/*` (encrypted), gh tokens (encrypted)
- PostgreSQL globals + per-DB `pg_dump --format=custom`
- Redis `dump.rdb` + non-default `CONFIG`
- PM2 `dump.pm2` + `jlist`
- nginx `nginx.conf`, `sites-available/`, `conf.d/`, `sites-enabled` symlink map
- Cron (`bot` crontab + `/etc/cron.d/*`)
- System users delta (`/etc/passwd`, `/etc/group`, `/etc/sudoers.d/*`)
- Package manifests (`apt-mark`, `dpkg --get-selections`, `pnpm/npm/pip`)

See [PRD § 5](./PRD.md#5-scope--what-is-captured--out-of-scope) for the full list.

## Security

All sensitive data (env files, ssh keys, tokens, postgres role passwords,
shadow lines, sudoers) is encrypted into `secrets.age` using
[age](https://github.com/FiloSottile/age) with a recipient public key.
The rest of the bundle is inspectable without the identity.

## Repo layout

```
bin/general-backup     # CLI entrypoint
lib/                   # Phase modules + manifest
tests/                 # Smoke and round-trip tests
docs/                  # Architecture, runbook, threat model
PRD.md                 # Product requirements document
```

## License

MIT — see [LICENSE](./LICENSE).
