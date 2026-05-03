"""Unit tests for lib.manifest. Uses stdlib unittest (no pytest dep)."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.manifest import (  # noqa: E402
    Manifest,
    Source,
    parse_checksums,
    sha256_file,
    sha256_tree,
    utc_now_iso,
    write_checksums,
)


class ManifestTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            m = Manifest(
                captured_at=utc_now_iso(),
                source=Source(hostname="h1", os="Ubuntu 24.04", kernel="6.8.0", user="bot", uid=1000),
                components={"postgres": {"databases": ["a", "b"], "version": "16"}},
            )
            p = tmp / "manifest.json"
            m.write(p)
            m2 = Manifest.read(p)
            self.assertIsNotNone(m2.source)
            assert m2.source is not None
            self.assertEqual(m2.source.hostname, "h1")
            self.assertEqual(m2.components["postgres"]["databases"], ["a", "b"])
            self.assertEqual(m2.validate(), [])

    def test_validate_catches_missing_fields(self) -> None:
        m = Manifest()
        errs = m.validate()
        self.assertTrue(any("captured_at" in e for e in errs))
        self.assertTrue(any("source" in e for e in errs))

    def test_rejects_future_schema(self) -> None:
        m = Manifest(
            schema_version=999,
            captured_at=utc_now_iso(),
            source=Source(hostname="h", os="x", kernel="y", user="z", uid=0),
        )
        errs = m.validate()
        self.assertTrue(any("newer" in e for e in errs))

    def test_json_is_stable(self) -> None:
        m = Manifest(
            captured_at="2026-05-03T00:00:00Z",
            source=Source(hostname="h", os="o", kernel="k", user="u", uid=1),
        )
        parsed = json.loads(m.to_json())
        self.assertEqual(parsed["schema_version"], 1)
        self.assertTrue(parsed["secrets_encrypted"])


class HashTests(unittest.TestCase):
    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.bin"
            p.write_bytes(b"hello world")
            self.assertEqual(sha256_file(p), hashlib.sha256(b"hello world").hexdigest())

    def test_sha256_tree_and_checksums_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "a.txt").write_text("aaa")
            (tmp / "sub").mkdir()
            (tmp / "sub" / "b.txt").write_text("bbb")
            tree = sha256_tree(tmp)
            self.assertEqual(set(tree.keys()), {"a.txt", "sub/b.txt"})
            cks = tmp / "checksums.sha256"
            write_checksums(tree, cks)
            parsed = parse_checksums(cks)
            self.assertEqual(parsed, tree)


if __name__ == "__main__":
    unittest.main()
