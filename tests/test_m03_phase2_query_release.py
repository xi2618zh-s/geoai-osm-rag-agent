from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.knowledge.evaluation import parse_query_jsonl

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


class Phase2QueryReleaseTests(unittest.TestCase):
    def test_immutable_release_manifest_and_615_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "phase2_queries"
            command = [
                PYTHON, str(ROOT / "scripts/build_m03_phase2_queries.py"),
                "--catalog", str(ROOT / "data/knowledge/osm/v3/releases/m03_phase2_2026-10-06_v1/catalog.json"),
                "--candidates", str(ROOT / "data/knowledge/osm/v3/candidates/m03_phase2_candidates_v1.json"),
                "--output-dir", str(output),
                "--build-timestamp", "2026-10-06T00:00:00Z",
            ]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
            manifest = json.loads((output / "query_manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["dev"]["query_count"], 410)
            self.assertEqual(manifest["public_eval"]["query_count"], 205)
            self.assertEqual(manifest["public_eval"]["target_count"], 205)
            self.assertFalse(manifest["public_eval"]["is_hidden_test"])
            self.assertEqual(len(parse_query_jsonl((output / "dev.jsonl").read_bytes())), 410)
            self.assertEqual(len(parse_query_jsonl((output / "public_eval.jsonl").read_bytes())), 205)
            rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("EXISTS", rejected.stderr + rejected.stdout)
