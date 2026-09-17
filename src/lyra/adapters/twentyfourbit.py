from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

from ..errors import SiteError
from ..http import HttpClient
from ..models import AudioResource, SiteConfig, SongCandidate, SongDetails
from .base import SiteAdapter


class TwentyFourBitAdapter(SiteAdapter):
    """Adapter for 24bit's public search and detail-page interfaces."""

    adapter_id = "24bit"
    search_endpoint = "/api/player/searchOnlineMusicOne"
    detail_paths = {"96": "/music/c/{id}", "192": "/music/a/{id}"}

    def _base_url(self, site: SiteConfig) -> str:
        if not site.base_url:
            raise SiteError(site.id, "configuration", "24bit adapter requires base_url")
        if not site.base_url.startswith("https://"):
            raise SiteError(site.id, "security", "24bit adapter only permits HTTPS URLs")
        return site.base_url.rstrip("/")

    def _quality(self, site: SiteConfig) -> str:
        quality = str(site.options.get("quality", "96"))
        if quality not in self.detail_paths:
            raise SiteError(site.id, "configuration", "24bit quality must be 96 or 192")
        return quality

    def search(self, query: str, site: SiteConfig, client: HttpClient) -> list[SongCandidate]:
        base_url = self._base_url(site)
        payload = {"keyword": quote(query, safe=""), "page": 1}
        response = client.post_json(f"{base_url}{self.search_endpoint}", payload, site)
        if response.get("status") is not True or not isinstance(response.get("result"), list):
            raise SiteError(site.id, "parser", "24bit search returned an unexpected response")
        detail_path = self.detail_paths[self._quality(site)]
        candidates: list[SongCandidate] = []
        for item in response["result"]:
            if not isinstance(item, dict):
                continue
            song_id = item.get("id")
            title = item.get("name")
            if not isinstance(song_id, str) or not song_id or not isinstance(title, str) or not title:
                continue
            artist = item.get("player", "")
            album = item.get("album", "")
            candidates.append(SongCandidate(
                site_id=site.id,
                site_name=site.name,
                title=title,
                artist=artist if isinstance(artist, str) else "",
                album=album if isinstance(album, str) else "",
                details_url=f"{base_url}{detail_path.format(id=song_id)}",
                adapter_ref=song_id,
                match_score=1.0 if title.casefold() == query.casefold() else 0.5,
            ))
        return candidates

    def get_details(self, candidate: SongCandidate, site: SiteConfig, client: HttpClient) -> SongDetails:
        try:
            html = client.fetch_text(candidate.details_url, site)
            lower_html = html.casefold()
            quota_markers = ("今日访问已达限额", "今日免费额度已用完", "免费额度", "需要注册")
            if any(marker in lower_html for marker in quota_markers):
                raise SiteError(site.id, "access", "24bit daily access quota is exhausted; wait until tomorrow or use the site's login flow")
            soup = BeautifulSoup(html, "html.parser")
            source = soup.select_one("audio source[src]")
        except SiteError:
            raise
        except Exception as exc:
            raise SiteError(site.id, "parser", "cannot parse 24bit detail HTML") from exc
        if source is None:
            raise SiteError(site.id, "parser", "24bit detail page has no audio source")
        audio_url = source.get("src")
        if not isinstance(audio_url, str) or not audio_url:
            raise SiteError(site.id, "parser", "24bit audio source has no URL")
        audio_url = urljoin(candidate.details_url, audio_url)
        if urlparse(audio_url).scheme != "https":
            raise SiteError(site.id, "security", "24bit audio source must use HTTPS")
        content_type = source.get("type") or "audio/flac"
        extension = Path(urlparse(audio_url).path).suffix or ".flac"
        return SongDetails(
            candidate=candidate,
            audio=AudioResource(url=audio_url, extension=extension, content_type=content_type),
        )
