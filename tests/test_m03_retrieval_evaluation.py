from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from src.knowledge import validate_catalog_payload
from src.knowledge.evaluation import (
    EvaluationLeakageError,
    PublicEvaluationAlreadyRunError,
    audit_query_leakage,
    compute_retrieval_metrics,
    write_public_evaluation_receipt,
)
from src.knowledge.contracts import RetrievalQueryV1


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "knowledge" / "m03" / "v2"


def catalog():
    return validate_catalog_payload(
        json.loads((FIXTURE_DIR / "valid_catalog.json").read_text("utf-8"))
    )


def query_payload(**overrides):
    payload = {
        "schema_version": "1.0",
        "query_id": "m03:dev:amenity=cafe:en",
        "split": "dev",
        "language": "en",
        "query_text": "Where can I get coffee and a light meal?",
        "target_tag": "amenity=cafe",
        "excluded_tags": ["amenity=restaurant"],
        "primary_category": "food",
        "challenge_type": "paraphrase",
        "construction_note": "Project-authored prompt; Codex-assisted; manually reviewed.",
        "author": "GeoAI project team",
        "construction_method": "codex_assisted_manual_review",
        "codex_assisted": True,
        "catalog_version": "fixture.m03.v2",
    }
    payload.update(overrides)
    return payload


class RetrievalEvaluationTests(unittest.TestCase):
    def test_query_contract_is_strict_and_hard_negative_is_not_self(self):
        query = RetrievalQueryV1.model_validate(query_payload())
        self.assertEqual(query.target_tag, "amenity=cafe")
        with self.assertRaises(ValidationError):
            RetrievalQueryV1.model_validate(query_payload(extra=True))
        with self.assertRaises(ValidationError):
            RetrievalQueryV1.model_validate(
                query_payload(excluded_tags=["amenity=cafe"])
            )

    def test_leakage_audit_rejects_query_text_in_catalog_fields(self):
        clean = [RetrievalQueryV1.model_validate(query_payload())]
        report = audit_query_leakage(catalog(), clean)
        self.assertEqual(report.leak_count, 0)

        leaked_catalog = deepcopy(catalog().model_dump(mode="json"))
        leaked_catalog["entries"][0]["aliases_en"].append(clean[0].query_text)
        with self.assertRaises(EvaluationLeakageError):
            audit_query_leakage(validate_catalog_payload(leaked_catalog), clean)

    def test_metrics_report_recall_mrr_top1_and_hard_negative_accuracy(self):
        queries = [
            RetrievalQueryV1.model_validate(query_payload()),
            RetrievalQueryV1.model_validate(query_payload(
                query_id="m03:dev:amenity=restaurant:zh",
                language="zh",
                query_text="哪里可以吃一顿完整的正餐？",
                target_tag="amenity=restaurant",
                excluded_tags=["amenity=cafe"],
            )),
        ]
        rankings = {
            queries[0].query_id: ["amenity=restaurant", "amenity=cafe"],
            queries[1].query_id: ["amenity=restaurant", "amenity=cafe"],
        }
        result = compute_retrieval_metrics(queries, rankings)
        self.assertEqual(result.recall_at_1, 0.5)
        self.assertEqual(result.recall_at_5, 1.0)
        self.assertEqual(result.mrr, 0.75)
        self.assertEqual(result.target_top1_accuracy, 0.5)
        self.assertEqual(result.hard_negative_accuracy, 0.5)
        self.assertIn("en", result.by_language)
        self.assertIn("food", result.by_category)

    def test_public_eval_receipt_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="m03_eval_") as temp:
            path = Path(temp) / "public_eval_receipt.json"
            payload = {"experiment_id": "m03-phase1-v1", "query_count": 100}
            write_public_evaluation_receipt(path, payload)
            first = path.read_bytes()
            with self.assertRaises(PublicEvaluationAlreadyRunError):
                write_public_evaluation_receipt(path, payload)
            self.assertEqual(path.read_bytes(), first)


if __name__ == "__main__":
    unittest.main()