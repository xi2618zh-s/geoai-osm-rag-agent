from pathlib import Path
import json
import unittest

from scripts.build_m11b_hidden import audit_private
from src.evaluation.m11b import load_m11b_tasks

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "benchmarks/data/test/private"
RECEIPT = ROOT / "benchmarks/data/test/m11b_private_receipt_v1.json"


class HiddenSplitTests(unittest.TestCase):
    def test_private_split_is_present_isolated_and_rebuildable(self):
        receipt = json.loads(RECEIPT.read_text("utf-8"))
        self.assertEqual(receipt["task_count"], 40)
        self.assertNotIn("seed", receipt)
        self.assertNotIn("query", receipt)
        if not PRIVATE.is_dir():
            self.skipTest("private holdout intentionally absent from checkout")
        tasks = load_m11b_tasks(PRIVATE / "m11b_test_tasks_v1.jsonl")
        self.assertEqual(len(tasks), 40)
        self.assertEqual({t.split for t in tasks}, {"test"})
        self.assertEqual(len({t.task_id for t in tasks}), 40)
        self.assertEqual(len({t.query.casefold() for t in tasks}), 40)
        self.assertTrue(audit_private(PRIVATE, RECEIPT))


if __name__ == "__main__":
    unittest.main()

