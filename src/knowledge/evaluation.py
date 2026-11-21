"""Leakage audit, immutable receipts, and retrieval metrics for M03."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
import unicodedata
from typing import Iterable, Mapping, Sequence

from .contracts import KnowledgeCatalogV2, KnowledgeCatalogV3, RetrievalQueryV1, RetrievalQueryV2
from .serialization import canonical_json_bytes


class EvaluationLeakageError(ValueError):
    pass


class PublicEvaluationAlreadyRunError(FileExistsError):
    pass


@dataclass(frozen=True)
class LeakageReport:
    leak_count: int
    checked_queries: int
    checked_fields: int


@dataclass(frozen=True)
class RetrievalMetrics:
    query_count: int
    recall_at_1: float
    recall_at_5: float
    mrr: float
    target_top1_accuracy: float
    hard_negative_accuracy: float
    by_language: dict[str, dict[str, float]]
    by_category: dict[str, dict[str, float]]


def _identity(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _catalog_text_fields(catalog: KnowledgeCatalogV2) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for entry in catalog.entries:
        for field in ("name_en", "name_zh", "description_en", "description_zh"):
            values.append((f"{entry.canonical_tag}.{field}", getattr(entry, field)))
        for field in ("aliases_en", "aliases_zh"):
            for index, value in enumerate(getattr(entry, field)):
                values.append((f"{entry.canonical_tag}.{field}[{index}]", value))
        for index, negative in enumerate(entry.hard_negatives):
            values.append((f"{entry.canonical_tag}.hard_negatives[{index}].reason_en", negative.reason_en))
            values.append((f"{entry.canonical_tag}.hard_negatives[{index}].reason_zh", negative.reason_zh))
    return values


def audit_query_leakage(
    catalog: KnowledgeCatalogV2,
    queries: Sequence[RetrievalQueryV1],
) -> LeakageReport:
    fields = _catalog_text_fields(catalog)
    leaks: list[str] = []
    for query in queries:
        query_text = _identity(query.query_text)
        if not query_text:
            continue
        for location, value in fields:
            field_text = _identity(value)
            if query_text == field_text or (len(query_text) >= 16 and query_text in field_text):
                leaks.append(f"{query.query_id} -> {location}")
    if leaks:
        raise EvaluationLeakageError("benchmark query leakage detected: " + "; ".join(leaks))
    return LeakageReport(
        leak_count=0,
        checked_queries=len(queries),
        checked_fields=len(fields),
    )


def _aggregate(
    queries: Sequence[RetrievalQueryV1],
    rankings: Mapping[str, Sequence[str]],
) -> dict[str, float]:
    if not queries:
        return {
            "query_count": 0.0,
            "recall_at_1": 0.0,
            "recall_at_5": 0.0,
            "mrr": 0.0,
            "target_top1_accuracy": 0.0,
            "hard_negative_accuracy": 0.0,
        }
    reciprocal = 0.0
    recall_one = 0
    recall_five = 0
    top_one = 0
    hard_correct = 0
    for query in queries:
        ranked = list(rankings.get(query.query_id, []))
        try:
            target_rank = ranked.index(query.target_tag) + 1
        except ValueError:
            target_rank = None
        if target_rank == 1:
            recall_one += 1
            top_one += 1
        if target_rank is not None and target_rank <= 5:
            recall_five += 1
        if target_rank is not None:
            reciprocal += 1.0 / target_rank
        excluded_ranks = []
        for excluded in query.excluded_tags:
            try:
                excluded_ranks.append(ranked.index(excluded) + 1)
            except ValueError:
                excluded_ranks.append(float("inf"))
        if target_rank is not None and all(target_rank < rank for rank in excluded_ranks):
            hard_correct += 1
    count = len(queries)
    return {
        "query_count": float(count),
        "recall_at_1": recall_one / count,
        "recall_at_5": recall_five / count,
        "mrr": reciprocal / count,
        "target_top1_accuracy": top_one / count,
        "hard_negative_accuracy": hard_correct / count,
    }


def compute_retrieval_metrics(
    queries: Sequence[RetrievalQueryV1],
    rankings: Mapping[str, Sequence[str]],
) -> RetrievalMetrics:
    overall = _aggregate(queries, rankings)
    by_language = {
        language: _aggregate([query for query in queries if query.language.value == language], rankings)
        for language in sorted({query.language.value for query in queries})
    }
    by_category = {
        category: _aggregate(
            [query for query in queries if query.primary_category.value == category], rankings
        )
        for category in sorted({query.primary_category.value for query in queries})
    }
    return RetrievalMetrics(
        query_count=len(queries),
        recall_at_1=overall["recall_at_1"],
        recall_at_5=overall["recall_at_5"],
        mrr=overall["mrr"],
        target_top1_accuracy=overall["target_top1_accuracy"],
        hard_negative_accuracy=overall["hard_negative_accuracy"],
        by_language=by_language,
        by_category=by_category,
    )


def write_public_evaluation_receipt(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as error:
        raise PublicEvaluationAlreadyRunError(
            "public_eval already has an immutable receipt; use a new experiment version"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

class Phase2QuerySetError(ValueError):
    """A Phase 2 query release fails coverage, provenance, or leakage checks."""


def validate_phase2_query_sets(
    catalog: KnowledgeCatalogV3,
    dev: Sequence[RetrievalQueryV2],
    public_eval: Sequence[RetrievalQueryV2],
    retained_tags: set[str],
    *,
    minimum_total: int = 300,
) -> dict[str, int]:
    """Require two bilingual dev queries and one independent public query per tag."""
    from .query_builder import target_terms_in_query

    entries = {entry.canonical_tag: entry for entry in catalog.entries}
    tags = set(entries)
    if not tags or not retained_tags <= tags or len(dev) + len(public_eval) < minimum_total:
        raise Phase2QuerySetError("M03_PHASE2_QUERY_QUOTA_INVALID")
    if len(dev) != 2 * len(tags) or len(public_eval) != len(tags):
        raise Phase2QuerySetError("M03_PHASE2_QUERY_QUOTA_INVALID")
    if len({q.query_id for q in [*dev, *public_eval]}) != len(dev) + len(public_eval):
        raise Phase2QuerySetError("M03_PHASE2_QUERY_ID_DUPLICATE")
    dev_pairs = {(q.target_tag, q.language.value) for q in dev}
    if dev_pairs != {(tag, language) for tag in tags for language in ("en", "zh")} or len(dev_pairs) != len(dev):
        raise Phase2QuerySetError("M03_PHASE2_DEV_COVERAGE_INVALID")
    public_targets = {q.target_tag for q in public_eval}
    if public_targets != tags or len(public_targets) != len(public_eval):
        raise Phase2QuerySetError("M03_PHASE2_PUBLIC_COVERAGE_INVALID")
    if {q.language.value for q in public_eval} != {"en", "zh"}:
        raise Phase2QuerySetError("M03_PHASE2_PUBLIC_LANGUAGE_INVALID")
    dev_texts = {_identity(q.query_text) for q in dev}
    if any(_identity(q.query_text) in dev_texts for q in public_eval):
        raise Phase2QuerySetError("M03_PHASE2_SPLIT_TEXT_OVERLAP")
    for split, queries in (("dev", dev), ("public_eval", public_eval)):
        for query in queries:
            entry = entries.get(query.target_tag)
            if entry is None or query.split.value != split:
                raise Phase2QuerySetError("M03_PHASE2_QUERY_TARGET_INVALID")
            expected_cohort = "phase1_retained" if query.target_tag in retained_tags else "phase2_new"
            if (query.target_cohort != expected_cohort
                or query.primary_category != entry.primary_category
                or query.tag_role != entry.tag_role
                or query.catalog_version != catalog.catalog_version):
                raise Phase2QuerySetError("M03_PHASE2_QUERY_METADATA_MISMATCH")
            negatives = {item.canonical_tag for item in entry.hard_negatives}
            if not query.excluded_tags or not set(query.excluded_tags) <= negatives:
                raise Phase2QuerySetError("M03_PHASE2_QUERY_NEGATIVE_INVALID")
            if split == "public_eval" and query.challenge_type != "alias":
                if target_terms_in_query(entry, query.query_text):
                    raise Phase2QuerySetError("M03_PHASE2_PUBLIC_TARGET_TERM_LEAK")
    try:
        audit_query_leakage(catalog, dev)
        audit_query_leakage(catalog, public_eval)
    except EvaluationLeakageError as error:
        raise Phase2QuerySetError("M03_PHASE2_QUERY_CATALOG_LEAK") from error
    return {
        "dev_count": len(dev),
        "public_eval_count": len(public_eval),
        "target_count": len(tags),
        "phase1_retained_target_count": len(retained_tags),
        "phase2_new_target_count": len(tags - retained_tags),
    }


def compute_phase2_cohort_metrics(
    queries: Sequence[RetrievalQueryV2],
    rankings: Mapping[str, Sequence[str]],
) -> dict[str, RetrievalMetrics]:
    return {
        cohort: compute_retrieval_metrics(
            [query for query in queries if query.target_cohort == cohort], rankings
        )
        for cohort in ("phase1_retained", "phase2_new")
    }


def parse_query_jsonl(data: bytes) -> list[RetrievalQueryV1 | RetrievalQueryV2]:
    """Parse one homogeneous strict query contract; never widen v1 records silently."""
    parsed: list[RetrievalQueryV1 | RetrievalQueryV2] = []
    versions: set[str] = set()
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        version = payload.get("schema_version")
        if version == "1.0":
            query = RetrievalQueryV1.model_validate(payload)
        elif version == "2.0":
            query = RetrievalQueryV2.model_validate(payload)
        else:
            raise Phase2QuerySetError("M03_QUERY_SCHEMA_UNSUPPORTED")
        parsed.append(query)
        versions.add(version)
    if not parsed or len(versions) != 1:
        raise Phase2QuerySetError("M03_QUERY_SCHEMA_MIXED_OR_EMPTY")
    return parsed
