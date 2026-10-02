from __future__ import annotations

import unittest

from benchmarks.run_benchmark import (
    percentile,
    reciprocal_rank,
    strict_schema_valid,
    top1_prediction,
    weighted_prediction,
)
from src.rag.retriever import RetrievedChunk


def hit(score: float, key: str, value: str) -> RetrievedChunk:
    return RetrievedChunk(score, "text", "https://example.test", "title", key, value)


class BenchmarkMetricTests(unittest.TestCase):
    def test_reciprocal_rank_and_top1(self):
        hits = [hit(0.9, "amenity", "restaurant"), hit(0.8, "amenity", "cafe")]
        self.assertEqual(top1_prediction(hits), ("amenity", "restaurant"))
        self.assertEqual(reciprocal_rank(hits, ("amenity", "cafe")), 0.5)

    def test_weighted_prediction_aggregates_duplicate_tag_evidence(self):
        hits = [
            hit(0.60, "shop", "supermarket"),
            hit(0.40, "amenity", "cafe"),
            hit(0.35, "amenity", "cafe"),
        ]
        self.assertEqual(weighted_prediction(hits, 3, 0.15), ("amenity", "cafe"))
        self.assertIsNone(weighted_prediction(hits, 3, 0.95))

    def test_percentile_handles_small_samples(self):
        self.assertIsNone(percentile([], 95))
        self.assertEqual(percentile([12.0], 95), 12.0)
        self.assertEqual(percentile([0.0, 10.0], 50), 5.0)

    def test_strict_schema_requires_all_contract_fields(self):
        valid = {
            "place": "Lund",
            "tag": {"key": "amenity", "value": "cafe"},
            "confidence": 0.8,
            "explanation": "Evidence supports the tag.",
        }
        self.assertTrue(strict_schema_valid(valid))
        missing = dict(valid)
        missing.pop("confidence")
        self.assertFalse(strict_schema_valid(missing))


if __name__ == "__main__":
    unittest.main()
