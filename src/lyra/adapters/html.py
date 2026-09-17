from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..errors import SiteError
from ..http import HttpClient
from ..models import AudioResource, LyricsResource, SiteConfig, SongCandidate, SongDetails
from .base import SiteAdapter


class HtmlAdapter(SiteAdapter):
    """Config-driven adapter for sites that expose parseable HTML pages."""

    adapter_id = "html"

    def _option(self, site: SiteConfig, name: str, required: bool = False) -> str | None:
        value = site.options.get(name)
        if value is None or value == "":
            if required:
                raise SiteError(site.id, "configuration", f"html adapter requires option: {name}")
            return None
        if not isinstance(value, str):
            raise SiteError(site.id, "configuration", f"html adapter option must be a string: {name}")
        return value

    def _url(self, base_url: str, value: str, site: SiteConfig) -> str:
        result = urljoin(base_url.rstrip("/") + "/", value)
        if urlparse(result).scheme != "https":
            raise SiteError(site.id, "security", "html adapter only permits HTTPS URLs")
        return result

    def _select_one(self, parent: BeautifulSoup | Tag, selector: str, site: SiteConfig, field: str) -> Tag:
        try:
            selected = parent.select(selector)
        except Exception as exc:
            raise SiteError(site.id, "configuration", f"invalid CSS selector for {field}") from exc
        if not selected:
            raise SiteError(site.id, "parser", f"selector returned no element: {field}")
        return selected[0]

    @staticmethod
    def _text(node: Tag) -> str:
        return " ".join(node.get_text(" ", strip=True).split())

    def search(self, query: str, site: SiteConfig, client: HttpClient) -> list[SongCandidate]:
        search_path = self._option(site, "search_path", required=True)
        result_selector = self._option(site, "result_selector", required=True)
        title_selector = self._option(site, "title_selector", required=True)
        details_selector = self._option(site, "details_selector", required=True)
        search_url = self._url(site.base_url, search_path.replace("{query}", quote_plus(query)), site)
        try:
            soup = BeautifulSoup(client.fetch_text(search_url, site), "html.parser")
            results = soup.select(result_selector)
        except SiteError:
            raise
        except Exception as exc:
            raise SiteError(site.id, "parser", "cannot parse search HTML") from exc
        candidates: list[SongCandidate] = []
        for result in results:
            try:
                title = self._text(self._select_one(result, title_selector, site, "title_selector"))
                details_node = self._select_one(result, details_selector, site, "details_selector")
                details_href = details_node.get(self._option(site, "details_attr") or "href")
                if not isinstance(details_href, str) or not details_href:
                    raise SiteError(site.id, "parser", "details selector has no URL attribute")
                candidates.append(SongCandidate(
                    site_id=site.id,
                    site_name=site.name,
                    title=title,
                    artist=self._optional_text(result, site, "artist_selector"),
                    album=self._optional_text(result, site, "album_selector"),
                    duration=self._optional_text(result, site, "duration_selector"),
                    details_url=self._url(site.base_url, details_href, site),
                    adapter_ref=details_href,
                    match_score=1.0,
                ))
            except SiteError:
                raise
            except Exception as exc:
                raise SiteError(site.id, "parser", "cannot map a search result") from exc
        return candidates

    def _optional_text(self, parent: BeautifulSoup | Tag, site: SiteConfig, option: str) -> str:
        selector = self._option(site, option)
        if not selector:
            return ""
        try:
            selected = parent.select(selector)
        except Exception as exc:
            raise SiteError(site.id, "configuration", f"invalid CSS selector for {option}") from exc
        return self._text(selected[0]) if selected else ""

    def get_details(self, candidate: SongCandidate, site: SiteConfig, client: HttpClient) -> SongDetails:
        try:
            soup = BeautifulSoup(client.fetch_text(candidate.details_url, site), "html.parser")
        except SiteError:
            raise
        except Exception as exc:
            raise SiteError(site.id, "parser", "cannot parse details HTML") from exc
        lyrics = self._lyrics(soup, site)
        audio = self._audio(soup, candidate.details_url, site)
        return SongDetails(candidate=candidate, lyrics=lyrics, audio=audio)

    def _lyrics(self, soup: BeautifulSoup, site: SiteConfig) -> LyricsResource | None:
        selector = self._option(site, "lyrics_selector")
        if not selector:
            return None
        node = self._select_one(soup, selector, site, "lyrics_selector")
        attr = self._option(site, "lyrics_attr")
        content = node.get(attr) if attr else node.get_text("\n", strip=True)
        if not isinstance(content, str) or not content.strip():
            return None
        extension = self._option(site, "lyrics_extension") or "lrc"
        return LyricsResource(content.strip() + "\n", f".{extension.lstrip('.')}")

    def _audio(self, soup: BeautifulSoup, details_url: str, site: SiteConfig) -> AudioResource | None:
        selector = self._option(site, "audio_selector")
        if not selector:
            return None
        node = self._select_one(soup, selector, site, "audio_selector")
        attr = self._option(site, "audio_attr") or "href"
        value = node.get(attr)
        if not isinstance(value, str) or not value:
            raise SiteError(site.id, "parser", "audio selector has no URL attribute")
        url = self._url(details_url, value, site)
        extension = self._option(site, "audio_extension")
        if not extension:
            suffix = urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)
            extension = suffix[-1] if len(suffix) == 2 else "mp3"
        if not re.fullmatch(r"[A-Za-z0-9]{1,8}", extension.lstrip(".")):
            raise SiteError(site.id, "configuration", "audio_extension must be a simple file extension")
        return AudioResource(url=url, extension=f".{extension.lstrip('.')}")
