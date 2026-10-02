"""Explainable tag-level BM25 + dense retrieval and rank fusion."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
import math
from pathlib import Path
import re
from typing import Iterable

from src.config import (
    HYBRID_CANDIDATE_K,
    HYBRID_DENSE_WEIGHT,
    HYBRID_LEXICAL_WEIGHT,
    HYBRID_NEGATIVE_WEIGHT,
    HYBRID_RRF_K,
    RETRIEVAL_MODE,
    TAG_CATALOG,
)
from src.rag.retriever import FaissRetriever, RetrievedChunk


TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
NEGATION_RE = re.compile(
    r"\b(?:not|rather\s+than|excluding|exclude|instead\s+of)\b",
    re.IGNORECASE,
)


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    normalized = str(text).casefold().replace("_", " ").replace("=", " ")
    return [_stem(token) for token in TOKEN_RE.findall(normalized)]


def split_query_polarity(query: str) -> tuple[str, str]:
    """Split common contrastive wording into wanted and excluded clauses."""

    match = NEGATION_RE.search(str(query))
    if not match:
        return str(query), ""
    positive = str(query)[: match.start()].strip(" ,;:-")
    negative = str(query)[match.end() :].strip(" ,;:-")
    return positive or str(query), negative


def _tag(hit: RetrievedChunk) -> tuple[str, str] | None:
    if hit.key and hit.value:
        return str(hit.key).strip(), str(hit.value).strip()
    return None


class BM25Index:
    """Small in-memory BM25 index over wiki chunks plus catalog entries."""

    def __init__(
        self,
        documents: list[RetrievedChunk],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        if not documents:
            raise ValueError("BM25 corpus must not be empty")
        self.documents = documents
        self.k1 = float(k1)
        self.b = float(b)
        self.tokens = [tokenize(self._search_text(item)) for item in documents]
        self.lengths = [len(tokens) for tokens in self.tokens]
        self.avgdl = sum(self.lengths) / len(self.lengths)
        frequencies: Counter[str] = Counter()
        for tokens in self.tokens:
            frequencies.update(set(tokens))
        size = len(documents)
        self.idf = {
            token: math.log(1.0 + (size - count + 0.5) / (count + 0.5))
            for token, count in frequencies.items()
        }

    @staticmethod
    def _search_text(item: RetrievedChunk) -> str:
        return " ".join(
            part
            for part in (
                item.key or "",
                item.value or "",
                f"{item.key}={item.value}" if item.key and item.value else "",
                item.title,
                item.page_content,
            )
            if part
        )

    def search(self, query: str, k: int | None = None) -> list[RetrievedChunk]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores: list[tuple[float, int]] = []
        for index, tokens in enumerate(self.tokens):
            counts = Counter(tokens)
            length_norm = 1.0 - self.b + self.b * self.lengths[index] / self.avgdl
            score = 0.0
            for token in query_tokens:
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                numerator = frequency * (self.k1 + 1.0)
                denominator = frequency + self.k1 * length_norm
                score += self.idf.get(token, 0.0) * numerator / denominator
            if score > 0:
                scores.append((score, index))
        scores.sort(key=lambda item: (-item[0], item[1]))
        selected = scores if k is None else scores[:k]
        return [
            replace(
                self.documents[index],
                score=float(score),
                lexical_score=float(score),
                retrieval_method="bm25",
            )
            for score, index in selected
        ]


def _metadata_documents(metadata: Iterable[dict]) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            score=0.0,
            page_content=str(row.get("page_content", "")),
            url=str(row.get("url", "")),
            title=str(row.get("title", "")),
            key=row.get("key"),
            value=row.get("value"),
            chunk_id=index,
            retrieval_method="bm25",
        )
        for index, row in enumerate(metadata)
    ]


def load_catalog_documents(path: Path = TAG_CATALOG) -> list[RetrievedChunk]:
    with Path(path).open("r", encoding="utf-8") as stream:
        catalog = json.load(stream)
    entries = catalog.get("entries")
    if catalog.get("schema_version") != 1 or not isinstance(entries, list):
        raise ValueError(f"Unsupported tag catalog schema: {path}")
    documents: list[RetrievedChunk] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        key = str(entry.get("key", "")).strip()
        value = str(entry.get("value", "")).strip()
        if not key or not value or (key, value) in seen:
            raise ValueError(
                f"Invalid or duplicate catalog tag at entry {index}: {key}={value}"
            )
        seen.add((key, value))
        aliases = " ".join(str(alias) for alias in entry.get("aliases", []))
        content = (
            f"OSM tag {key}={value}. Status: {entry.get('status', 'unknown')}. "
            f"Aliases: {aliases}. {entry.get('description', '')}"
        )
        documents.append(
            RetrievedChunk(
                score=0.0,
                page_content=content,
                url=str(entry.get("url", "")),
                title=f"Catalog: {key}={value}",
                key=key,
                value=value,
                chunk_id=-(index + 1),
                retrieval_method="catalog_bm25",
            )
        )
    return documents


def _unique_tag_ranks(
    hits: Iterable[RetrievedChunk],
) -> tuple[
    dict[tuple[str, str], int],
    dict[tuple[str, str], RetrievedChunk],
]:
    ranks: dict[tuple[str, str], int] = {}
    representatives: dict[tuple[str, str], RetrievedChunk] = {}
    for hit in hits:
        tag = _tag(hit)
        if tag is None or tag in ranks:
            continue
        ranks[tag] = len(ranks) + 1
        representatives[tag] = hit
    return ranks, representatives


def _query_tag_match(query: str, tag: tuple[str, str]) -> float:
    tokens = set(tokenize(query))
    key_tokens = set(tokenize(tag[0]))
    value_tokens = set(tokenize(tag[1]))
    value_match = bool(value_tokens) and value_tokens.issubset(tokens)
    key_match = bool(key_tokens) and key_tokens.issubset(tokens)
    if key_match and value_match:
        return 1.0
    if value_match:
        return 0.35
    return 0.0


class HybridRetriever:
    """Fuse dense and lexical rankings at OSM tag granularity."""

    def __init__(
        self,
        dense_retriever: FaissRetriever | None = None,
        *,
        catalog_path: Path = TAG_CATALOG,
        dense_weight: float = HYBRID_DENSE_WEIGHT,
        lexical_weight: float = HYBRID_LEXICAL_WEIGHT,
        rrf_k: int = HYBRID_RRF_K,
        candidate_k: int = HYBRID_CANDIDATE_K,
        tag_match_weight: float = 0.15,
        negative_weight: float = HYBRID_NEGATIVE_WEIGHT,
        local_files_only: bool = False,
    ):
        self.dense = dense_retriever or FaissRetriever(
            local_files_only=local_files_only
        )
        documents = _metadata_documents(self.dense.meta) + load_catalog_documents(
            catalog_path
        )
        self.lexical = BM25Index(documents)
        self.dense_weight = float(dense_weight)
        self.lexical_weight = float(lexical_weight)
        self.rrf_k = int(rrf_k)
        self.candidate_k = max(1, int(candidate_k))
        self.tag_match_weight = max(0.0, float(tag_match_weight))
        self.negative_weight = max(0.0, float(negative_weight))

    def retrieve(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        dense_hits = self.dense.retrieve(
            query, k=min(self.candidate_k, len(self.dense.meta))
        )
        positive_query, negative_query = split_query_polarity(query)
        lexical_hits = self.lexical.search(positive_query)
        negative_hits = self.lexical.search(negative_query) if negative_query else []
        dense_ranks, dense_representatives = _unique_tag_ranks(dense_hits)
        lexical_ranks, lexical_representatives = _unique_tag_ranks(lexical_hits)
        negative_ranks, _negative_representatives = _unique_tag_ranks(negative_hits)
        tags = set(dense_ranks) | set(lexical_ranks)
        scored: list[tuple[float, tuple[str, str], RetrievedChunk]] = []
        for tag in tags:
            dense_component = (
                self.dense_weight / (self.rrf_k + dense_ranks[tag])
                if tag in dense_ranks
                else 0.0
            )
            lexical_component = (
                self.lexical_weight / (self.rrf_k + lexical_ranks[tag])
                if tag in lexical_ranks
                else 0.0
            )
            negative_component = (
                self.negative_weight / (self.rrf_k + negative_ranks[tag])
                if tag in negative_ranks
                else 0.0
            )
            match_component = self.tag_match_weight * _query_tag_match(
                positive_query, tag
            )
            fusion = (
                dense_component
                + lexical_component
                + match_component
                - negative_component
            )
            dense_hit = dense_representatives.get(tag)
            lexical_hit = lexical_representatives.get(tag)
            representative = (
                lexical_hit
                if lexical_hit is not None and lexical_component > dense_component
                else dense_hit or lexical_hit
            )
            if representative is not None:
                scored.append((fusion, tag, representative))
        scored.sort(key=lambda item: (-item[0], item[1]))
        maximum = scored[0][0] if scored else 1.0
        results: list[RetrievedChunk] = []
        for fusion, tag, representative in scored[: max(0, k)]:
            dense_hit = dense_representatives.get(tag)
            lexical_hit = lexical_representatives.get(tag)
            results.append(
                replace(
                    representative,
                    score=max(0.0, float(fusion / maximum)),
                    dense_score=dense_hit.score if dense_hit else None,
                    lexical_score=(
                        lexical_hit.lexical_score if lexical_hit else None
                    ),
                    fusion_score=float(fusion),
                    retrieval_method="hybrid_rrf",
                )
            )
        return results


class BM25Retriever:
    """Tag-deduplicated lexical-only retriever used for ablation."""

    def __init__(self, metadata: list[dict], catalog_path: Path = TAG_CATALOG):
        self.index = BM25Index(
            _metadata_documents(metadata) + load_catalog_documents(catalog_path)
        )

    def retrieve(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        positive_query, negative_query = split_query_polarity(query)
        hits = self.index.search(positive_query)
        _ranks, representatives = _unique_tag_ranks(hits)
        negative_hits = self.index.search(negative_query) if negative_query else []
        _negative_ranks, negative_representatives = _unique_tag_ranks(negative_hits)
        negative_scores = {
            tag: item.score for tag, item in negative_representatives.items()
        }
        maximum_positive = max(
            (item.score for item in representatives.values()), default=1.0
        )
        maximum_negative = max(negative_scores.values(), default=1.0)
        scored: list[tuple[float, tuple[str, str], RetrievedChunk]] = []
        for tag, item in representatives.items():
            positive_score = item.score / maximum_positive
            negative_score = negative_scores.get(tag, 0.0) / maximum_negative
            exact_bonus = 0.75 * _query_tag_match(positive_query, tag)
            score = positive_score + exact_bonus - 0.5 * negative_score
            scored.append((score, tag, item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        maximum = scored[0][0] if scored else 1.0
        return [
            replace(item, score=max(0.0, score / maximum))
            for score, _tag_value, item in scored[: max(0, k)]
        ]


def build_retriever(
    mode: str = RETRIEVAL_MODE,
    *,
    local_files_only: bool = False,
):
    normalized = str(mode).strip().lower()
    dense = FaissRetriever(local_files_only=local_files_only)
    if normalized == "dense":
        return dense
    if normalized == "bm25":
        return BM25Retriever(dense.meta)
    if normalized == "hybrid":
        return HybridRetriever(dense_retriever=dense)
    raise ValueError(
        f"Unsupported retrieval mode: {mode!r}; use dense, bm25, or hybrid"
    )
