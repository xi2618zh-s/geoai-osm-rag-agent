"""Re-run the frozen M12a protocol and byte-compare the published deterministic receipt.

Also verifies the freeze manifest, rebuilds the oracle references, re-renders the demo
HTML from the saved ScenarioReport snapshot and checks the snapshot's download hashes.
"""
from __future__ import annotations

import base64
import hashlib
import json

from benchmarks.run_m12a_scenario import DEMO_HTML, RESULTS, SNAPSHOT, evaluate_once, preflight
from src.scenarios.html import render_scenario_html
from src.scenarios.report import ScenarioReport


def audit() -> dict:
    preflight()
    published = (RESULTS / "deterministic_receipt.json").read_bytes()
    if evaluate_once()["receipt_bytes"] != published:
        raise ValueError("M12a deterministic receipt differs from re-run")
    report = ScenarioReport.model_validate(json.loads(SNAPSHOT.read_bytes()))
    if render_scenario_html(report).encode("utf-8") != DEMO_HTML.read_bytes():
        raise ValueError("M12a demo HTML differs from the saved report snapshot")
    for item in report.downloads:
        data = base64.b64decode(item.href.split(",", 1)[1])
        if hashlib.sha256(data).hexdigest() != item.sha256 or len(data) != item.byte_count:
            raise ValueError(f"M12a download {item.filename} hash mismatch")
    summary = json.loads((RESULTS / "report.json").read_bytes())
    return {"receipt_sha256": hashlib.sha256(published).hexdigest(),
            "html_sha256": hashlib.sha256(DEMO_HTML.read_bytes()).hexdigest(),
            "cases_passed": summary["cases_passed"], "accepted": summary["accepted"]}


if __name__ == "__main__":
    result = audit()
    print(f"[PASS] M12a receipt {result['receipt_sha256']} ({result['cases_passed']}/13, accepted={result['accepted']}); "
          f"demo HTML {result['html_sha256']}")
