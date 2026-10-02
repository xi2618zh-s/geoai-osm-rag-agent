"""Audit repository assets needed for a clean, reviewable reproduction."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REQUIRED_FILES = [
    "README.md",
    "requirements.txt",
    "environment.yml",
    ".env.example",
    "app_min.py",
    "chat.html",
    "verify_installation.py",
    "data/osm/source_manifest.json",
    "data/knowledge/tag_catalog_v1.json",
    "data/wiki_raw/wiki_raw.jsonl",
    "faiss_index/faiss_index",
    "faiss_index/faiss_index.metadata.json",
    "benchmarks/data/query_benchmark_v1.jsonl",
    "benchmarks/data/workflow_benchmark_v1.jsonl",
]


def git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def audit(*, ci: bool) -> dict:
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    missing = [item for item in REQUIRED_FILES if not (ROOT / item).is_file()]
    record("required_files", not missing, "missing=" + repr(missing))

    for relative in [
        "data/osm/source_manifest.json",
        "data/knowledge/tag_catalog_v1.json",
        "faiss_index/faiss_index.metadata.json",
        "benchmarks/results/p4_workflow.json",
    ]:
        try:
            json.loads((ROOT / relative).read_text(encoding="utf-8"))
            record(f"json:{relative}", True, "valid JSON")
        except Exception as error:
            record(f"json:{relative}", False, str(error))

    ignored = git("check-ignore", "data/osm/repro-check.osm.pbf")
    record("pbf_gitignore", ignored.returncode == 0, ignored.stdout.strip())

    tracked = git("ls-files", "data/osm/*.osm.pbf", "output", ".cache")
    tracked_items = [item for item in tracked.stdout.splitlines() if item.strip()]
    record("large_runtime_files_untracked", not tracked_items, repr(tracked_items))

    absolute_path_hits: list[str] = []
    pattern = re.compile(r"(?:[A-Za-z]:\\\\|C:/Users/|D:/)")
    for path in [*ROOT.glob("src/**/*.py"), *ROOT.glob("scripts/*.py")]:
        if path.resolve() == Path(__file__).resolve():
            continue
        for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if pattern.search(line):
                absolute_path_hits.append(f"{path.relative_to(ROOT)}:{number}")
    record("no_hardcoded_source_paths", not absolute_path_hits, repr(absolute_path_hits))

    environment = (ROOT / "environment.yml").read_text(encoding="utf-8")
    record("osmium_declared", "osmium-tool" in environment, "environment.yml")
    record(
        "offline_tests_present",
        (ROOT / "tests" / "test_p5_delivery.py").is_file(),
        "tests/test_p5_delivery.py",
    )

    if ci:
        record("pbf_runtime", True, "skipped in CI by design")
        record("osmium_runtime", True, "installed by CI workflow")
    else:
        from src.config import OSM_PBF
        from src.osm.extractor import find_osmium_executable

        record("pbf_runtime", Path(OSM_PBF).is_file(), str(OSM_PBF))
        osmium_path = find_osmium_executable()
        record("osmium_runtime", osmium_path is not None, str(osmium_path))

    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ci" if ci else "local",
        "passed": passed,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci", action="store_true", help="Skip large local runtime inputs")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = audit(ci=args.ci)
    for item in report["checks"]:
        marker = "PASS" if item["passed"] else "FAIL"
        print(f"[{marker}] {item['name']}: {item['detail']}")
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
