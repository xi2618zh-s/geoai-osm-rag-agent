"""Re-run the frozen M11c protocol and byte-compare the published deterministic receipt.

Timings and timestamps are excluded from the receipt; every outcome, score
component, tool sequence and Feature ID set is covered through per-run counts and
signature hashes. The private split is re-run only when the ignored private
package is present; otherwise only the public-dev part is compared and the
private part is reported as not verified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.run_m11c_baselines import MANIFEST_NAME, RESULTS, deterministic_receipt
from src.evaluation.m11c import PRIVATE_DIR

RUN_DIR = RESULTS / "formal" / "m11c_comparison_v1"
MANIFEST = RESULTS / "freeze" / MANIFEST_NAME


def audit(run_dir: Path = RUN_DIR, manifest: Path = MANIFEST) -> dict:
    published_bytes = (Path(run_dir) / "deterministic_receipt.json").read_bytes()
    published = json.loads(published_bytes)
    private = PRIVATE_DIR.is_dir() and "test" in published["splits"]
    rebuilt = deterministic_receipt(manifest, seeds=published["seeds"], include_private=private,
                                    check_fingerprint=False)
    if private:
        if (json.dumps(rebuilt, sort_keys=True, indent=2) + "\n").encode("utf-8") != published_bytes:
            raise ValueError("M11c deterministic receipt differs from re-run")
    elif rebuilt["splits"]["dev"] != published["splits"]["dev"]:
        raise ValueError("M11c public-dev receipt differs from re-run")
    return {"receipt_sha256": hashlib.sha256(published_bytes).hexdigest(),
            "dev_verified": True, "private_verified": private}


if __name__ == "__main__":
    result = audit()
    print(f"[PASS] M11c receipt {result['receipt_sha256']} "
          f"(dev verified; private verified: {result['private_verified']})")
