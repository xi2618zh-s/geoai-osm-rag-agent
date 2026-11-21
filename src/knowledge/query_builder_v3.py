"""Deterministic, role-aware Phase 2 bilingual development and public queries."""

from __future__ import annotations

from .contracts import KnowledgeCatalogV3, KnowledgeEntryV3, RetrievalQueryV2
from .query_builder import _scrub_terms, target_terms_in_query


# Reviewed exceptions where mechanical name removal makes the scenario
# ungrammatical or would expose a canonical tag in the question.
SCENARIO_OVERRIDES = {
    ("aeroway=aerodrome", "en"): "A designated ground area supports aircraft arrivals and departures.",
    ("aeroway=airport", "zh"): "旧数据用一条已废弃的航空设施标签；这里只识别历史标签，不建议新增使用。",
    ("amenity=university", "en"): "An institution provides degree-level higher education and research.",
    ("building=house", "en"): "A residential building is designed for one household or family.",
    ("healthcare=dentist", "en"): "A dental care service is mapped separately from the facility tag.",
    ("healthcare=rehabilitation", "en"): "A medical service helps patients recover function, sometimes with residential care.",
    ("leisure=stadium", "zh"): "大型体育设施围绕比赛场地或跑道设置观众席。",
    ("railway=halt", "zh"): "这处铁路乘降设施较小，配套设施少于一般车站。",
    ("wheelchair=limited", "zh"): "轮椅只可到达部分区域，单凭这一条记录无法确定具体范围。",
}


def _query(
    entry: KnowledgeEntryV3, *, split: str, language: str, text: str,
    challenge_type: str, retained: set[str], catalog_version: str,
) -> RetrievalQueryV2:
    method = "catalog_alias_challenge" if challenge_type == "alias" else "codex_assisted_manual_review"
    note = (
        "Catalog-alias public challenge; generated after catalog freeze and reviewed for role, target, and excluded tags."
        if challenge_type == "alias" else
        "Catalog-informed scenario written after catalog freeze; Codex-assisted, checked for target-term and split leakage."
    )
    return RetrievalQueryV2.model_validate({
        "schema_version": "2.0",
        "query_id": f"m03:phase2:{split}:{entry.canonical_tag}:{language}",
        "split": split, "language": language, "query_text": text,
        "target_tag": entry.canonical_tag,
        "excluded_tags": [item.canonical_tag for item in entry.hard_negatives],
        "primary_category": entry.primary_category.value,
        "tag_role": entry.tag_role.value,
        "target_cohort": "phase1_retained" if entry.canonical_tag in retained else "phase2_new",
        "challenge_type": challenge_type,
        "construction_note": note,
        "author": "GeoAI project team",
        "construction_method": method,
        "codex_assisted": True,
        "catalog_version": catalog_version,
    })


def _dev_text(entry: KnowledgeEntryV3, language: str) -> str:
    role = entry.tag_role.value
    if language == "en":
        if role == "attribute":
            return f"For an existing mapped object, which additional OSM tag records {entry.name_en}?"
        if role == "land_use":
            return f"Which canonical OSM land-use tag describes an area known as {entry.name_en}?"
        return f"While mapping, which canonical OSM tag should I use for a place or object called {entry.name_en}?"
    if role == "attribute":
        return f"已有地图对象需要补充“{entry.name_zh}”条件，应该使用哪个 OSM 附加标签？"
    if role == "land_use":
        return f"一片区域被称为“{entry.name_zh}”，应选择哪个规范 OSM 用地标签？"
    return f"制图时遇到“{entry.name_zh}”，应使用哪个规范 OSM 标签？"


def _public_text(entry: KnowledgeEntryV3, negative: KnowledgeEntryV3, language: str, kind: str) -> str:
    role = entry.tag_role.value
    if kind == "alias":
        alias = entry.aliases_en[0] if language == "en" else entry.aliases_zh[0]
        if language == "en":
            return (f"A mapper calls this condition {alias}. Which additional tag belongs on the existing object?"
                    if role == "attribute" else f"Local map notes call this {alias}. Which canonical OSM tag fits?")
        return (f"制图者把现有对象的条件称为“{alias}”，应补充哪个规范标签？"
                if role == "attribute" else f"当地记录把这里称作“{alias}”，对应哪个规范 OSM 标签？")
    scenario = SCENARIO_OVERRIDES.get((entry.canonical_tag, language))
    if scenario is None:
        scenario = _scrub_terms(
            entry.description_en if language == "en" else entry.description_zh,
            entry, language,
        )
    scenario = scenario.rstrip("。.! ")
    if language == "en":
        object_word = "additional attribute" if role == "attribute" else ("land-use type" if role == "land_use" else "feature tag")
        if kind == "hard_negative":
            text = f"A mapper observes: {scenario}. Which {object_word} fits rather than {negative.name_en}?"
        else:
            text = f"A map survey describes this situation: {scenario}. Which canonical OSM {object_word} applies?"
    else:
        object_word = "附加属性" if role == "attribute" else ("用地类型" if role == "land_use" else "对象标签")
        if kind == "hard_negative":
            text = f"实地记录指出：{scenario}。应选哪个规范{object_word}，而不是“{negative.name_zh}”？"
        else:
            text = f"实地调查这样描述一个未点名对象：{scenario}。应选哪个规范 OSM {object_word}？"
    return _scrub_terms(text, entry, language) if target_terms_in_query(entry, text) else text


def build_query_sets_v3(
    catalog: KnowledgeCatalogV3, retained_tags: set[str]
) -> tuple[list[RetrievalQueryV2], list[RetrievalQueryV2]]:
    entries = sorted(catalog.entries, key=lambda item: item.canonical_tag)
    by_tag = {entry.canonical_tag: entry for entry in entries}
    if len(entries) < 200 or len(retained_tags) != 100 or not retained_tags <= set(by_tag):
        raise ValueError("M03_PHASE2_QUERY_INPUT_COUNT_INVALID")
    dev = [
        _query(entry, split="dev", language=language, text=_dev_text(entry, language),
               challenge_type="name_prompt", retained=retained_tags,
               catalog_version=catalog.catalog_version)
        for entry in entries for language in ("en", "zh")
    ]
    public = []
    for index, entry in enumerate(entries):
        language = "en" if index % 2 else "zh"
        kind = "alias" if index % 4 == 0 else ("hard_negative" if index % 4 == 2 else "colloquial")
        negative = by_tag[entry.hard_negatives[0].canonical_tag]
        text = _public_text(entry, negative, language, kind)
        if "this unnamed feature" in text or "这种未点名的设施" in text or "。。" in text:
            raise ValueError("M03_PHASE2_PUBLIC_QUERY_LANGUAGE_REVIEW_REQUIRED: " + entry.canonical_tag)
        if kind != "alias" and target_terms_in_query(entry, text):
            raise ValueError("M03_PHASE2_PUBLIC_TARGET_TERM_LEAK: " + entry.canonical_tag)
        public.append(_query(entry, split="public_eval", language=language, text=text,
                             challenge_type=kind, retained=retained_tags,
                             catalog_version=catalog.catalog_version))
    return sorted(dev, key=lambda item: item.query_id), sorted(public, key=lambda item: item.query_id)
