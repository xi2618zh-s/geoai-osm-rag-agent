"""Central configuration for the GeoAI pipeline.

Every setting has a safe project-relative default and can be overridden with an
environment variable.  This keeps the demo easy to run while allowing a real
deployment to move data, models, and services without editing source code.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _path_setting(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


def _float_setting(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


def _int_setting(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


def _bool_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


DATA_DIR = _path_setting("GEOAI_DATA_DIR", ROOT / "data")
_LATEST_OSM_PBF = DATA_DIR / "osm" / "sweden-latest.osm.pbf"
_LEGACY_OSM_PBF = DATA_DIR / "osm" / "sweden-251214.osm.pbf"
_DEFAULT_OSM_PBF = (
    _LEGACY_OSM_PBF
    if _LEGACY_OSM_PBF.is_file() and not _LATEST_OSM_PBF.is_file()
    else _LATEST_OSM_PBF
)
OSM_PBF = _path_setting(
    "GEOAI_OSM_PBF", _DEFAULT_OSM_PBF
)

WIKI_RAW_DIR = _path_setting("GEOAI_WIKI_RAW_DIR", DATA_DIR / "wiki_raw")

FAISS_DIR = _path_setting("GEOAI_FAISS_DIR", ROOT / "faiss_index")
FAISS_INDEX = _path_setting("GEOAI_FAISS_INDEX", FAISS_DIR / "faiss_index")
FAISS_META = _path_setting(
    "GEOAI_FAISS_META", FAISS_DIR / "faiss_index.metadata.json"
)
TAG_CATALOG = _path_setting(
    "GEOAI_TAG_CATALOG", ROOT / "data" / "knowledge" / "tag_catalog_v1.json"
)

OUTPUT_DIR = _path_setting("GEOAI_OUTPUT_DIR", ROOT / "output")
OUTPUT_GEOJSON = OUTPUT_DIR / "output.geojson"  # legacy compatibility only
CACHE_DIR = _path_setting("GEOAI_CACHE_DIR", ROOT / ".cache")
OSM_CLIP_CACHE_DIR = _path_setting(
    "GEOAI_OSM_CLIP_CACHE_DIR", CACHE_DIR / "osm_clips"
)

EMBEDDING_MODEL = os.getenv(
    "GEOAI_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
OLLAMA_BASE_URL = os.getenv("GEOAI_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("GEOAI_OLLAMA_MODEL", "qwen2.5:3b")
DEFAULT_PLACE = os.getenv("GEOAI_DEFAULT_PLACE", "Lund")
OSMIUM_EXTRACT_STRATEGY = os.getenv("GEOAI_OSMIUM_STRATEGY", "simple")
OSMIUM_TIMEOUT_S = _int_setting("GEOAI_OSMIUM_TIMEOUT_S", 180)
OSM_CLIP_CACHE_ENABLED = _bool_setting("GEOAI_OSM_CLIP_CACHE_ENABLED", True)

RAG_TOP_K = _int_setting("GEOAI_RAG_TOP_K", 5)
RAG_MIN_SCORE = _float_setting("GEOAI_RAG_MIN_SCORE", 0.15)
RETRIEVAL_MODE = os.getenv("GEOAI_RETRIEVAL_MODE", "hybrid").strip().lower()
HYBRID_DENSE_WEIGHT = _float_setting("GEOAI_HYBRID_DENSE_WEIGHT", 1.0)
HYBRID_LEXICAL_WEIGHT = _float_setting("GEOAI_HYBRID_LEXICAL_WEIGHT", 2.0)
HYBRID_RRF_K = _int_setting("GEOAI_HYBRID_RRF_K", 60)
HYBRID_CANDIDATE_K = _int_setting("GEOAI_HYBRID_CANDIDATE_K", 40)
HYBRID_NEGATIVE_WEIGHT = _float_setting("GEOAI_HYBRID_NEGATIVE_WEIGHT", 0.25)

OLLAMA_TIMEOUT_S = _int_setting("GEOAI_OLLAMA_TIMEOUT_S", 180)
OLLAMA_MAX_RETRIES = _int_setting("GEOAI_OLLAMA_MAX_RETRIES", 1)
NOMINATIM_TIMEOUT_S = _int_setting("GEOAI_NOMINATIM_TIMEOUT_S", 30)
NOMINATIM_MAX_RETRIES = _int_setting("GEOAI_NOMINATIM_MAX_RETRIES", 2)
RETRY_BACKOFF_S = _float_setting("GEOAI_RETRY_BACKOFF_S", 0.5)
GEOCODE_CACHE_TTL_S = _int_setting("GEOAI_GEOCODE_CACHE_TTL_S", 86400)

NOMINATIM_URL = os.getenv(
    "GEOAI_NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"
)
NOMINATIM_USER_AGENT = os.getenv(
    "GEOAI_NOMINATIM_USER_AGENT",
    "GeoAI-OSM-RAG/2.0 (educational project; set GEOAI_NOMINATIM_USER_AGENT)",
)

FLASK_HOST = os.getenv("GEOAI_FLASK_HOST", "127.0.0.1")
FLASK_PORT = _int_setting("GEOAI_FLASK_PORT", 8000)
FLASK_DEBUG = os.getenv("GEOAI_FLASK_DEBUG", "0").lower() in {
    "1",
    "true",
    "yes",
}
