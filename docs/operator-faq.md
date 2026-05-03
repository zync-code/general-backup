# Operator FAQ

## Capture issues

### Capture aborts with "dirty repo" and exit code 5

A registered project has uncommitted changes and `--no-snapshot-commit` was passed (or the default `--allow-snapshot-commit` was explicitly overridden).

**Fix**: either commit and push the changes manually, or run capture without `--no-snapshot-commit` to let it create a snapshot commit automatically.

```bash
# See which projects are dirty
general-backup capture --dry-run 2>&1 | grep dirty

# Allow automatic snapshot commits (default behaviour)
general-backup capture --age-recipient $(cat ~/.config/age/key.pub)
```

---

### Capture fails on the postgres phase with "permission denied"

The postgres phase reads `pg_authid` to extract role password hashes. This requires SUPERUSER.

**Fix**: run capture as a user with SUPERUSER, or use `psql -U postgres`:

```bash
sudo -u postgres general-backup capture --age-recipient ...
```

If SUPERUSER access is not available, role passwords will be missing from the bundle. Restore will succeed but databases will reject application connections until passwords are set manually.

---

### Capture is running but no output appears for 2+ minutes

The `git-sync` phase is pushing large repositories over the network. This is expected. The phase logs each project name as it starts.

To see verbose output: add `--verbose` to the capture command.

---

### Bundle is larger than expected

Check whether `--include-logs` was passed. The `~/.orchestrator/logs/` directory can be several hundred MB. Omit it unless you specifically need log history in the bundle.

```bash
# Check bundle contents without extracting
tar -tvf general-backup-*.tar.zst | sort -k5 -rn | head -20
```

---

### age is not installed

```bash
sudo apt-get install age
```

age is in the Ubuntu 24.04 main repository.

---

## Restore issues

### Restore fails at secrets-decrypt: "no identity matched a recipient"

The age private key passed via `--age-identity` does not correspond to the public key used at capture time.

**Check**: compare the public key fingerprint:

```bash
age-keygen -y ~/.config/age/key.txt       # prints the public key from a private key file
cat ~/.config/age/key.pub                  # the public key used at capture
```

If they don't match, you are using the wrong private key. Retrieve the correct one from your password manager or offline backup.

---

### Restore fails at projects-clone: "repository not found"

The GitHub repo at the captured URL is private and the `bot` user's GitHub token is not yet installed (secrets-decrypt must run first) — or the token has expired.

**Fix**: ensure secrets-decrypt completed before projects-clone:

```bash
general-backup restore snapshot.tar.zst --age-identity key.txt --phases secrets-decrypt,projects-clone
```

If the token is expired, create a new GitHub PAT, update `~/.config/gh/hosts.yml` manually, then re-run from projects-clone.

---

### One project fails pnpm install during restore

The restore continues and marks the project as `degraded`. After restore completes, check `restore-report.md` for the error.

**Fix**:

```bash
cd /home/bot/projects/<project-name>
pnpm install       # attempt manually; read the error output
```

Common causes: incompatible Node version, network timeout, private npm registry token in `.env` not yet applied (if secrets-decrypt didn't complete before projects-clone).

---

### pm2 resurrect shows 0 processes after restore

The `dump.pm2` file may not have been written at capture time (pm2 was not running, or the pm2 phase was skipped).

**Check**:

```bash
tar -tvf snapshot.tar.zst | grep dump.pm2
```

If `dump.pm2` is present but pm2 still shows 0 processes:

```bash
cat ~/.pm2/dump.pm2    # verify it contains process entries
pm2 resurrect          # retry manually
pm2 save               # save the in-memory list back to dump.pm2
```

---

### nginx -t fails after restore

Check the nginx error log:

```bash
nginx -t 2>&1
sudo journalctl -u nginx --no-pager | tail -30
```

Common causes:
- An env variable used in a `include` directive is not set (secrets-decrypt may not have run).
- A site references a certificate path that does not exist on the target (certificates are not in the bundle; obtain them with certbot).

For Let's Encrypt certificates:

```bash
sudo certbot --nginx -d yourdomain.com
```

---

### Restore is interrupted. How do I resume?

Just re-run the same `restore` command. Done-markers under `/var/lib/general-backup/state/` tell the orchestrator which phases completed. Only the incomplete phases will re-run.

```bash
ls /var/lib/general-backup/state/          # see which phases are done
general-backup restore snapshot.tar.zst --age-identity key.txt
```

To force a phase to re-run, delete its marker:

```bash
rm /var/lib/general-backup/state/postgres.ok
general-backup restore snapshot.tar.zst --age-identity key.txt
```

---

### verify reports checksum mismatch

The bundle file was corrupted in transit.

**Fix**: re-transfer the bundle, then verify again:

```bash
general-backup verify snapshot.tar.zst
```

If the source bundle itself is corrupt (e.g. disk error on the source server), you will need a previous capture. This is why `install-cron` retains 7 bundles by default.

---

## Operational tasks

### Set up daily automatic capture

```bash
general-backup install-cron --retain 7 --out-dir /var/backups/general-backup --age-recipient $(cat ~/.config/age/key.pub)
```

This writes `/etc/cron.d/general-backup` running capture daily and pruning bundles older than N=7.

---

### Verify a bundle without restoring

```bash
general-backup verify snapshot.tar.zst
# With age check (verifies decryptable but does not extract secrets):
general-backup verify snapshot.tar.zst --age-identity ~/.config/age/key.txt
```

---

### Check what a restore would change on the current host

```bash
general-backup diff snapshot.tar.zst
```

Output is grouped by component (projects, postgres, redis, pm2, nginx, packages) and shows:
- Projects in manifest but missing on host
- Projects on host but not in manifest
- Databases missing from host
- pm2 process count delta
- Package list diff

---

### Rotate the age key

Rotating the encryption key requires re-capturing with the new key. There is no re-encryption path for existing bundles (age bundles are not re-keyable without decrypting and re-encrypting).

1. Generate a new key: `age-keygen -o ~/.config/age/key-new.txt`
2. Run a fresh capture: `general-backup capture --age-recipient $(age-keygen -y ~/.config/age/key-new.txt)`
3. Store `key-new.txt` in your password manager.
4. Retire the old key after confirming the new bundle restores correctly.

---

### Capture only specific components

```bash
# Capture only databases and redis (skip everything else)
general-backup capture --include postgres,redis --age-recipient $(cat ~/.config/age/key.pub)

# Capture everything except the state tarballs
general-backup capture --exclude state --age-recipient $(cat ~/.config/age/key.pub)
```

---

### Run a single phase manually

```bash
# Capture phase (writes to a staging dir)
general-backup phase capture-postgres

# Restore phase (reads from a bundle)
general-backup phase restore-projects --bundle /tmp/snapshot.tar.zst
```

Useful for debugging a specific phase without running the full pipeline.
