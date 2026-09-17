from __future__ import annotations

from abc import ABC, abstractmethod

from ..http import HttpClient
from ..models import LyricsResource, SiteConfig, SongCandidate, SongDetails


class SiteAdapter(ABC):
    """Site-specific boundary; the application layer only uses these methods."""

    adapter_id: str

    @abstractmethod
    def search(self, query: str, site: SiteConfig, client: HttpClient) -> list[SongCandidate]:
        raise NotImplementedError

    @abstractmethod
    def get_details(self, candidate: SongCandidate, site: SiteConfig, client: HttpClient) -> SongDetails:
        raise NotImplementedError

    def get_lyrics(self, details: SongDetails, site: SiteConfig, client: HttpClient) -> LyricsResource | None:
        return details.lyrics

    def get_audio(self, details: SongDetails, site: SiteConfig, client: HttpClient):
        return details.audio
