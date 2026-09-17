from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SiteConfig:
    id: str
    name: str
    adapter: str
    base_url: str = ""
    enabled: bool = True
    priority: int = 0
    fixture_path: str | None = None
    timeout: float | None = None
    retries: int | None = None
    rate_limit: float = 0.0
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    output_dir: str
    timeout: float = 10.0
    retries: int = 1
    max_response_bytes: int = 10 * 1024 * 1024
    sites: tuple[SiteConfig, ...] = ()


@dataclass(frozen=True)
class SongCandidate:
    site_id: str
    site_name: str
    title: str
    artist: str = ""
    album: str = ""
    duration: str = ""
    details_url: str = ""
    adapter_ref: str = ""
    match_score: float = 0.0

    def to_dict(self, index: int | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "site": self.site_name,
            "site_id": self.site_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration,
            "details_url": self.details_url,
            "adapter_ref": self.adapter_ref,
            "match_score": self.match_score,
        }
        if index is not None:
            result = {"index": index, **result}
        return result


@dataclass(frozen=True)
class SongDetails:
    candidate: SongCandidate
    lyrics: LyricsResource | None = None
    audio: AudioResource | None = None


@dataclass(frozen=True)
class LyricsResource:
    content: str
    extension: str = ".lrc"
    encoding: str = "utf-8"


@dataclass(frozen=True)
class AudioResource:
    data: bytes | None = None
    url: str | None = None
    extension: str = ".mp3"
    content_type: str = "audio/mpeg"


@dataclass(frozen=True)
class SiteFailure:
    site_id: str
    category: str
    message: str


@dataclass
class SearchReport:
    candidates: list[SongCandidate] = field(default_factory=list)
    failures: list[SiteFailure] = field(default_factory=list)
    no_result_sites: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DownloadResult:
    candidate: SongCandidate
    lyrics_path: str | None
    audio_path: str | None
    metadata_path: str | None
    partial: bool


@dataclass(frozen=True)
class DirectDownloadResult:
    url: str
    path: str
    bytes_written: int


@dataclass(frozen=True)
class ProviderTestResult:
    provider_id: str
    query: str
    candidate: SongCandidate | None
    audio_available: bool
    audio_downloaded: bool
    audio_bytes: int | None
    error: str | None = None
