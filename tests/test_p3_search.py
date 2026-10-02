from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.rag.hybrid_retriever import (
    BM25Index,
    BM25Retriever,
    HybridRetriever,
    load_catalog_documents,
    split_query_polarity,
    tokenize,
)
from src.rag.retriever import RetrievedChunk, pick_tag_from_chunks


def hit(score: float, key: str, value: str, chunk_id: int) -> RetrievedChunk:
    return RetrievedChunk(
        score=score,
        page_content=f"Evidence for {key}={value}",
        url="https://example.test",
        title=f"Tag:{key}={value}",
        key=key,
        value=value,
        chunk_id=chunk_id,
        dense_score=score,
    )


def write_catalog(root: Path) -> Path:
    path = root / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "key": "aeroway",
                        "value": "airport",
                        "status": "obsolete",
                        "aliases": ["legacy airport tag", "explicit airport tag"],
                        "description": "Existing features explicitly tagged as airports.",
                        "url": "https://wiki.example/airport",
                    },
                    {
                        "key": "aeroway",
                        "value": "aerodrome",
                        "status": "de_facto",
                        "aliases": ["airfield", "airport grounds"],
                        "description": "The whole aviation site.",
                        "url": "https://wiki.example/aerodrome",
                    },
                    {
                        "key": "amenity",
                        "value": "cafe",
                        "status": "approved",
                        "aliases": ["coffee shop"],
                        "description": "A cafe.",
                        "url": "https://wiki.example/cafe",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


class FakeDenseRetriever:
    def __init__(self):
        self.meta = [
            {"page_content": "coffee shop", "url": "u", "title": "cafe", "key": "amenity", "value": "cafe"},
            {"page_content": "more coffee", "url": "u", "title": "cafe", "key": "amenity", "value": "cafe"},
            {"page_content": "airfield", "url": "u", "title": "aerodrome", "key": "aeroway", "value": "aerodrome"},
            {"page_content": "legacy airport", "url": "u", "title": "airport", "key": "aeroway", "value": "airport"},
        ]

    def retrieve(self, _query: str, k: int = 5):
        return [
            hit(0.90, "amenity", "cafe", 0),
            hit(0.85, "amenity", "cafe", 1),
            hit(0.80, "aeroway", "aerodrome", 2),
            hit(0.50, "aeroway", "airport", 3),
        ][:k]


class P3SearchTests(unittest.TestCase):
    def test_tokenizer_normalizes_plural_and_osm_separators(self):
        self.assertEqual(tokenize("Airports traffic_signals"), ["airport", "traffic", "signal"])

    def test_contrastive_query_is_split_into_positive_and_negative_clauses(self):
        positive, negative = split_query_polarity(
            "Find airport terminals, not the whole airfield"
        )
        self.assertEqual(positive, "Find airport terminals")
        self.assertEqual(negative, "the whole airfield")

    def test_bm25_ranks_exact_legacy_tag_above_unrelated_content(self):
        documents = [
            hit(0, "amenity", "cafe", 0),
            RetrievedChunk(0, "legacy airport tag", "u", "airport", "aeroway", "airport"),
        ]
        results = BM25Index(documents).search("explicitly tagged airports")
        self.assertEqual((results[0].key, results[0].value), ("aeroway", "airport"))

    def test_catalog_rejects_duplicate_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_catalog(Path(directory))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["entries"].append(dict(payload["entries"][0]))
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_catalog_documents(path)

    def test_hybrid_returns_one_candidate_per_tag_and_fixes_exact_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            retriever = HybridRetriever(
                FakeDenseRetriever(),
                catalog_path=write_catalog(Path(directory)),
                dense_weight=1.0,
                lexical_weight=1.0,
                tag_match_weight=0.15,
                candidate_k=10,
            )
            results = retriever.retrieve(
                "List aeroway airport features, not general aerodromes", k=3
            )
        tags = [(item.key, item.value) for item in results]
        self.assertEqual(tags[0], ("aeroway", "airport"))
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(results[0].retrieval_method, "hybrid_rrf")
        self.assertIsNotNone(results[0].lexical_score)

    def test_tag_dedup_makes_weighted_fallback_equal_top_candidate(self):
        metadata = FakeDenseRetriever().meta
        with tempfile.TemporaryDirectory() as directory:
            retriever = BM25Retriever(metadata, write_catalog(Path(directory)))
            results = retriever.retrieve("coffee shop", k=3)
        tags = [(item.key, item.value) for item in results]
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(pick_tag_from_chunks(results), tags[0])


if __name__ == "__main__":
    unittest.main()
