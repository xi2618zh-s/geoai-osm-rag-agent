from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_reproducibility import audit
from scripts.fetch_osm_data import (
    ChecksumMismatch,
    download_verified,
    load_manifest,
    parse_checksum,
)


class DataDeliveryTests(unittest.TestCase):
    def test_manifest_declares_https_pbf_and_checksum(self):
        manifest = load_manifest()
        self.assertTrue(manifest["pbf_url"].startswith("https://"))
        self.assertTrue(manifest["checksum_url"].endswith(".md5"))
        self.assertEqual(manifest["checksum_algorithm"], "md5")

    def test_checksum_parser_supports_provider_md5_and_pinned_sha256(self):
        algorithm, digest = parse_checksum("a" * 32 + "  sweden.osm.pbf\n")
        self.assertEqual((algorithm, digest), ("md5", "a" * 32))
        algorithm, digest = parse_checksum("sha256:" + "b" * 64)
        self.assertEqual((algorithm, digest), ("sha256", "b" * 64))

    def test_verified_download_is_atomic_and_reusable(self):
        content = b"small deterministic PBF fixture"
        digest = hashlib.md5(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            output = root / "output.osm.pbf"
            source.write_bytes(content)
            first = download_verified(
                source.as_uri(), output, algorithm="md5", expected_digest=digest
            )
            second = download_verified(
                source.as_uri(), output, algorithm="md5", expected_digest=digest
            )
            self.assertEqual(first["status"], "downloaded_verified")
            self.assertEqual(second["status"], "existing_verified")
            self.assertEqual(output.read_bytes(), content)
            self.assertFalse(output.with_name(output.name + ".part").exists())

    def test_bad_download_checksum_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            output = root / "output.osm.pbf"
            source.write_bytes(b"corrupt")
            with self.assertRaises(ChecksumMismatch):
                download_verified(
                    source.as_uri(),
                    output,
                    algorithm="md5",
                    expected_digest="0" * 32,
                )
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name(output.name + ".part").exists())

    def test_ci_reproducibility_audit_passes(self):
        report = audit(ci=True)
        if not report["passed"]:
            self.fail(json.dumps(report, indent=2))


if __name__ == "__main__":
    unittest.main()
