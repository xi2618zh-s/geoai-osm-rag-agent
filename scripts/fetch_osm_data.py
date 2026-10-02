"""Download an OSM PBF atomically and verify its provider checksum."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "osm" / "source_manifest.json"
USER_AGENT = "GeoAI-OSM-RAG-P5/1.0"
BUFFER_SIZE = 4 * 1024 * 1024


class ChecksumMismatch(RuntimeError):
    pass


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "pbf_url",
        "checksum_url",
        "checksum_algorithm",
        "default_output",
    }
    missing = required.difference(document)
    if missing:
        raise ValueError(f"Source manifest is missing: {sorted(missing)}")
    return document


def parse_checksum(value: str, default_algorithm: str = "md5") -> tuple[str, str]:
    value = str(value).strip()
    if not value:
        raise ValueError("Checksum response was empty")
    if ":" in value and not re.fullmatch(r"[0-9a-fA-F]+", value):
        algorithm, digest = value.split(":", 1)
    else:
        algorithm, digest = default_algorithm, value.split()[0]
    algorithm = algorithm.lower().strip()
    digest = digest.lower().strip()
    lengths = {"md5": 32, "sha256": 64}
    if algorithm not in lengths:
        raise ValueError("Only md5 and sha256 checksums are supported")
    if not re.fullmatch(rf"[0-9a-f]{{{lengths[algorithm]}}}", digest):
        raise ValueError(f"Invalid {algorithm} checksum: {digest!r}")
    return algorithm, digest


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        return response.read().decode("ascii", errors="strict")


def digest_file(path: Path, algorithm: str) -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(BUFFER_SIZE), b""):
            hasher.update(block)
    return hasher.hexdigest()


def download_verified(
    url: str,
    output: Path,
    *,
    algorithm: str,
    expected_digest: str,
    force: bool = False,
) -> dict:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        actual = digest_file(output, algorithm)
        if actual == expected_digest:
            return {"status": "existing_verified", "bytes": output.stat().st_size}
        raise ChecksumMismatch(
            f"Existing file checksum mismatch: expected {expected_digest}, got {actual}. "
            "Use --force only after confirming the source."
        )

    temporary = output.with_name(f"{output.name}.part")
    temporary.unlink(missing_ok=True)
    hasher = hashlib.new(algorithm)
    downloaded = 0
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as target:
            for block in iter(lambda: response.read(BUFFER_SIZE), b""):
                target.write(block)
                hasher.update(block)
                downloaded += len(block)
        actual = hasher.hexdigest()
        if actual != expected_digest:
            raise ChecksumMismatch(
                f"Downloaded checksum mismatch: expected {expected_digest}, got {actual}"
            )
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"status": "downloaded_verified", "bytes": downloaded}


def write_receipt(
    output: Path,
    *,
    url: str,
    algorithm: str,
    digest: str,
    result: dict,
) -> Path:
    receipt = output.with_name(f"{output.name}.receipt.json")
    document = {
        "schema_version": 1,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
        "path": str(output),
        "bytes": result["bytes"],
        "checksum_algorithm": algorithm,
        "checksum": digest,
        "status": result["status"],
    }
    temporary = receipt.with_name(f"{receipt.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
    os.replace(temporary, receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    manifest = load_manifest()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=manifest["pbf_url"])
    parser.add_argument("--checksum-url", default=manifest["checksum_url"])
    parser.add_argument(
        "--checksum",
        help="Pinned checksum as md5:HEX or sha256:HEX; skips checksum URL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / manifest["default_output"],
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.checksum:
        algorithm, expected = parse_checksum(args.checksum)
        checksum_source = "command_line"
    else:
        checksum_text = fetch_text(args.checksum_url)
        algorithm, expected = parse_checksum(checksum_text, "md5")
        checksum_source = args.checksum_url
    output = args.output if args.output.is_absolute() else ROOT / args.output
    print(f"Source: {args.url}")
    print(f"Output: {output.resolve()}")
    print(f"Expected {algorithm}: {expected} ({checksum_source})")
    if args.dry_run:
        print("Dry run: PBF was not downloaded.")
        return 0
    result = download_verified(
        args.url,
        output,
        algorithm=algorithm,
        expected_digest=expected,
        force=args.force,
    )
    receipt = write_receipt(
        output.resolve(),
        url=args.url,
        algorithm=algorithm,
        digest=expected,
        result=result,
    )
    print(f"PASS: {result['status']}, {result['bytes']} bytes")
    print(f"Receipt: {receipt}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ChecksumMismatch, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
