# src/rag/index_builder.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import List, Dict

import numpy as np
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

import faiss

from src.config import EMBEDDING_MODEL
from src.path_compat import write_faiss_index


@dataclass
class Chunk:
    page_content: str
    url: str
    title: str
    key: str | None = None
    value: str | None = None


def _infer_key_value_from_url(url: str) -> tuple[str | None, str | None]:
    # e.g. https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dcafe
    m = re.search(r"Tag:([^%]+)%3D(.+)$", url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def clean_wiki_text(text: str) -> str:
    """
    Remove obvious navigation/language noise and normalize whitespace.
    Keep the "Description / How to map / ..." parts.
    """
    # normalize newlines
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # drop super noisy lines
    drop_patterns = [
        r"^Jump to navigation$",
        r"^Jump to search$",
        r"^From OpenStreetMap Wiki$",
        r"^In other languages$",
        r"^Other languages\.\.\.$",
        r"^Contents$",
        r"^Tools for this tag$",
        r"^More details at tag$",
    ]

    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if any(re.match(pat, s) for pat in drop_patterns):
            continue
        # remove the huge language menu block heuristically
        if len(s) <= 2:
            continue
        lines.append(s)

    # collapse repeated spaces
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)

    # optionally truncate extremely long pages (MVP safety)
    return cleaned


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> List[str]:
    """
    Simple character-based chunking with overlap.
    For wiki pages this works fine for MVP.
    """
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be > overlap")

    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end].strip()
        if len(chunk) >= 200:  # ignore tiny junk
            chunks.append(chunk)
        if end == n:
            break
        start = end - overlap
    return chunks


def load_wiki_raw_jsonl(path: Path) -> List[Dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def build_faiss_index(
    wiki_raw_jsonl: Path,
    index_path: Path,
    metadata_path: Path,
    embedding_model_name: str = EMBEDDING_MODEL,
    chunk_size: int = 1200,
    overlap: int = 150,
) -> None:
    rows = load_wiki_raw_jsonl(wiki_raw_jsonl)

    all_chunks: List[Chunk] = []
    for r in rows:
        url = r.get("url", "")
        title = r.get("title", "")
        raw_text = r.get("text", "")
        key, value = _infer_key_value_from_url(url)

        cleaned = clean_wiki_text(raw_text)
        pieces = chunk_text(cleaned, chunk_size=chunk_size, overlap=overlap)

        for p in pieces:
            all_chunks.append(Chunk(page_content=p, url=url, title=title, key=key, value=value))

    if not all_chunks:
        raise RuntimeError("No chunks generated. Check scraping output and cleaning rules.")

    print(f"Total chunks: {len(all_chunks)}")

    model = SentenceTransformer(embedding_model_name)
    texts = [c.page_content for c in all_chunks]

    # Embed
    emb_list = []
    for t in tqdm(texts, desc="Embedding"):
        v = model.encode(t, normalize_embeddings=True)
        emb_list.append(v.astype(np.float32))
    embeddings = np.vstack(emb_list).astype(np.float32)

    # Build FAISS (cosine similarity via inner product on normalized vectors)
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)

    # Save
    index_path.parent.mkdir(parents=True, exist_ok=True)
    write_faiss_index(index, index_path)

    metadata = [
        {
            "page_content": c.page_content,
            "url": c.url,
            "title": c.title,
            "key": c.key,
            "value": c.value,
            "embedding_model": embedding_model_name,
            "chunk_size": chunk_size,
            "overlap": overlap,
        }
        for c in all_chunks
    ]
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"Saved FAISS index: {index_path}")
    print(f"Saved metadata:   {metadata_path}")
