# -*- coding: utf-8 -*-

from dataclasses import dataclass
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests

from magazarr.version import get_version

USER_AGENT = f"Magazarr/{get_version()}"
TIMEOUT = 60
SEARCH_PAGE_LIMIT = 100


@dataclass(frozen=True)
class QuasarrResult:
    title: str
    download_url: str
    pub_date: str
    size_bytes: int
    source: str


class QuasarrClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def search(
        self,
        query: str,
        category: str,
        on_page=None,
        page_limit: int = SEARCH_PAGE_LIMIT,
    ) -> list[QuasarrResult]:
        results = []
        offset = 0
        while True:
            page = self.search_page(query, category, offset, page_limit)
            if on_page:
                on_page(offset, page)
            results.extend(page)
            if len(page) < page_limit:
                return results
            offset += page_limit

    def search_page(
        self,
        query: str,
        category: str,
        offset: int = 0,
        limit: int = SEARCH_PAGE_LIMIT,
    ) -> list[QuasarrResult]:
        response = self.session.get(
            urljoin(self.base_url, "api"),
            params={
                "t": "search",
                "q": query,
                "cat": category,
                "offset": offset,
                "limit": limit,
                "apikey": self.api_key,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return parse_search_results(response.text)

    def add_url(self, download_url: str, category: str) -> list[str]:
        response = self.session.get(
            urljoin(self.base_url, "api"),
            params={
                "mode": "addurl",
                "name": download_url,
                "cat": category,
                "apikey": self.api_key,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("status"):
            return []
        return [str(item) for item in data.get("nzo_ids", []) if item]

    def history(self) -> list[dict]:
        response = self.session.get(
            urljoin(self.base_url, "api"),
            params={"mode": "history", "apikey": self.api_key},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return list(data.get("history", {}).get("slots", []))

    def queue(self) -> list[dict]:
        response = self.session.get(
            urljoin(self.base_url, "api"),
            params={"mode": "queue", "apikey": self.api_key},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return list(data.get("queue", {}).get("slots", []))

    def delete_package(self, package_id: str, title: str = "") -> bool:
        response = self.session.get(
            urljoin(self.base_url, "api"),
            params={
                "mode": "queue",
                "name": "delete",
                "value": package_id,
                "title": title,
                "apikey": self.api_key,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return bool(data.get("status"))


def parse_search_results(xml_text: str) -> list[QuasarrResult]:
    root = ElementTree.fromstring(xml_text)
    results = []
    for item in root.findall(".//item"):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        pub_date = _child_text(item, "pubDate")
        source = _child_text(item, "comments")
        enclosure = item.find("enclosure")
        size_bytes = 0
        if enclosure is not None:
            try:
                size_bytes = int(enclosure.attrib.get("length", "0") or 0)
            except ValueError:
                size_bytes = 0
        if title and link and title != "No results found":
            results.append(
                QuasarrResult(
                    title=title,
                    download_url=link,
                    pub_date=pub_date,
                    size_bytes=size_bytes,
                    source=source,
                )
            )
    return results


def _child_text(item, name: str) -> str:
    child = item.find(name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()
