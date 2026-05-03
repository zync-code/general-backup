# Extending general-backup

## Adding a new capture component (e.g. MongoDB)

This example walks through adding MongoDB support. The same pattern applies to any new service.

### 1. Write the capture phase module

Create `lib/phases/mongodb.py`:

```python
"""MongoDB capture phase — dump all databases into staging."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import Context, PhaseError


def run(ctx: Context) -> None:
    out_dir = ctx.ensure_dir("data", "mongodb")

    result = subprocess.run(
        ["mongodump", "--out", str(out_dir), "--gzip"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise PhaseError(f"mongodump failed: {result.stderr}")

    # Record in manifest
    db_names = [p.name for p in out_dir.iterdir() if p.is_dir()]
    ctx.manifest.components["mongodb"] = {"databases": db_names}
```

### 2. Write the restore phase module

Create `lib/phases/restore_mongodb.py`:

```python
"""MongoDB restore phase — restore all databases from staging."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import Context, PhaseError


def run(ctx: Context) -> None:
    dump_dir = ctx.staging / "data" / "mongodb"
    if not dump_dir.exists():
        return  # nothing to restore

    result = subprocess.run(
        ["mongorestore", "--gzip", str(dump_dir)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise PhaseError(f"mongorestore failed: {result.stderr}")
```

### 3. Add the preflight tool check

In `lib/phases/preflight.py`, add `"mongodump"` and `"mongorestore"` to the required tools list (or make them conditional on whether MongoDB is detected).

### 4. Register the phase in the capture pipeline

In `lib/commands/capture.py`, add `"mongodb"` to the default phase list in the correct position (after `redis`, before `pm2`). The phase name must match the module filename.

### 5. Register the restore phase

In `lib/commands/restore.py`, add `"mongodb"` to the restore phase list (after `redis`, before `nginx`).

### 6. Update bootstrap.sh

Add `mongodb-org` to the apt install block in `bootstrap.sh`. Pin the version to match what the manifest records.

### 7. Update the manifest schema

In `lib/manifest.py`, add `mongodb` to the `components` TypedDict if you want strict schema validation.

### 8. Add a test

In `tests/smoke-capture.sh`, verify that `data/mongodb/` appears in the bundle and that `general-backup verify` still passes.

---

## Adding a new project type

Projects are registered in `~/.orchestrator/config/projects.json`. The `deploy_type` field controls how the restore agent handles post-clone setup.

### Current deploy types

| `deploy_type` | What restore does |
|---------------|-------------------|
| `nginx` | `pnpm install`, `pnpm build`, links nginx vhost |
| (unset) | `pnpm install` only, no nginx |

### Adding a new deploy type (e.g. `docker-compose`)

1. In `lib/phases/git_sync.py`, the `deploy_type` from `projects.json` is passed through to `manifest.projects[]` as-is. No code change needed here.

2. In `docs/restore-runbook.md`, add a section describing what the agent should do for `deploy_type: docker-compose`:
   ```
   If deploy_type == "docker-compose":
     Run: docker compose up -d
     Verify: docker compose ps shows all services Up
   ```

3. In `lib/phases/restore_projects.py` (script-mode restore), add a branch:
   ```python
   if project["deploy_type"] == "docker-compose":
       subprocess.run(["docker", "compose", "up", "-d"], cwd=project_dir, check=True)
   ```

4. Update `bootstrap.sh` to install Docker if any registered project uses `deploy_type: docker-compose`.

---

## Adding a new per-project metadata field

Say you want to capture a `post_install` list of commands per project.

1. In `lib/phases/git_sync.py`, read the field from `projects.json`:
   ```python
   entry["post_install"] = proj.get("post_install", [])
   ```

2. In `lib/manifest.py`, add `post_install: List[str]` to the project entry TypedDict.

3. In `lib/phases/restore_projects.py`, execute the commands after `pnpm install`:
   ```python
   for cmd in project.get("post_install", []):
       subprocess.run(cmd, shell=True, cwd=project_dir, check=True)
   ```

4. In `docs/restore-runbook.md`, document that the agent should run `post_install` commands after the standard install steps.

---

## Changing the secrets vault contents

All files to be encrypted land in `ctx.secrets_staging/` during their respective phases. The `secrets` phase then tars and pipes this directory through `age`.

To add a new secret type (e.g. a per-project API key file at a custom path):

1. In the relevant capture phase (e.g. `lib/phases/state.py`), copy the file to `ctx.secrets_dir("my-service/api-key.txt")`.

2. In the restore phase, after `secrets-decrypt` has placed files in the tmpfs staging dir, read the file from the staging path and install it at the correct location on disk.

3. Record the target path in `manifest.projects[].env_paths` (or a new manifest field) so the agent knows where it was placed and can verify it's present after restore.
