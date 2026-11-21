"""Explicit online acquisition for allow-listed official OSM Wiki Tag pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable, Mapping
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup
import requests

from .contracts import RawWikiSourceV1
from .errors import KnowledgeValidationError
from .validation import validate_raw_source_payload


USER_AGENT = "GeoAI-OSM-Knowledge-M03/2.0 (text-only research snapshot)"
API_ENDPOINT = "https://wiki.openstreetmap.org/w/api.php"
WIKI_INDEX = "https://wiki.openstreetmap.org/w/index.php"
LICENSE_URL = "https://wiki.openstreetmap.org/wiki/Wiki_content_license"
LICENSE_TEMPLATE_RE = re.compile(r"/wiki/Template:(?:License|Copyright|PD|Cc)", re.IGNORECASE)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    url: str


class OsmWikiFetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _default_get(url: str) -> HttpResponse:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json"},
        timeout=45,
        allow_redirects=True,
    )
    return HttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        content=response.content,
        url=response.url,
    )


def _request(http_get: Callable[[str], HttpResponse], url: str) -> HttpResponse:
    try:
        response = http_get(url)
    except Exception as error:
        raise OsmWikiFetchError("OSM_KB_FETCH_FAILED", f"request failed for {url}: {error}") from error
    if response.status_code != 200:
        raise OsmWikiFetchError(
            "OSM_KB_HTTP_RESPONSE_INVALID",
            f"unexpected HTTP {response.status_code} for {url}",
        )
    return response


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.casefold() == name.casefold():
            return str(value)
    return None


def _title_identity(value: str) -> str:
    return " ".join(str(value).replace("_", " ").casefold().split())


def _resolve_revision(candidate: dict, http_get: Callable[[str], HttpResponse]) -> int:
    params = [
        ("action", "query"),
        ("prop", "revisions"),
        ("rvprop", "ids"),
        ("titles", candidate["page_title"]),
        ("format", "json"),
        ("formatversion", "2"),
    ]
    api_url = API_ENDPOINT + "?" + urlencode(params)
    response = _request(http_get, api_url)
    content_type = _header(response.headers, "Content-Type") or ""
    if "json" not in content_type.casefold():
        raise OsmWikiFetchError("OSM_KB_HTTP_RESPONSE_INVALID", "revision response is not JSON")
    try:
        payload = json.loads(response.content.decode("utf-8"))
        pages = payload["query"]["pages"]
        if len(pages) != 1:
            raise ValueError("expected one page")
        page = pages[0]
        if page.get("missing") is not None or "missing" in page:
            raise ValueError("page is missing")
        if _title_identity(page.get("title", "")) != _title_identity(candidate["page_title"]):
            raise ValueError("page title differs from frozen candidate")
        if "redirect" in page:
            raise ValueError("redirect pages are not accepted")
        revision = page["revisions"][0]["revid"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
            raise ValueError("invalid revision")
        return revision
    except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", str(error)) from error


def _fixed_revision_url(page_title: str, revision: int) -> str:
    return WIKI_INDEX + "?" + urlencode([("title", page_title), ("oldid", str(revision))])


def _fixed_parse_api_url(revision: int) -> str:
    return API_ENDPOINT + "?" + urlencode(
        [
            ("action", "parse"),
            ("oldid", str(revision)),
            ("prop", "text|displaytitle"),
            ("format", "json"),
            ("formatversion", "2"),
        ]
    )


def _validate_fixed_api_url(url: str, revision: int) -> None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.hostname != "wiki.openstreetmap.org":
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", "fixed page left official Wiki host")
    if query.get("action") != ["parse"] or query.get("oldid") != [str(revision)]:
        raise OsmWikiFetchError(
            "OSM_KB_SOURCE_SCHEMA_INVALID",
            "fixed revision response redirected to an unexpected API target",
        )


def _extract_text_and_license(html: bytes) -> tuple[str, bool]:
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as error:
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", "invalid Wiki HTML") from error
    main = soup.find("div", class_="mw-parser-output") or soup.find("div", id="bodyContent") or soup.find("div", id="content") or soup.body
    if main is None:
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", "Wiki page has no content container")
    for element in main.select("script, style, noscript"):
        element.decompose()
    separate_license = bool(
        main.find("a", href=LICENSE_TEMPLATE_RE)
        or main.select_one(".licensetpl, .license, [class*='licensebox']")
    )
    lines = [" ".join(line.split()) for line in main.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    if not text:
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", "Wiki page text is empty")
    return text, separate_license


def _parse_fixed_api_response(
    response: HttpResponse,
    page_title: str,
    revision: int,
) -> tuple[str, str, bool]:
    content_type = _header(response.headers, "Content-Type") or ""
    if "json" not in content_type.casefold():
        raise OsmWikiFetchError("OSM_KB_HTTP_RESPONSE_INVALID", "fixed page response is not JSON")
    _validate_fixed_api_url(response.url, revision)
    try:
        payload = json.loads(response.content.decode("utf-8"))
        parsed = payload["parse"]
        if parsed["revid"] != revision:
            raise ValueError("fixed API revision differs from requested oldid")
        if _title_identity(parsed["title"]) != _title_identity(page_title):
            raise ValueError("fixed API title differs from frozen candidate")
        html = parsed["text"]
        if not isinstance(html, str):
            raise ValueError("fixed API text is not a string")
        display_title = BeautifulSoup(str(parsed.get("displaytitle", parsed["title"])), "lxml").get_text(" ", strip=True)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", str(error)) from error
    text, separate_license = _extract_text_and_license(html.encode("utf-8"))
    if re.match(r"^\s*(?:Redirect to:|#REDIRECT\b)", text, flags=re.IGNORECASE):
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", "Tag page is a redirect-only stub")
    return display_title, text, separate_license

def fetch_candidate_snapshot(
    candidate: dict,
    *,
    http_get: Callable[[str], HttpResponse] | None = None,
    now: Callable[[], datetime] | None = None,
) -> RawWikiSourceV1:
    get = http_get or _default_get
    clock = now or (lambda: datetime.now(timezone.utc))
    required = {"canonical_tag", "page_title", "request_url", "review_state"}
    if not isinstance(candidate, dict) or not required.issubset(candidate):
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", "candidate record is incomplete")
    request = urlparse(str(candidate["request_url"]))
    if request.scheme != "https" or request.hostname != "wiki.openstreetmap.org":
        raise OsmWikiFetchError("OSM_KB_SOURCE_SCHEMA_INVALID", "candidate is outside the official Wiki allow-list")

    revision = _resolve_revision(candidate, get)
    fixed_url = _fixed_revision_url(candidate["page_title"], revision)
    fixed_api_url = _fixed_parse_api_url(revision)
    response = _request(get, fixed_api_url)
    content_type = _header(response.headers, "Content-Type") or ""
    title, text, separate_license = _parse_fixed_api_response(
        response, candidate["page_title"], revision
    )
    if separate_license:
        raise OsmWikiFetchError(
            "OSM_KB_LICENSE_EXCEPTION",
            "page contains a separate license marker and requires manual review",
        )

    retrieved_at = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    history_url = WIKI_INDEX + "?" + urlencode(
        [("title", candidate["page_title"]), ("action", "history")]
    )
    payload = {
        "schema_version": "1.0",
        "source_id": f"osm-wiki:{candidate['canonical_tag']}@{revision}",
        "canonical_tag": candidate["canonical_tag"],
        "request_url": candidate["request_url"],
        "canonical_url": fixed_url,
        "revision": str(revision),
        "history_url": history_url,
        "retrieved_at": retrieved_at,
        "etag": _header(response.headers, "ETag"),
        "last_modified": _header(response.headers, "Last-Modified"),
        "content_type": content_type,
        "title": title or candidate["page_title"],
        "text": text,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "license_id": "CC-BY-SA-2.0",
        "license_url": LICENSE_URL,
        "contributors": "OpenStreetMap Wiki contributors",
        "modification_note": "Text extracted from fixed-revision MediaWiki API HTML; navigation and scripts removed.",
        "page_has_separate_license": False,
    }
    try:
        return validate_raw_source_payload(payload)
    except KnowledgeValidationError as error:
        raise OsmWikiFetchError(error.code, str(error)) from error