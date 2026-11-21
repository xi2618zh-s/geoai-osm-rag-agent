"""Locate scan-leaks hits in the public private-v2 outputs without printing private content.

Run by the custodian/evaluator or the owner (it reads the sealed workspace). For every
whole-word hit of a private role, field name or text value (and any query or task ID
substring) it reports only: category, file, a masked JSON path or the Markdown heading,
whether the hit sits in a key or a value, and whether the containing string also appears
verbatim in the public dev report (i.e. fixed template text). Private words are never
printed: path components containing a hit become <HIT>, keys absent from the dev report
become <OTHER>, and headings absent from the dev report become <OTHER>.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator

from benchmarks.run_m11c_v2 import PRIVATE_V2_RECEIPT, RESULTS, verify_sealed
from src.agent.model_frontend import profile_catalogue

PUBLIC_DIR = RESULTS / "private" / "m11c_v2_private_formal"
DEV_DIR = RESULTS / "formal" / "m11c_v2_dev_formal"
FILES = ("report.json", "report.md", "deterministic_receipt.json")


def _patterns(workspace, receipt_path: Path) -> dict[str, list[re.Pattern]]:
    tasks, catalogue = verify_sealed(workspace, receipt_path)
    profile = profile_catalogue(catalogue)
    words = {"roles": set(profile.roles()), "fields": {f.name for d in profile.datasets for f in d.fields},
             "text_values": {json.loads(raw) for d in profile.datasets for f in d.fields
                             for kind, raw in f.distinct if kind == "str" and len(json.loads(raw)) >= 4}}
    out = {name: [re.compile(rf"(?<![A-Za-z0-9_]){re.escape(v)}(?![A-Za-z0-9_])") for v in sorted(values)]
           for name, values in words.items()}
    out["queries"] = [re.compile(re.escape(t.query)) for t in tasks]
    out["task_ids"] = [re.compile(re.escape(t.task_id)) for t in tasks]
    return out


def _walk(node: Any, path: tuple = ()) -> Iterator[tuple[tuple, str, str]]:
    """Yield (path, 'key'|'value', text) for every key and string value."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, "key", str(key)
            yield from _walk(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, path + (index,))
    elif isinstance(node, str):
        yield path, "value", node


def _dev_vocabulary(dev_dir: Path) -> tuple[set[str], set[str], set[str]]:
    keys, strings = set(), set()
    for name in ("report.json", "deterministic_receipt.json"):
        path = Path(dev_dir) / name
        if path.is_file():
            for _, kind, text in _walk(json.loads(path.read_text("utf-8"))):
                (keys if kind == "key" else strings).add(text)
    lines = set((Path(dev_dir) / "report.md").read_text("utf-8").splitlines()) if (Path(dev_dir) / "report.md").is_file() else set()
    return keys, strings | keys, lines


def _contains(patterns: list[re.Pattern], text: str) -> int:
    return sum(len(p.findall(text)) for p in patterns)


def locate(workspace, *, public_dir: Path = PUBLIC_DIR, dev_dir: Path = DEV_DIR,
           receipt_path: Path = PRIVATE_V2_RECEIPT) -> dict[str, Any]:
    patterns = _patterns(workspace, receipt_path)
    every = [p for group in patterns.values() for p in group]
    dev_keys, dev_strings, dev_lines = _dev_vocabulary(dev_dir)
    hits = []
    for name in FILES:
        path = Path(public_dir) / name
        if not path.is_file():
            continue
        if name.endswith(".json"):
            for location, kind, text in _walk(json.loads(path.read_text("utf-8"))):
                masked = [part if isinstance(part, int) else
                          ("<HIT>" if _contains(every, part) else part if part in dev_keys else "<OTHER>")
                          for part in location]
                for category, group in patterns.items():
                    count = _contains(group, text)
                    if count:
                        hits.append({"category": category, "count": count, "file": name, "in": kind,
                                     "location": masked,
                                     "verbatim_in_dev_report": text in dev_strings
                                     or any(text in line for line in dev_lines)})
        else:
            heading = None
            for line in path.read_text("utf-8").splitlines():
                if line.startswith("#"):
                    heading = line if (line in dev_lines and not _contains(every, line)) else "<OTHER>"
                for category, group in patterns.items():
                    count = _contains(group, line)
                    if count:
                        hits.append({"category": category, "count": count, "file": name,
                                     "in": "table row" if line.startswith("|") else
                                     "bullet" if line.startswith("-") else "text",
                                     "location": heading, "verbatim_in_dev_report": line in dev_lines})
    total = sum(item["count"] for item in hits)
    template = sum(item["count"] for item in hits if item["verbatim_in_dev_report"])
    return {"hits": hits, "summary": {"total_hits": total, "in_strings_identical_to_dev_report": template,
                                      "elsewhere": total - template}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("GEOAI_M11C_PRIVATE_V2_DIR"))
    parser.add_argument("--public-dir", type=Path, default=PUBLIC_DIR)
    args = parser.parse_args()
    print(json.dumps(locate(args.workspace, public_dir=args.public_dir), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
