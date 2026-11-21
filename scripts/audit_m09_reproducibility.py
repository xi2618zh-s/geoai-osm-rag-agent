"""Audit frozen M09 multi-turn receipt by byte-for-byte recomputation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.run_m09_turns import evaluate

RECEIPT = Path("benchmarks/results/m09/dev/m09_turn_eval_v1.json")


def main() -> None:
    rebuilt = (json.dumps(evaluate(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    published = RECEIPT.read_bytes()
    if rebuilt != published:
        raise SystemExit("[FAIL] M09 receipt differs from recomputation")
    print(f"[PASS] M09 v1: {hashlib.sha256(published).hexdigest()}")


if __name__ == "__main__":
    main()

