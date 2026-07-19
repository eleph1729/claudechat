"""Client for The National Archives' Find Case Law service.

Uses the public Atom search feed to find judgments whose full text contains a
citation, and fetches judgment bodies as Akoma Ntoso (LegalDocML) XML.

Programmatic use of the service requires a computational-analysis licence:
https://caselaw.nationalarchives.gov.uk/licence-application-process
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from . import __version__
from .citation import NeutralCitation

BASE_URL = os.environ.get("FCL_BASE_URL", "https://caselaw.nationalarchives.gov.uk")

ATOM_NS = "{http://www.w3.org/2005/Atom}"
AKN_NS = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"
UK_NS = "{https://caselaw.nationalarchives.gov.uk/akn}"

_WS = re.compile(r"\s+")


@dataclass
class SearchHit:
    title: str
    uri: str   # FCL document URI, e.g. "ewca/civ/2020/123"
    link: str  # public judgment page URL
    date: str | None = None


@dataclass
class Judgment:
    uri: str
    name: str | None
    cite: str | None
    date: str | None
    xml: str


class FCLClient:
    def __init__(self, base_url: str = BASE_URL, delay: float = 0.5,
                 timeout: float = 30.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent", f"fcl-citator/{__version__} (licensed computational analysis)"
        )
        self._last_request = 0.0

    # ------------------------------------------------------------------ http

    def _get(self, url: str, **params) -> requests.Response:
        # Be polite to the service: at most one request per `delay` seconds.
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        resp = self.session.get(url, params=params or None, timeout=self.timeout)
        self._last_request = time.monotonic()
        resp.raise_for_status()
        return resp

    # ---------------------------------------------------------------- search

    def search(self, phrase: str, max_pages: int = 4, per_page: int = 50) -> list[SearchHit]:
        """Full-text search for an exact phrase, following pagination."""
        hits: list[SearchHit] = []
        for page in range(1, max_pages + 1):
            resp = self._get(f"{self.base_url}/atom.xml",
                             query=f'"{phrase}"', page=page, per_page=per_page)
            page_hits = parse_atom_feed(resp.text, self.base_url)
            hits.extend(page_hits)
            if len(page_hits) < per_page:
                break
        return hits

    def find_citing(self, target: NeutralCitation, extra_phrases: list[str] | None = None,
                    max_pages: int = 4) -> list[SearchHit]:
        """Find judgments citing `target`, searching several citation forms."""
        phrases = [target.canonical] + list(extra_phrases or [])
        seen: dict[str, SearchHit] = {}
        for phrase in phrases:
            for hit in self.search(phrase, max_pages=max_pages):
                if hit.uri and hit.uri != target.fcl_uri:
                    seen.setdefault(hit.uri, hit)
        return list(seen.values())

    # ------------------------------------------------------------- judgments

    def fetch_judgment(self, uri: str) -> Judgment:
        resp = self._get(f"{self.base_url}/{uri}/data.xml")
        name, cite, date = parse_judgment_metadata(resp.text)
        return Judgment(uri=uri, name=name, cite=cite, date=date, xml=resp.text)


# ---------------------------------------------------------------- pure parsing


def parse_atom_feed(xml_text: str, base_url: str = BASE_URL) -> list[SearchHit]:
    root = ET.fromstring(xml_text)
    hits = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title = _text(entry.find(f"{ATOM_NS}title")) or "(untitled)"
        link = ""
        for lk in entry.findall(f"{ATOM_NS}link"):
            if lk.get("rel") in (None, "alternate"):
                link = lk.get("href", "")
                break
        date = _text(entry.find(f"{ATOM_NS}published")) or _text(entry.find(f"{ATOM_NS}updated"))
        uri = _uri_from_link(link, base_url)
        if uri:
            hits.append(SearchHit(title=title, uri=uri, link=link, date=date))
    return hits


def _uri_from_link(link: str, base_url: str) -> str | None:
    if not link:
        return None
    path = link.split("://", 1)[-1]
    path = path.split("/", 1)[1] if "/" in path else ""
    path = path.split("?")[0].strip("/")
    if path.endswith("/data.xml"):
        path = path[: -len("/data.xml")]
    return path or None


def parse_judgment_metadata(xml_text: str) -> tuple[str | None, str | None, str | None]:
    """Return (case name, neutral citation, judgment date) from AKN XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, None, None
    name_el = root.find(f".//{AKN_NS}FRBRname")
    name = name_el.get("value") if name_el is not None else None
    cite_el = root.find(f".//{UK_NS}cite")
    cite = _text(cite_el)
    date_el = root.find(f".//{AKN_NS}FRBRWork/{AKN_NS}FRBRdate")
    date = date_el.get("date") if date_el is not None else None
    return name, cite, date


def extract_citation_contexts(xml_text: str, target: NeutralCitation,
                              max_contexts: int = 3, window: int = 1) -> list[str]:
    """Return passages of the judgment surrounding references to `target`.

    Matches either the citation text (spacing-tolerant) or an enriched
    <ref> whose href points at the target's FCL URI.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    pattern = target.context_regex()
    uri = target.fcl_uri

    paras: list[tuple[str, bool]] = []  # (flattened text, matches target)
    for el in root.iter():
        if el.tag not in (f"{AKN_NS}p", f"{AKN_NS}block"):
            continue
        text = _WS.sub(" ", "".join(el.itertext())).strip()
        if not text:
            continue
        matched = bool(pattern.search(text))
        if not matched and uri:
            for ref in el.iter(f"{AKN_NS}ref"):
                href = ref.get("href", "")
                if href.rstrip("/").endswith("/" + uri):
                    matched = True
                    break
        paras.append((text, matched))

    match_idx = [i for i, (_, m) in enumerate(paras) if m]
    if not match_idx:
        return []

    # Merge overlapping ±window ranges around each match, keep the first few.
    ranges: list[list[int]] = []
    for i in match_idx:
        lo, hi = max(0, i - window), min(len(paras) - 1, i + window)
        if ranges and lo <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], hi)
        else:
            ranges.append([lo, hi])
    contexts = ["\n".join(paras[i][0] for i in range(lo, hi + 1)) for lo, hi in ranges]
    return contexts[:max_contexts]


def _text(el) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None
