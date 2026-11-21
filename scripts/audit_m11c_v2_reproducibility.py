"""Replay the formal M11c v2 public-dev run from recorded raw model responses.

No model is called: every recorded response is re-parsed with the frozen frame parser,
grounded deterministically and executed again through the frozen systems, and the
rebuilt deterministic receipt (no timings) is byte-compared with the published one.
The stage-3 verdict's result hashes and the published prompt file are checked too.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.run_m11c_v2 import RESULTS, STAGE2_NAME, STAGE3_NAME, replay_receipt
from src.agent.model_frontend import load_prompt

RUN_DIR = RESULTS / "formal" / "m11c_v2_dev_formal"
MANIFEST = RESULTS / "freeze" / STAGE2_NAME


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(run_dir: Path = RUN_DIR, manifest: Path = MANIFEST) -> dict:
    frozen = json.loads(Path(manifest).read_text("utf-8"))
    if load_prompt().sha256 != frozen["prompt"]["sha256"]:
        raise ValueError("published prompt file differs from the stage-2 manifest")
    stage3 = json.loads((Path(run_dir) / STAGE3_NAME).read_text("utf-8"))
    for name, digest in stage3["result_sha256"].items():
        if _sha(Path(run_dir) / name) != digest:
            raise ValueError(f"{name} changed after the stage-3 verdict")
    published = (Path(run_dir) / "deterministic_receipt.json").read_bytes()
    if replay_receipt(run_dir, manifest) != published:
        raise ValueError("M11c v2 deterministic receipt differs from the replay")
    return {"receipt_sha256": hashlib.sha256(published).hexdigest(), "gate_passed": stage3["gate_passed"]}


if __name__ == "__main__":
    result = audit()
    print(f"[PASS] M11c v2 dev receipt {result['receipt_sha256']} replayed from recorded responses "
          f"(stage-3 gate passed: {result['gate_passed']})")
