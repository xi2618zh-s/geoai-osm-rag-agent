"""Deterministic M03 BM25 index construction and retrieval."""

from __future__ import annotations

from collections import Counter
import json
import math
from typing import Sequence

from .contracts import IndexDocumentV1, IndexDocumentV2
from .tokenizer import CJK_TOKENIZER_VERSION, tokenize_bilingual


BM25_INDEX_SCHEMA_VERSION = "1.0"


def build_bm25_index_payload(
    documents: Sequence[IndexDocumentV1],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict:
    if not documents:
        raise ValueError("BM25 document input must not be empty")
    ordered = list(documents)
    ids = [item.document_id for item in ordered]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("BM25 documents must have unique deterministic document_id order")
    tokens = [tokenize_bilingual(item.text) for item in ordered]
    if any(not item for item in tokens):
        raise ValueError("BM25 documents must produce at least one token")
    lengths = [len(item) for item in tokens]
    avgdl = sum(lengths) / len(lengths)
    frequencies: Counter[str] = Counter()
    for item in tokens:
        frequencies.update(set(item))
    size = len(ordered)
    idf = {
        token: math.log(1.0 + (size - count + 0.5) / (count + 0.5))
        for token, count in sorted(frequencies.items())
    }
    return {
        "schema_version": BM25_INDEX_SCHEMA_VERSION,
        "tokenizer_version": CJK_TOKENIZER_VERSION,
        "k1": float(k1),
        "b": float(b),
        "document_count": size,
        "document_ids": ids,
        "canonical_tags": [item.canonical_tag for item in ordered],
        "languages": [item.language.value for item in ordered],
        "tokens": tokens,
        "document_lengths": lengths,
        "average_document_length": avgdl,
        "inverse_document_frequency": idf,
    }


class Bm25IndexV1:
    def __init__(self, payload: dict):
        if payload.get("schema_version") != BM25_INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported BM25 index schema")
        if payload.get("tokenizer_version") != CJK_TOKENIZER_VERSION:
            raise ValueError("BM25 tokenizer version mismatch")
        count = payload.get("document_count")
        fields = ("document_ids", "canonical_tags", "languages", "tokens", "document_lengths")
        if not isinstance(count, int) or count <= 0 or any(len(payload.get(field, [])) != count for field in fields):
            raise ValueError("BM25 index arrays do not match document_count")
        self.payload = payload

    def search_documents(self, query: str, *, k: int = 10) -> list[tuple[int, float]]:
        query_tokens = tokenize_bilingual(query)
        if not query_tokens or k <= 0:
            return []
        scores: list[tuple[int, float]] = []
        k1 = float(self.payload["k1"])
        b = float(self.payload["b"])
        avgdl = float(self.payload["average_document_length"])
        idf = self.payload["inverse_document_frequency"]
        for index, tokens in enumerate(self.payload["tokens"]):
            counts = Counter(tokens)
            length_norm = 1.0 - b + b * len(tokens) / avgdl
            score = 0.0
            for token in query_tokens:
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                score += float(idf.get(token, 0.0)) * (
                    frequency * (k1 + 1.0) / (frequency + k1 * length_norm)
                )
            if score > 0:
                scores.append((index, score))
        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores[:k]

    def search_tags(self, query: str, *, k: int = 10) -> list[tuple[str, float]]:
        document_hits = self.search_documents(
            query, k=max(k * 4, int(self.payload["document_count"]))
        )
        best: dict[str, float] = {}
        for index, score in document_hits:
            tag = self.payload["canonical_tags"][index]
            best[tag] = max(best.get(tag, float("-inf")), score)
        return sorted(best.items(), key=lambda item: (-item[1], item[0]))[:k]

def parse_index_documents(
    data: bytes, *, expected_count: int
) -> list[IndexDocumentV1 | IndexDocumentV2]:
    """Require a homogeneous v1/v2 document release with two languages per tag."""
    if expected_count <= 0 or expected_count % 2:
        raise ValueError("OSM_KB_INDEX_COUNT_MISMATCH")
    documents: list[IndexDocumentV1 | IndexDocumentV2] = []
    versions: set[str] = set()
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        version = payload.get("schema_version")
        if version == "1.0":
            document = IndexDocumentV1.model_validate(payload)
        elif version == "2.0":
            document = IndexDocumentV2.model_validate(payload)
        else:
            raise ValueError("OSM_KB_INDEX_DOCUMENT_SCHEMA_UNSUPPORTED")
        versions.add(version)
        documents.append(document)
    if len(documents) != expected_count or len(versions) != 1:
        raise ValueError("OSM_KB_INDEX_COUNT_OR_SCHEMA_MISMATCH")
    ids = [item.document_id for item in documents]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("OSM_KB_INDEX_DOCUMENT_ORDER_INVALID")
    languages: dict[str, set[str]] = {}
    for document in documents:
        languages.setdefault(document.canonical_tag, set()).add(document.language.value)
    if len(languages) * 2 != expected_count or any(value != {"en", "zh"} for value in languages.values()):
        raise ValueError("OSM_KB_INDEX_DOCUMENT_LANGUAGE_INVALID")
    return documents
