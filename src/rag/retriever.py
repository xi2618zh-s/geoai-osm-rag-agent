# src/rag/retriever.py
"""
RAG retriever: semantic search with FAISS
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import List, Dict, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import EMBEDDING_MODEL, FAISS_INDEX, FAISS_META, RAG_MIN_SCORE
from src.path_compat import read_faiss_index


@dataclass
class RetrievedChunk:
    """A retrieved document chunk"""
    score: float          # similarity score
    page_content: str     # document content
    url: str              # source URL
    title: str            # page title
    key: str | None       # OSM tag key (e.g. "amenity")
    value: str | None     # OSM tag value (e.g. "cafe")
    chunk_id: int | None = None
    dense_score: float | None = None
    lexical_score: float | None = None
    fusion_score: float | None = None
    retrieval_method: str = "dense"


class FaissRetriever:
    """
    FAISS vector retriever
    Retrieves the document chunks most similar to a query from a prebuilt index
    """
    
    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        index_path=FAISS_INDEX,
        meta_path=FAISS_META,
        *,
        local_files_only: bool = False,
    ):
        """
        Initialize the retriever
        
        Args:
            model_name: Sentence Transformer model name
            index_path: path to the FAISS index file
            meta_path: path to the metadata JSON file
        """
        print(f"[Retriever] Loading model: {model_name}")
        self.model = SentenceTransformer(
            model_name,
            local_files_only=local_files_only,
        )
        
        print(f"[Retriever] Loading FAISS index: {index_path}")
        self.index = read_faiss_index(index_path)
        
        print(f"[Retriever] Loading metadata: {meta_path}")
        with open(meta_path, "r", encoding="utf-8") as f:
            self.meta: List[Dict] = json.load(f)

        if self.index.ntotal != len(self.meta):
            raise RuntimeError(
                "FAISS index/metadata mismatch: "
                f"index has {self.index.ntotal} vectors, metadata has {len(self.meta)} rows"
            )

        expected_dim = self.model.get_sentence_embedding_dimension()
        if self.index.d != expected_dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: index={self.index.d}, model={expected_dim}"
            )
        
        print(f"[Retriever] Loaded {len(self.meta)} chunks")

    def retrieve(self, query: str, k: int = 5) -> List[RetrievedChunk]:
        """
        Retrieve the k document chunks most similar to the query
        
        Args:
            query: user query text
            k: number of results to return
        
        Returns:
            List of RetrievedChunk, sorted by descending similarity
        """
        # Encode the query as a vector
        query_vector = self.model.encode(
            query,
            normalize_embeddings=True
        ).astype(np.float32)[None, :]
        
        # FAISS search
        distances, indices = self.index.search(query_vector, k)
        
        # Build the results
        results: List[RetrievedChunk] = []
        for rank, idx in enumerate(indices[0]):
            if idx < 0 or idx >= len(self.meta):
                continue
            
            meta = self.meta[int(idx)]
            results.append(
                RetrievedChunk(
                    score=float(distances[0][rank]),
                    page_content=meta.get("page_content", ""),
                    url=meta.get("url", ""),
                    title=meta.get("title", ""),
                    key=meta.get("key"),
                    value=meta.get("value"),
                    chunk_id=int(idx),
                    dense_score=float(distances[0][rank]),
                    retrieval_method="dense",
                )
            )
        
        return results


def pick_tag_from_chunks(
    chunks: List[RetrievedChunk], min_score: float = RAG_MIN_SCORE
) -> Tuple[str, str]:
    """
    Vote for the most likely OSM tag among the retrieved chunks
    
    Uses weighted voting with the similarity score as the weight
    
    Args:
        chunks: list of retrieved document chunks
    
    Returns:
        (key, value) tuple
    
    Raises:
        ValueError: if no valid tag is found
    """
    votes: Dict[Tuple[str, str], float] = {}
    
    for chunk in chunks:
        if chunk.key and chunk.value and chunk.score >= min_score:
            tag = (chunk.key, chunk.value)
            votes[tag] = votes.get(tag, 0.0) + max(chunk.score, 0.0)
    
    if not votes:
        raise ValueError(
            "No reliable (key, value) found in retrieved chunks at or above "
            f"the similarity threshold {min_score:.3f}."
        )
    
    # Pick the tag with the highest score
    best_tag = max(votes.items(), key=lambda x: x[1])[0]
    return best_tag[0], best_tag[1]
