"""Dependency-free deterministic Latin and CJK tokenization for M03 BM25."""

from __future__ import annotations

import re
import unicodedata


CJK_TOKENIZER_VERSION = "m03-cjk-unigram-bigram-v1"
SEGMENT_RE = re.compile(r"[a-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)


def _stem_latin(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize_bilingual(text: str) -> list[str]:
    """Tokenize NFKC text, preserving Latin tokens and Han uni/bi-grams."""

    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    normalized = normalized.replace("_", " ").replace("=", " ")
    tokens: list[str] = []
    for match in SEGMENT_RE.finditer(normalized):
        segment = match.group(0)
        if segment[0].isascii():
            tokens.append(_stem_latin(segment))
            continue
        characters = list(segment)
        tokens.extend(characters)
        tokens.extend(
            characters[index] + characters[index + 1]
            for index in range(len(characters) - 1)
        )
    return tokens