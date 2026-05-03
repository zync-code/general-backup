"""Manifest dataclass + JSON schema + sha256 helpers.

The manifest is the canonical description of a bundle: source host, OS,
captured component summaries, exclusions list, and a pointer to the
checksums file. It is `manifest.json` at the root of the unpacked bundle.
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
TOOL_VERSION = "1.0.0"

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
class Manifest:
    schema_version: int = SCHEMA_VERSION
    tool_version: str = TOOL_VERSION
    captured_at: str = ""
    source: Optional[Source] = None
    components: Dict[str, Any] = dc.field(default_factory=dict)
    exclusions: List[str] = dc.field(default_factory=lambda: list(DEFAULT_EXCLUSIONS))
    checksums_file: str = "checksums.sha256"
    secrets_encrypted: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = dc.asdict(self)
        if self.source is not None:
            d["source"] = self.source.to_dict()
        return d

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
        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            tool_version=data.get("tool_version", TOOL_VERSION),
            captured_at=data.get("captured_at", ""),
            source=Source(**src) if src else None,
            components=data.get("components", {}),
            exclusions=data.get("exclusions", list(DEFAULT_EXCLUSIONS)),
            checksums_file=data.get("checksums_file", "checksums.sha256"),
            secrets_encrypted=data.get("secrets_encrypted", True),
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
        if not self.captured_at:
            errs.append("captured_at must be set")
        if self.source is None:
            errs.append("source must be set")
        else:
            for f in ("hostname", "os", "kernel", "user"):
                if not getattr(self.source, f, None):
                    errs.append(f"source.{f} must be set")
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
