"""Download and verify the pinned M03 multilingual embedding model outside the repository."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from huggingface_hub import snapshot_download
from sentence_transformers import SentenceTransformer

from src.knowledge.serialization import canonical_json_bytes
from src.knowledge.tokenizer import CJK_TOKENIZER_VERSION


MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
CRITICAL_FILES = (
    "1_Pooling/config.json",
    "README.md",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--hub-cache-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = args.receipt.resolve()
    if receipt.exists():
        raise SystemExit("OSM_KB_MODEL_RECEIPT_EXISTS: refusing to overwrite model receipt")

    model_dir = args.model_dir.resolve()
    try:
        model_dir.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit("OSM_KB_MODEL_PATH_INVALID: model weights must remain outside the repository")
    model_dir.mkdir(parents=True, exist_ok=True)
    hub_cache_dir = args.hub_cache_dir.resolve()
    try:
        hub_cache_dir.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit("OSM_KB_MODEL_PATH_INVALID: Hub cache must remain outside the repository")
    hub_cache_dir.mkdir(parents=True, exist_ok=True)
    snapshot = Path(
        snapshot_download(
            repo_id=MODEL_ID,
            revision=MODEL_REVISION,
            cache_dir=hub_cache_dir,
            local_dir=model_dir,
            max_workers=1,
            allow_patterns=[
                "1_Pooling/config.json",
                "README.md",
                "*.json",
                "*.model",
                "*.safetensors",
            ],
        )
    )
    records = []
    for relative in CRITICAL_FILES:
        path = snapshot / relative
        if not path.is_file():
            raise RuntimeError(f"OSM_KB_MODEL_FILE_MISSING: {relative}")
        digest, size = hash_file(path)
        records.append({"path": relative, "sha256": digest, "size_bytes": size})

    model = SentenceTransformer(str(snapshot), device="cpu", local_files_only=True)
    dimension = model.get_sentence_embedding_dimension()
    if dimension != 384:
        raise RuntimeError(f"OSM_KB_EMBEDDING_DIMENSION_INVALID: {dimension}")
    tokenizer = getattr(model[0], "tokenizer", None)
    tokenizer_class = tokenizer.__class__.__name__ if tokenizer is not None else None
    tokenizer_vocab_size = len(tokenizer) if tokenizer is not None else None

    payload = {
        "schema_version": "1.0",
        "receipt_id": "m03-multilingual-minilm-l12-v2-e8f8c211",
        "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "model_card_url": f"https://huggingface.co/{MODEL_ID}/blob/{MODEL_REVISION}/README.md",
        "model_api_url": f"https://huggingface.co/api/models/{MODEL_ID}?revision={MODEL_REVISION}",
        "license_id": "Apache-2.0",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0",
        "weights_committed_to_repository": False,
        "offline_rebuild_policy": "Load this exact cached revision with local_files_only=True; never resolve latest silently.",
        "sentence_transformers_version": importlib.metadata.version("sentence-transformers"),
        "transformers_version": importlib.metadata.version("transformers"),
        "huggingface_hub_version": importlib.metadata.version("huggingface-hub"),
        "embedding_dimension": dimension,
        "normalize_embeddings": True,
        "dense_index_type": "IndexFlatIP",
        "model_tokenizer_class": tokenizer_class,
        "model_tokenizer_vocab_size": tokenizer_vocab_size,
        "bm25_tokenizer_version": CJK_TOKENIZER_VERSION,
        "files": records,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_json_bytes(payload))
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())