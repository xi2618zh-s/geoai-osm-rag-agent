# src/step3_build_faiss.py
from src.config import EMBEDDING_MODEL, WIKI_RAW_DIR, FAISS_INDEX, FAISS_META
from src.rag.index_builder import build_faiss_index

def main():
    wiki_raw = WIKI_RAW_DIR / "wiki_raw.jsonl"
    build_faiss_index(
        wiki_raw_jsonl=wiki_raw,
        index_path=FAISS_INDEX,
        metadata_path=FAISS_META,
        embedding_model_name=EMBEDDING_MODEL,
        chunk_size=1200,
        overlap=150,
    )

if __name__ == "__main__":
    main()
