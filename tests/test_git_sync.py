"""Unit tests for git-sync phase — projects.json parsing and manifest.projects[] population."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.phases import Context, PhaseError, load_projects_json, project_entries
from lib.manifest import Manifest


class LoadProjectsJsonTests(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        result = load_projects_json("/nonexistent/path/projects.json")
        self.assertEqual(result, {"projects": {}})

    def test_loads_valid_json(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"projects": {"foo": {"github_repo": "https://github.com/x/foo"}}}, f)
            path = f.name
        try:
            result = load_projects_json(path)
            self.assertIn("projects", result)
            self.assertIn("foo", result["projects"])
        finally:
            os.unlink(path)

    def test_returns_empty_on_missing_github_repo(self) -> None:
        raw = {"projects": {"no-remote": {"name": "no-remote"}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(raw, f)
            path = f.name
        try:
            entries = project_entries(load_projects_json(path))
            self.assertEqual(entries, [])
        finally:
            os.unlink(path)


class ProjectEntriesTests(unittest.TestCase):
    def _make_projects_json(self, projects: dict) -> dict:
        return {"projects": projects}

    def test_returns_sorted_by_name(self) -> None:
        raw = self._make_projects_json({
            "zebra": {"github_repo": "https://github.com/x/zebra"},
            "alpha": {"github_repo": "https://github.com/x/alpha"},
        })
        entries = project_entries(raw)
        self.assertEqual([e["name"] for e in entries], ["alpha", "zebra"])

    def test_injects_name_key(self) -> None:
        raw = self._make_projects_json({
            "my-proj": {"github_repo": "https://github.com/x/my-proj", "deploy_type": "nginx"}
        })
        entries = project_entries(raw)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "my-proj")
        self.assertEqual(entries[0]["deploy_type"], "nginx")

    def test_skips_non_dict_entries(self) -> None:
        raw = self._make_projects_json({"bad": "not-a-dict"})
        entries = project_entries(raw)
        self.assertEqual(entries, [])

    def test_empty_projects_dict(self) -> None:
        self.assertEqual(project_entries({"projects": {}}), [])
        self.assertEqual(project_entries({}), [])


class GitSyncManifestPopulationTests(unittest.TestCase):
    """Integration-style tests using a real bare-repo + working clone."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._bare = Path(self._tmp) / "origin.git"
        self._work = Path(self._tmp) / "work"

        subprocess.run(["git", "init", "--bare", str(self._bare)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self._bare), str(self._work)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self._work), "config", "user.email", "t@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self._work), "config", "user.name", "Test"], check=True, capture_output=True)

        # Initial commit
        (self._work / "README.md").write_text("hello")
        subprocess.run(["git", "-C", str(self._work), "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self._work), "commit", "-m", "init"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self._work), "push", "origin", "HEAD"], check=True, capture_output=True)
        self._initial_sha = subprocess.check_output(
            ["git", "-C", str(self._work), "rev-parse", "HEAD"],
            text=True
        ).strip()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_ctx(self, allow_snapshot: bool = True) -> Context:
        staging = Path(tempfile.mkdtemp(prefix="gb-test-staging-"))
        secrets = Path(tempfile.mkdtemp(prefix="gb-test-secrets-"))

        class FakeArgs:
            allow_snapshot_commit = allow_snapshot
            include_logs = False
            dry_run = False
            out = None
            age_recipient = None

        projects_json = {
            "projects": {
                "test-proj": {
                    "github_repo": f"file://{self._bare}",
                    "project_dir": str(self._work),
                    "deploy_type": "nginx",
                    "pm2_apps": ["my-api"],
                    "db_names": ["my_db"],
                }
            }
        }
        manifest = Manifest()
        ctx = Context(
            args=FakeArgs(),
            staging=staging,
            secrets_staging=secrets,
            manifest=manifest,
            projects_json=projects_json,
        )
        return ctx

    def test_clean_repo_populates_manifest(self) -> None:
        from lib.phases.git_sync import run
        ctx = self._make_ctx()
        run(ctx)

        self.assertEqual(len(ctx.manifest.projects), 1)
        proj = ctx.manifest.projects[0]
        self.assertEqual(proj.name, "test-proj")
        self.assertEqual(proj.sha, self._initial_sha)
        self.assertEqual(proj.deploy_type, "nginx")
        self.assertEqual(proj.pm2_apps, ["my-api"])
        self.assertEqual(proj.db_names, ["my_db"])

    def test_dirty_no_snapshot_raises_exit5(self) -> None:
        from lib.phases.git_sync import run
        (self._work / "README.md").write_text("modified")
        ctx = self._make_ctx(allow_snapshot=False)
        with self.assertRaises(PhaseError) as cm:
            run(ctx)
        self.assertEqual(cm.exception.exit_code, 5)

    def test_dirty_with_snapshot_creates_commit(self) -> None:
        from lib.phases.git_sync import run
        (self._work / "README.md").write_text("modified")
        ctx = self._make_ctx(allow_snapshot=True)
        run(ctx)

        self.assertEqual(len(ctx.manifest.projects), 1)
        new_sha = ctx.manifest.projects[0].sha
        self.assertNotEqual(new_sha, self._initial_sha)

        # Verify new SHA exists on origin
        result = subprocess.run(
            ["git", "-C", str(self._bare), "cat-file", "-t", new_sha],
            capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "commit")


if __name__ == "__main__":
    unittest.main()
