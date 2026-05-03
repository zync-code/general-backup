"""Capture phase: checksums — sha256 every file in staging → checksums.sha256."""
from __future__ import annotations

from ..log import info
from ..manifest import sha256_tree, write_checksums
from . import Context


def run(ctx: Context) -> None:
    info("checksums: hashing bundle files")
    tree = sha256_tree(ctx.staging)

    checksum_file = ctx.staging / ctx.manifest.checksums_file
    write_checksums(tree, checksum_file)

    info(f"checksums: {len(tree)} file(s) hashed → {ctx.manifest.checksums_file}")
