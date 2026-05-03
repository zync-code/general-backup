# Threat Model

## What an attacker gets with the bundle but no age key

The bundle (`general-backup-<host>-<timestamp>.tar.zst`) splits into two parts: a cleartext section and `secrets.age`.

With only the bundle (no age private key), an attacker can read:

| What they can read | What they learn |
|--------------------|-----------------|
| `manifest.json` | Hostname, OS, project names, GitHub URLs, database names, PM2 process names, nginx vhost names |
| `data/postgres/globals.sql` | Role names, tablespace names — **no passwords** |
| `data/postgres/<db>.dump` | Full database contents (user data, application data) |
| `data/redis/dump.rdb` | Full Redis keyspace (session data, cache, job queues) |
| `data/nginx/` | Full nginx config including upstream addresses, locations, SSL certificate paths |
| `data/pm2/jlist.json` | Process names, cwd, env var **names** (but not values — those are in secrets.age) |
| `data/system/passwd.delta` | Non-system usernames and uids |
| `state/*.tar.zst` | Orchestrator command definitions, Claude plugin configs, non-secret app configs |
| `packages/` | Installed package list |

What they **cannot** read without the age key:

- `.env*` file contents (API keys, database passwords, third-party tokens)
- `~/.ssh/*` private keys
- GitHub tokens (`~/.config/gh/hosts.yml`)
- PostgreSQL role password hashes
- `/etc/shadow` entries
- `/etc/sudoers.d/` entries
- `~/.orchestrator/config/settings.json` (telegram bot token, etc.)

### Impact without the age key

An attacker with the bundle but no key can:
- Read all application data from the postgres dumps and redis snapshot.
- Map the full server topology (projects, services, vhosts, processes).
- Attempt to restore a clone without `secrets-decrypt` (no env files or SSH keys; postgres will have wrong role passwords; nginx will start but apps won't connect to APIs).

They **cannot** authenticate to GitHub, connect to external APIs, SSH into other servers, or gain `sudo` access to the target.

---

## What an attacker gets with the age key alone

With only `~/.config/age/key.txt` (the age private key) and **no bundle**, an attacker can decrypt any bundle that was encrypted to the corresponding public key. The key itself reveals nothing on its own.

---

## What an attacker gets with both bundle and age key

Full server reconstruction capability. This is equivalent to root access to the original server from a data-access perspective.

This is why the private key must be stored outside the bundle: in a password manager, offline hardware key, or separate secure storage that is not transmitted alongside the bundle.

---

## Threat scenarios

### Bundle intercepted in transit

**Risk**: an attacker intercepts the `scp` or file transfer of the bundle.

**Mitigation**: they get all application data (postgres, redis) and the full server topology but not credentials or keys. Transfer the bundle over SSH; the cleartext exposure is an operational risk, not a cryptographic one.

**Recommendation**: use `general-backup verify` on the target after transfer to confirm integrity.

---

### Bundle stored on a shared or compromised server

**Risk**: the bundle is stored in a location accessible to other users or a compromised process.

**Mitigation**: restrict bundle file permissions (`chmod 600`). The age key should never be co-located with the bundle on the same storage medium.

---

### Age key stored in the same place as the bundle

**Risk**: operator copies `key.txt` next to the bundle for convenience.

**Mitigation**: the age key file is deliberately not included in any automated workflow. The operator must explicitly pass `--age-identity FILE` at restore time.

**Recommendation**: store the age private key in a password manager and export it to a file only at restore time.

---

### Snapshot commit leaks secrets

**Risk**: the `--allow-snapshot-commit` mode commits all tracked files and pushes. If any `.env*` file was accidentally `git add`-ed, it lands on GitHub.

**Mitigation**: the `git-sync` phase does `git add -A` only on files already tracked (it does not add untracked files). `.env*` files are typically in `.gitignore`. The smoke-capture test (`tests/smoke-capture.sh`) greps the entire cleartext bundle for patterns like `ghp_|gho_|sk-|password|BEGIN [A-Z]+ PRIVATE KEY` and fails if any appear outside `secrets.age`.

---

### Restore on a non-isolated target exposes secrets

**Risk**: `secrets-decrypt` extracts age contents to a tmpfs staging directory and writes env files to project directories. If the target has other users or processes, they could read these files during restore.

**Mitigation**: restore should be run on a freshly provisioned host with only the `bot` user. The staging tmpfs is `chmod 700`. Env files are written with mode `600`.

---

### Postgres role passwords captured incorrectly

**Risk**: `pg_authid` extract requires SUPERUSER. If the capture user does not have SUPERUSER, role passwords are not captured and restore will fail to authenticate apps to their databases.

**Mitigation**: the preflight phase checks for SUPERUSER access and warns if unavailable. The capture can proceed without role passwords but the restore-report will flag affected roles.

---

## What age does NOT protect against

- Metadata: bundle filenames, directory structure, file sizes, and modification times are visible in the tarball even without the age key.
- Future key compromise: age does not provide forward secrecy. If the private key is compromised in the future, all past bundles encrypted to that public key become decryptable.
- Integrity of the cleartext section: `checksums.sha256` covers all bundle files and is verified by `general-backup verify`, but an attacker with write access to the bundle could tamper with both the data and the checksum file. For high-assurance scenarios, sign the checksum file with `--sign KEYFILE` at capture time.
