from __future__ import annotations

from datetime import datetime, timezone
import json
from unittest.mock import patch
import unittest

from src.knowledge.fetcher import (
    HttpResponse,
    OsmWikiFetchError,
    fetch_candidate_snapshot,
)


CANDIDATE = {
    "canonical_tag": "amenity=cafe",
    "primary_category": "food",
    "page_title": "Tag:amenity=cafe",
    "request_url": "https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dcafe",
    "retained_from_v1": True,
    "compatibility_only": False,
    "review_state": "manually_reviewed_candidate",
}


def api_response(*, title="Tag:amenity=cafe", revision=123, status=200):
    body = {
        "batchcomplete": True,
        "query": {
            "pages": [{
                "pageid": 10,
                "ns": 0,
                "title": title,
                "revisions": [{"revid": revision}],
            }]
        },
    }
    return HttpResponse(
        status_code=status,
        headers={"Content-Type": "application/json; charset=utf-8"},
        content=json.dumps(body).encode("utf-8"),
        url="https://wiki.openstreetmap.org/w/api.php",
    )


def fixed_api_response(html: str, *, status=200, title="Tag:amenity=cafe", revision=123):
    body = {
        "parse": {
            "title": title,
            "pageid": 10,
            "revid": revision,
            "displaytitle": "Cafe",
            "text": html,
        }
    }
    return HttpResponse(
        status_code=status,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "ETag": '"fixture-etag"',
            "Last-Modified": "Sun, 05 Oct 2026 00:00:00 GMT",
        },
        content=json.dumps(body).encode("utf-8"),
        url="https://wiki.openstreetmap.org/w/api.php?action=parse&oldid=123&prop=text%7Cdisplaytitle&format=json&formatversion=2",
    )


class OsmWikiFetcherTests(unittest.TestCase):
    def test_allowlisted_page_resolves_then_fetches_fixed_revision(self):
        seen = []

        def fake_get(url):
            seen.append(url)
            if "action=parse" not in url:
                return api_response()
            return fixed_api_response(
                "<html><head><title>Cafe - OpenStreetMap Wiki</title></head>"
                "<body><div id='content'><h1>Cafe</h1><p>A cafe serves drinks and light meals.</p></div></body></html>"
            )

        with patch("requests.get", side_effect=AssertionError("real network forbidden")):
            source = fetch_candidate_snapshot(
                CANDIDATE,
                http_get=fake_get,
                now=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
            )
        self.assertEqual(len(seen), 2)
        self.assertIn("titles=Tag%3Aamenity%3Dcafe", seen[0])
        self.assertIn("oldid=123", seen[1])
        self.assertEqual(source.revision, "123")
        self.assertEqual(source.etag, '"fixture-etag"')
        self.assertEqual(source.last_modified, "Sun, 05 Oct 2026 00:00:00 GMT")
        self.assertEqual(source.content_type, "application/json; charset=utf-8")
        self.assertFalse(source.page_has_separate_license)
        self.assertIn("A cafe serves drinks", source.text)

    def test_network_http_and_source_errors_are_distinct(self):
        def network_failure(_url):
            raise TimeoutError("offline")

        with self.assertRaises(OsmWikiFetchError) as raised:
            fetch_candidate_snapshot(CANDIDATE, http_get=network_failure)
        self.assertEqual(raised.exception.code, "OSM_KB_FETCH_FAILED")

        with self.assertRaises(OsmWikiFetchError) as raised:
            fetch_candidate_snapshot(CANDIDATE, http_get=lambda _url: api_response(status=503))
        self.assertEqual(raised.exception.code, "OSM_KB_HTTP_RESPONSE_INVALID")

        with self.assertRaises(OsmWikiFetchError) as raised:
            fetch_candidate_snapshot(
                CANDIDATE,
                http_get=lambda _url: api_response(title="Tag:amenity=restaurant"),
            )
        self.assertEqual(raised.exception.code, "OSM_KB_SOURCE_SCHEMA_INVALID")

    def test_redirect_only_tag_page_is_rejected(self):
        responses = iter([
            api_response(),
            fixed_api_response(
                "<html><body><div class='mw-parser-output'>"
                "<p>Redirect to:</p><p>Key:wheelchair</p>"
                "</div></body></html>"
            ),
        ])
        with self.assertRaises(OsmWikiFetchError) as raised:
            fetch_candidate_snapshot(CANDIDATE, http_get=lambda _url: next(responses))
        self.assertEqual(raised.exception.code, "OSM_KB_SOURCE_SCHEMA_INVALID")

    def test_page_level_license_marker_is_rejected(self):
        responses = iter([
            api_response(),
            fixed_api_response(
                "<html><body><div id='content'><a href='/wiki/Template:License'>Special license</a></div></body></html>"
            ),
        ])
        with self.assertRaises(OsmWikiFetchError) as raised:
            fetch_candidate_snapshot(CANDIDATE, http_get=lambda _url: next(responses))
        self.assertEqual(raised.exception.code, "OSM_KB_LICENSE_EXCEPTION")


if __name__ == "__main__":
    unittest.main()