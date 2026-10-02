"""Compatibility helpers for native libraries on Windows Unicode paths."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import faiss
import numpy as np


def read_faiss_index(path: Path):
    """Read a FAISS index, falling back to Python byte I/O for Unicode paths."""

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"FAISS index not found: {path}")
    try:
        return faiss.read_index(str(path))
    except RuntimeError:
        data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        return faiss.deserialize_index(data)


def write_faiss_index(index, path: Path) -> None:
    """Write a FAISS index with a Unicode-safe fallback."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        faiss.write_index(index, str(path))
    except RuntimeError:
        data = faiss.serialize_index(index)
        path.write_bytes(data.tobytes())


@contextmanager
def native_readable_path(path: Path) -> Iterator[Path]:
    """Yield an ASCII temporary copy when a native reader rejects Unicode.

    pyosmium on Windows currently passes file paths through a narrow native
    interface.  Copying only the already-clipped city PBF to the system temp
    directory is predictable and keeps the large country PBF untouched.
    """

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    if os.name != "nt" or str(path).isascii():
        yield path
        return

    with tempfile.TemporaryDirectory(prefix="geoai_native_") as temp_dir:
        temp_path = Path(temp_dir) / "input.osm.pbf"
        shutil.copyfile(path, temp_path)
        yield temp_path
