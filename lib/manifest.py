"""Manifest dataclass + JSON schema + sha256 helpers.

The manifest is the canonical description of a bundle: source host, OS,
toolchain versions, captured component summaries, the per-project map
(name, git URL, branch, sha, env paths, pm2 apps, db names), exclusions
list, and a pointer to the checksums file. It is `manifest.json` at the
root of the unpacked bundle.

Schema v2 (PRD §6) is the canonical schema. v1 bundles are no longer
accepted by this tool — they predate the git-based projects model.
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 2
TOOL_VERSION = "2.0.0"

DEFAULT_EXCLUSIONS: List[str] = [
    "node_modules",
    ".next",
    "dist",
    "build",
    ".cache",
    ".turbo",
    "coverage",
    ".claude/cache",
    ".claude/paste-cache",
    ".claude/shell-snapshots",
    ".claude/telemetry",
    ".claude/file-history",
    ".claude/history.jsonl",
]


@dc.dataclass
class Source:
    hostname: str
    os: str
    kernel: str
    user: str
    uid: int

    def to_dict(self) -> Dict[str, Any]:
        return dc.asdict(self)


@dc.dataclass
class Toolchain:
    """Tool versions captured from the source host. Replayed by bootstrap.sh."""

    node: str = ""
    pnpm: str = ""
    pm2: str = ""
    python3: str = ""
    postgres: str = ""
    redis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dc.asdict(self)


@dc.dataclass
class Project:
    """One project tree restored from git, plus the state needed to wire it
    back into pm2 / nginx / postgres on the target host.

    `git_url`, `branch`, and `sha` are the *only* things needed to recover
    source. Everything else here describes how the project plugs into
    services that were captured in the bundle.
    """

    name: str
    git_url: str
    branch: str
    sha: str
    project_dir: str
    deploy_type: str = ""  # nginx | static | pm2-only | …
    env_paths: List[str] = dc.field(default_factory=list)
    pm2_apps: List[str] = dc.field(default_factory=list)
    db_names: List[str] = dc.field(default_factory=list)
    post_install: List[str] = dc.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dc.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        return cls(
            name=data["name"],
            git_url=data["git_url"],
            branch=data.get("branch", "main"),
            sha=data.get("sha", ""),
            project_dir=data.get("project_dir", ""),
            deploy_type=data.get("deploy_type", ""),
            env_paths=list(data.get("env_paths", [])),
            pm2_apps=list(data.get("pm2_apps", [])),
            db_names=list(data.get("db_names", [])),
            post_install=list(data.get("post_install", [])),
        )


@dc.dataclass
class Manifest:
    schema_version: int = SCHEMA_VERSION
    tool_version: str = TOOL_VERSION
    captured_at: str = ""
    source: Optional[Source] = None
    toolchain: Toolchain = dc.field(default_factory=Toolchain)
    projects: List[Project] = dc.field(default_factory=list)
    components: Dict[str, Any] = dc.field(default_factory=dict)
    exclusions: List[str] = dc.field(default_factory=lambda: list(DEFAULT_EXCLUSIONS))
    checksums_file: str = "checksums.sha256"
    secrets_encrypted: bool = True
    runbook_sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool_version": self.tool_version,
            "captured_at": self.captured_at,
            "source": self.source.to_dict() if self.source else None,
            "toolchain": self.toolchain.to_dict(),
            "projects": [p.to_dict() for p in self.projects],
            "components": self.components,
            "exclusions": self.exclusions,
            "checksums_file": self.checksums_file,
            "secrets_encrypted": self.secrets_encrypted,
            "runbook_sha256": self.runbook_sha256,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def write(self, path: os.PathLike) -> None:
        Path(path).write_text(self.to_json() + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: os.PathLike) -> "Manifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Manifest":
        src = data.get("source")
        tc = data.get("toolchain") or {}
        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            tool_version=data.get("tool_version", TOOL_VERSION),
            captured_at=data.get("captured_at", ""),
            source=Source(**src) if src else None,
            toolchain=Toolchain(**tc) if isinstance(tc, dict) else Toolchain(),
            projects=[Project.from_dict(p) for p in data.get("projects", [])],
            components=data.get("components", {}),
            exclusions=data.get("exclusions", list(DEFAULT_EXCLUSIONS)),
            checksums_file=data.get("checksums_file", "checksums.sha256"),
            secrets_encrypted=data.get("secrets_encrypted", True),
            runbook_sha256=data.get("runbook_sha256", ""),
        )

    def validate(self) -> List[str]:
        """Return a list of human-readable errors. Empty list = valid."""
        errs: List[str] = []
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            errs.append("schema_version must be a positive int")
        if self.schema_version > SCHEMA_VERSION:
            errs.append(
                f"schema_version {self.schema_version} is newer than this tool understands "
                f"({SCHEMA_VERSION})"
            )
        if self.schema_version < SCHEMA_VERSION:
            errs.append(
                f"schema_version {self.schema_version} is older than this tool supports "
                f"({SCHEMA_VERSION}); v1 bundles predate the git-based projects model"
            )
        if not self.captured_at:
            errs.append("captured_at must be set")
        if self.source is None:
            errs.append("source must be set")
        else:
            for f in ("hostname", "os", "kernel", "user"):
                if not getattr(self.source, f, None):
                    errs.append(f"source.{f} must be set")
        seen: set = set()
        for i, p in enumerate(self.projects):
            if not p.name:
                errs.append(f"projects[{i}].name must be set")
            if not p.git_url:
                errs.append(f"projects[{i}].git_url must be set")
            if not p.sha:
                errs.append(f"projects[{i}].sha must be set")
            if p.name in seen:
                errs.append(f"projects[].name duplicated: {p.name!r}")
            seen.add(p.name)
        return errs


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: os.PathLike, chunk: int = 1 << 20) -> str:
    """Stream-hash a file. Returns lowercase hex digest."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def sha256_tree(root: os.PathLike) -> Dict[str, str]:
    """SHA-256 every regular file under `root` (recursive). Returns
    {relative_path: hex_digest}, sorted by path for stable output."""
    root = Path(root)
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            out[rel] = sha256_file(p)
    return out


def write_checksums(tree: Dict[str, str], path: os.PathLike) -> None:
    """Write a checksums.sha256 file in the standard `<digest>  <path>` format."""
    lines = [f"{digest}  {rel}\n" for rel, digest in sorted(tree.items())]
    Path(path).write_text("".join(lines), encoding="utf-8")


def parse_checksums(path: os.PathLike) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, rel = line.partition("  ")
        if not digest or not rel:
            continue
        out[rel] = digest
    return out
