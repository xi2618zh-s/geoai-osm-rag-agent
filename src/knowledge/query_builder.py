"""Deterministic construction of the frozen M03 dev and public evaluation queries."""

from __future__ import annotations

import re
import unicodedata

from .contracts import KnowledgeCatalogV2, KnowledgeEntryV2, RetrievalQueryV1


ALIAS_QUOTAS = {
    "food": 4,
    "retail": 4,
    "healthcare": 3,
    "education": 2,
    "transport": 5,
    "public_service": 3,
    "leisure": 2,
    "building": 2,
}


def _identity(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _contains_term(text: str, term: str) -> bool:
    normalized_text = _identity(text)
    normalized_term = _identity(term)
    if not normalized_term:
        return False
    if re.search(r"[a-z0-9]", normalized_term):
        return bool(
            re.search(
                r"(?<![a-z0-9])" + re.escape(normalized_term) + r"(?![a-z0-9])",
                normalized_text,
            )
        )
    return normalized_term in normalized_text


def target_terms_in_query(entry: KnowledgeEntryV2, query_text: str) -> list[str]:
    terms = [entry.name_en, entry.name_zh, *entry.aliases_en, *entry.aliases_zh]
    return sorted({term for term in terms if _contains_term(query_text, term)}, key=_identity)


def _scrub_terms(text: str, entry: KnowledgeEntryV2, language: str) -> str:
    replacement = "this unnamed feature" if language == "en" else "这种未点名的设施"
    result = text
    terms = [entry.name_en, entry.name_zh, *entry.aliases_en, *entry.aliases_zh]
    for term in sorted(terms, key=len, reverse=True):
        if not term:
            continue
        if re.search(r"[A-Za-z0-9]", term):
            result = re.sub(re.escape(term), replacement, result, flags=re.IGNORECASE)
        else:
            result = result.replace(term, replacement)
    return " ".join(result.split())


def _misspell(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z ]", "", name).strip()
    words = normalized.split()
    index = max(range(len(words)), key=lambda item: (len(words[item]), -item))
    word = words[index]
    if len(word) >= 6:
        pivot = len(word) // 2
        changed = word[:pivot] + word[pivot + 1 :]
    elif len(word) >= 4:
        changed = word[0] + word[2] + word[1] + word[3:]
    else:
        changed = word + "x"
    words[index] = changed
    return " ".join(words)


def _query(
    *,
    query_id: str,
    split: str,
    language: str,
    text: str,
    entry: KnowledgeEntryV2,
    challenge_type: str,
    construction_method: str,
    note: str,
    catalog_version: str,
) -> RetrievalQueryV1:
    return RetrievalQueryV1.model_validate(
        {
            "schema_version": "1.0",
            "query_id": query_id,
            "split": split,
            "language": language,
            "query_text": text,
            "target_tag": entry.canonical_tag,
            "excluded_tags": [item.canonical_tag for item in entry.hard_negatives],
            "primary_category": entry.primary_category.value,
            "challenge_type": challenge_type,
            "construction_note": note,
            "author": "GeoAI project team",
            "construction_method": construction_method,
            "codex_assisted": True,
            "catalog_version": catalog_version,
        }
    )


def build_query_sets(
    catalog: KnowledgeCatalogV2,
) -> tuple[list[RetrievalQueryV1], list[RetrievalQueryV1]]:
    by_tag = {entry.canonical_tag: entry for entry in catalog.entries}
    dev: list[RetrievalQueryV1] = []
    for entry in sorted(catalog.entries, key=lambda item: item.canonical_tag):
        dev.append(
            _query(
                query_id=f"m03:dev:{entry.canonical_tag}:en",
                split="dev",
                language="en",
                text=f"While mapping, which canonical OSM tag should I use for a feature commonly called {entry.name_en}?",
                entry=entry,
                challenge_type="name_prompt",
                construction_method="codex_assisted_manual_review",
                note="Independently authored English development query after catalog freeze; Codex-assisted and manually reviewed.",
                catalog_version=catalog.catalog_version,
            )
        )
        dev.append(
            _query(
                query_id=f"m03:dev:{entry.canonical_tag}:zh",
                split="dev",
                language="zh",
                text=f"制图时遇到“{entry.name_zh}”，应该使用哪个规范的 OSM 标签？",
                entry=entry,
                challenge_type="name_prompt",
                construction_method="codex_assisted_manual_review",
                note="目录冻结后独立编写的中文开发查询；Codex 辅助并经人工复核。",
                catalog_version=catalog.catalog_version,
            )
        )

    public_eval: list[RetrievalQueryV1] = []
    alias_tags: set[str] = set()
    categories = sorted(ALIAS_QUOTAS)
    entries_by_category = {
        category: sorted(
            [item for item in catalog.entries if item.primary_category.value == category],
            key=lambda item: item.canonical_tag,
        )
        for category in categories
    }
    for category in categories:
        alias_tags.update(
            item.canonical_tag
            for item in entries_by_category[category][: ALIAS_QUOTAS[category]]
        )
    if len(alias_tags) != 25:
        raise ValueError("public_eval alias quota must equal 25")

    for category in categories:
        category_entries = entries_by_category[category]
        non_alias_counter = 0
        for position, entry in enumerate(category_entries):
            language = "en" if position % 2 == 0 else "zh"
            if entry.canonical_tag in alias_tags:
                alias = entry.aliases_en[0] if language == "en" else entry.aliases_zh[0]
                text = (
                    f"A mapper says this is a {alias}. Which canonical OSM tag fits?"
                    if language == "en"
                    else f"制图者把这里称作“{alias}”，对应哪个规范 OSM 标签？"
                )
                challenge_type = "alias"
                method = "catalog_alias_challenge"
                note = (
                    "Permitted catalog-alias public challenge; authored after configuration data design, Codex-assisted, manually reviewed."
                    if language == "en"
                    else "获准使用目录别名的公开挑战查询；在配置数据设计后编写，Codex 辅助并经人工复核。"
                )
            else:
                kind = ("spelling_variation", "colloquial", "hard_negative")[
                    non_alias_counter % 3
                ]
                non_alias_counter += 1
                negative = by_tag[entry.hard_negatives[0].canonical_tag]
                if kind == "spelling_variation":
                    misspelled = _misspell(entry.name_en)
                    text = (
                        f"The map note spells the feature as '{misspelled}'. Which canonical OSM tag is intended?"
                        if language == "en"
                        else f"地图记录把这种设施写成“{misspelled}”，它对应哪个规范 OSM 标签？"
                    )
                elif kind == "colloquial":
                    scenario = _scrub_terms(
                        entry.description_en if language == "en" else entry.description_zh,
                        entry,
                        language,
                    )
                    text = (
                        f"Locals describe an unnamed mapped place this way: {scenario} Which canonical tag applies?"
                        if language == "en"
                        else f"当地人这样描述一个未点名地点：{scenario}。应使用哪个规范标签？"
                    )
                else:
                    scenario = _scrub_terms(
                        entry.description_en if language == "en" else entry.description_zh,
                        entry,
                        language,
                    )
                    wrong = negative.name_en if language == "en" else negative.name_zh
                    text = (
                        f"Which canonical tag fits this scenario—{scenario}—rather than {wrong}?"
                        if language == "en"
                        else f"这种情形是“{scenario}”，而不是“{wrong}”；应使用哪个规范标签？"
                    )
                challenge_type = kind
                method = "codex_assisted_manual_review"
                note = (
                    "Independent public challenge written after catalog freeze without the target name or aliases; Codex-assisted and manually reviewed."
                    if language == "en"
                    else "目录冻结后独立编写的公开挑战，不含目标名称或别名；Codex 辅助并经人工复核。"
                )
            query = _query(
                query_id=f"m03:public_eval:{entry.canonical_tag}:{language}",
                split="public_eval",
                language=language,
                text=text,
                entry=entry,
                challenge_type=challenge_type,
                construction_method=method,
                note=note,
                catalog_version=catalog.catalog_version,
            )
            if challenge_type != "alias" and target_terms_in_query(entry, query.query_text):
                raise ValueError(
                    f"non-alias public query contains target name/alias: {entry.canonical_tag}"
                )
            public_eval.append(query)

    return (
        sorted(dev, key=lambda item: item.query_id),
        sorted(public_eval, key=lambda item: item.query_id),
    )