"""Recompute M10 public receipt and released standalone HTML byte for byte."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from benchmarks.run_m10_results import evaluate
from src.agent.result_html import _render_audited_payload
from src.agent.result_presentation import AnalysisResult

RECEIPT = Path("benchmarks/results/m10/dev/m10_result_eval_v1.json")


def main() -> None:
    rebuilt = (json.dumps(evaluate(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    published = RECEIPT.read_bytes()
    if rebuilt != published:
        raise SystemExit("[FAIL] M10 evaluation receipt differs from recomputation")
    print(f"[PASS] M10 evaluation: {hashlib.sha256(published).hexdigest()}")
    for version in ("v1", "v2"):
        result_path = Path(f"benchmarks/results/m10/dev/m10_demo_result_{version}.json")
        html_path = Path(f"docs/demo/m10_analysis_result_{version}.html")
        result = AnalysisResult.model_validate_json(result_path.read_text(encoding="utf-8"))
        for item in result.downloads:
            data = base64.b64decode(item.href.split(",", 1)[1], validate=True)
            if hashlib.sha256(data).hexdigest() != item.sha256 or len(data) != item.byte_count:
                raise SystemExit(f"[FAIL] M10 {version} published download differs from recorded hash")
        if _render_audited_payload(result).encode("utf-8") != html_path.read_bytes():
            raise SystemExit(f"[FAIL] M10 {version} HTML differs from its frozen result snapshot")
        print(f"[PASS] M10 {version} result: {hashlib.sha256(result_path.read_bytes()).hexdigest()}")
        print(f"[PASS] M10 {version} HTML: {hashlib.sha256(html_path.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()

