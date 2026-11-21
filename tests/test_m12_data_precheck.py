"""M12 data precheck gate v2 (plan v2.1 section 2.2, ADR-0024 revision)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

import osmium

from scripts import build_m12_lund_data as data
from src.population.contracts import SCB_PROPERTY_NAMES

AGE_FIELDS = [name for name in SCB_PROPERTY_NAMES if name.startswith("ald")]
ABSENT = object()  # marks a property key that must be omitted


def scb_feature(index: int, **overrides):
    x = 378000 + (index % 20) * 1000
    y = 6166000 + (index // 20) * 1000
    properties = {
        "objectid": index + 1, "rutid_inspire": f"1kmN{y // 1000}E{x // 1000}",
        "rutid_scb": f"{x}{y}", "rutstorl": 1000, "beftotalt": 1000, "man": 500,
        "kvinna": 500, "referenstid": "20251231",
    }
    properties.update({name: 10 for name in AGE_FIELDS})
    properties.update(overrides)
    for key in [key for key, value in overrides.items() if value is ABSENT]:
        properties.pop(key)
    return {
        "type": "Feature", "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 1000, y], [x + 1000, y + 1000], [x, y + 1000], [x, y]]]},
    }


def collection(features) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")


class PropertyAuditTests(unittest.TestCase):
    def test_geometry_element_is_checked_as_top_level_geometry(self):
        audit = data.scb_property_audit([scb_feature(0), scb_feature(1)])
        self.assertNotIn("sp_geometry", audit["required_property_keys"])
        self.assertEqual(len(audit["required_property_keys"]), len(SCB_PROPERTY_NAMES) - 1)
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["geometry_invalid_count"], 0)
        # The v1 defect: the union-of-keys check reports the geometry element as missing.
        self.assertIn("sp_geometry", data.scb_summary_v1_missing_properties([scb_feature(0)]))

    def test_every_key_must_be_present_in_every_feature(self):
        features = [scb_feature(0), scb_feature(1, ald5_9=ABSENT)]
        audit = data.scb_property_audit(features)
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["missing_key_counts"], {"ald5_9": 1})
        # A union over features would wrongly report the set as complete.
        self.assertNotIn("ald5_9", data.scb_summary_v1_missing_properties(features))

    def test_rule_based_missing_values_are_allowed(self):
        features = [scb_feature(0, beftotalt=None, ald5_9=".."), scb_feature(1, man="-", kvinna="")]
        audit = data.scb_property_audit(features)
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["rule_violation_counts"], {})
        self.assertEqual(audit["population_null_or_token_counts"]["beftotalt"], 1)
        self.assertEqual(audit["population_null_or_token_counts"]["ald5_9"], 1)

    def test_rule_violations_are_counted(self):
        features = [
            scb_feature(0, beftotalt=-1),
            scb_feature(1, ald5_9=True),
            scb_feature(2, man="n/a"),
            scb_feature(3, objectid="4"),
            scb_feature(4, rutstorl=500),
            scb_feature(5, rutid_scb=" "),
            scb_feature(6, referenstid="20241231"),
            scb_feature(7, rutid_inspire=scb_feature(8)["properties"]["rutid_inspire"]),
            scb_feature(8),
        ]
        features.append({**scb_feature(9), "geometry": None})
        features.append({**scb_feature(10), "geometry": {"type": "Point", "coordinates": [1, 2]}})
        audit = data.scb_property_audit(features)
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["rule_violation_counts"], {
            "population_value": 3, "objectid": 1, "rutstorl": 1, "identifier_string": 1,
            "referenstid": 1, "rutid_inspire_duplicate": 1,
        })
        self.assertEqual(audit["geometry_invalid_count"], 2)


class GateV2Tests(unittest.TestCase):
    def summary(self, count: int) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scb.geojson"
            path.write_bytes(collection([scb_feature(i) for i in range(count)]))
            return data.scb_summary_v2(path)

    def test_feature_count_upper_bound_is_484(self):
        self.assertEqual(data.GATE_V1["scb_feature_count_max"], 400)
        self.assertEqual(data.GATE_V2["scb_feature_count_max"], 484)
        self.assertTrue(self.summary(484)["checks"]["feature_count"])
        self.assertFalse(self.summary(485)["checks"]["feature_count"])
        self.assertTrue(self.summary(100)["checks"]["feature_count"])
        self.assertFalse(self.summary(99)["checks"]["feature_count"])

    def test_summary_reports_edge_cell_inclusion_without_exact_area_claim(self):
        summary = self.summary(120)
        self.assertTrue(all(summary["checks"].values()))
        edge = summary["edge_cell_rule"]
        self.assertEqual(edge["cells_within_study_bbox"] + edge["cells_touching_or_crossing_edge"], 120)
        self.assertIn("not the exact population", edge["population_statement"])

    def test_report_references_v1_and_refuses_overwrite(self):
        sections = {name: {"checks": {"ok": True}} for name in ("scb", "osm", "m12a_nontriviality", "gtfs")}
        report = data.build_report_v2(sections, v1_reference={"file_name": "m12_data_precheck_v1.json", "sha256": "a" * 64})
        self.assertTrue(report["passed"])
        self.assertEqual(report["gate_version"], "v2")
        self.assertEqual(report["supersedes"]["sha256"], "a" * 64)
        self.assertTrue(report["amendments"])
        failing = dict(sections, gtfs={"checks": {"ok": False}})
        self.assertFalse(data.build_report_v2(failing, v1_reference=report["supersedes"])["passed"])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.json"
            data._write_exclusive(target, b"{}\n")
            with self.assertRaises(FileExistsError):
                data._write_exclusive(target, b"{}\n")


class BuilderGuardTests(unittest.TestCase):
    def test_builders_require_a_passed_v2_report_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            def report(name, **payload):
                path = Path(tmp) / name
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path
            good = report("v2.json", schema="m12-data-precheck-v2", passed=True, scb={"sha256": "a" * 64})
            self.assertTrue(data.require_passed_precheck(good, section="scb", sha256="a" * 64)["passed"])
            for path in (report("v1.json", schema="m12-data-precheck-v1", passed=True, scb={"sha256": "a" * 64}),
                         report("failed.json", schema="m12-data-precheck-v2", passed=False, scb={"sha256": "a" * 64})):
                with self.assertRaises(SystemExit):
                    data.require_passed_precheck(path, section="scb", sha256="a" * 64)
            with self.assertRaises(SystemExit):
                data.require_passed_precheck(good, section="scb", sha256="b" * 64)


class GtfsServiceTests(unittest.TestCase):
    def test_weekly_calendar_and_exceptions(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("calendar.txt", "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                             "WEEK,1,1,1,1,1,0,0,20250901,20251231\nREMOVED,1,1,1,1,1,0,0,20250901,20251231\n")
            archive.writestr("calendar_dates.txt", "service_id,date,exception_type\nADDED,20251007,1\nREMOVED,20251007,2\nOTHER,20251008,1\n")
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
            self.assertEqual(data.active_service_ids(archive, date(2025, 10, 7)), {"WEEK", "ADDED"})


class OsmExtractionTests(unittest.TestCase):
    def test_three_pass_extraction_of_nodes_ways_and_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.osm.pbf"
            writer = osmium.SimpleWriter(str(path))
            try:
                lon, lat = 13.19, 55.70  # inside the Lund study BBOX
                writer.add_node(osmium.osm.mutable.Node(id=1, location=(lon, lat), tags={"amenity": "school", "name": "A"}))
                square = [(lon + 0.01, lat), (lon + 0.012, lat), (lon + 0.012, lat + 0.001), (lon + 0.01, lat + 0.001)]
                for offset, coord in enumerate(square, start=10):
                    writer.add_node(osmium.osm.mutable.Node(id=offset, location=coord))
                ring = [(lon - 0.01, lat), (lon - 0.008, lat), (lon - 0.008, lat + 0.001), (lon - 0.01, lat + 0.001)]
                for offset, coord in enumerate(ring, start=20):
                    writer.add_node(osmium.osm.mutable.Node(id=offset, location=coord))
                writer.add_node(osmium.osm.mutable.Node(id=30, location=(18.07, 59.33), tags={"amenity": "school"}))
                writer.add_node(osmium.osm.mutable.Node(id=31, location=(lon, lat + 0.002), tags={"amenity": "bench"}))
                writer.add_way(osmium.osm.mutable.Way(id=100, nodes=[10, 11, 12, 13, 10], tags={"shop": "supermarket"}))
                writer.add_way(osmium.osm.mutable.Way(id=101, nodes=[20, 21, 22, 23, 20]))
                writer.add_relation(osmium.osm.mutable.Relation(
                    id=200, members=[("w", 101, "outer")], tags={"type": "multipolygon", "amenity": "pharmacy"}))
            finally:
                writer.close()
            pois, stats = data.extract_pois(path)
        found = {(p["osm_type"], p["osm_id"]): p for p in pois}
        self.assertEqual(set(found), {("node", 1), ("way", 100), ("relation", 200)})
        self.assertEqual(found[("node", 1)]["category"], "school")
        self.assertEqual(found[("way", 100)]["method"], "way_representative_point")
        self.assertEqual(found[("relation", 200)]["category"], "pharmacy")
        self.assertTrue(all(p["in_study_bbox"] for p in pois))
        self.assertEqual(stats["skipped_geometry"], 0)


if __name__ == "__main__":
    unittest.main()
