from __future__ import annotations

from dataclasses import dataclass, replace
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .adapters.registry import AdapterRegistry
from .errors import LyraError, SiteError
from .http import HttpClient
from .models import AppConfig, DirectDownloadResult, DownloadResult, ProviderTestResult, ResolvedAudioResult, SearchReport, SiteFailure, SongCandidate, SiteConfig
from .storage import safe_component, save_audio_bytes, save_download


def normalize_direct_url(url: str) -> str:
    """Accept common shell-escaped query delimiters copied into a URL argument."""
    return re.sub(r"\\([?=&])", r"\1", url.strip())


def enabled_provider(config: AppConfig, provider_id: str) -> SiteConfig:
    site = next((site for site in config.sites if site.id == provider_id and site.enabled), None)
    if site is None:
        raise LyraError(f"enabled provider not found: {provider_id}")
    return site


@dataclass
class SearchService:
    config: AppConfig
    registry: AdapterRegistry

    def search(self, query: str, provider_id: str | None = None) -> SearchReport:
        if not query.strip():
            raise LyraError("search query must not be empty")
        client = HttpClient(self.config.timeout, self.config.retries, self.config.max_response_bytes)
        report = SearchReport()
        sites = (enabled_provider(self.config, provider_id),) if provider_id is not None else tuple(
            site for site in self.config.sites if site.enabled
        )
        for site in sorted(sites, key=lambda item: -item.priority):
            try:
                candidates = self.registry.get(site.adapter).search(query, site, client)
                if not candidates:
                    report.no_result_sites.append(site.id)
                report.candidates.extend(candidates)
            except SiteError as exc:
                report.failures.append(SiteFailure(site.id, exc.category, str(exc)))
        report.candidates.sort(key=lambda item: (-next(s.priority for s in self.config.sites if s.id == item.site_id), -item.match_score, item.site_id, item.title.casefold()))
        return report


@dataclass
class DownloadService:
    config: AppConfig
    registry: AdapterRegistry

    def download(self, candidate: SongCandidate, output_dir: str | None = None, overwrite: bool = False) -> DownloadResult:
        site = enabled_provider(self.config, candidate.site_id)
        client = HttpClient(self.config.timeout, self.config.retries, self.config.max_response_bytes)
        adapter = self.registry.get(site.adapter)
        details = adapter.get_details(candidate, site, client)
        lyrics = adapter.get_lyrics(details, site, client)
        audio = adapter.get_audio(details, site, client)
        if audio is not None and audio.data is None and audio.url:
            audio = replace(audio, data=client.fetch_bytes(audio.url, site, accept=audio.content_type))
        if lyrics is None and audio is None:
            raise LyraError("selected result has no downloadable lyrics or audio")
        return save_download(
            details,
            lyrics,
            audio,
            output_dir or self.config.output_dir,
            overwrite=overwrite,
        )


@dataclass
class DirectDownloadService:
    config: AppConfig

    def download(
        self,
        url: str,
        output_dir: str | None = None,
        filename: str | None = None,
        overwrite: bool = False,
    ) -> DirectDownloadResult:
        url = normalize_direct_url(url)
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise LyraError("direct download URL must be an HTTPS URL")
        site = SiteConfig(id="direct-url", name="Direct URL", adapter="direct", base_url=f"https://{parsed.netloc}")
        data = HttpClient(self.config.timeout, self.config.retries, self.config.max_response_bytes).fetch_bytes(
            url, site, accept="audio/*,application/octet-stream"
        )
        extension = self._extension(parsed.path)
        target_name = filename or Path(unquote(parsed.path)).name or f"audio{extension}"
        target_name = safe_component(target_name, f"audio{extension}")
        if "." not in target_name.rsplit("/", 1)[-1]:
            target_name = f"{target_name}{extension}"
        directory = Path(output_dir or self.config.output_dir).expanduser()
        if directory.exists() and not directory.is_dir():
            raise LyraError(f"output path is not a directory: {directory}")
        path = save_audio_bytes(data, str(directory), target_name, overwrite)
        return DirectDownloadResult(url=url, path=str(path), bytes_written=len(data))

    @staticmethod
    def _extension(path: str) -> str:
        suffix = Path(unquote(path)).suffix
        return suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix) else ".audio"


@dataclass
class ResolveService:
    config: AppConfig
    registry: AdapterRegistry

    def resolve(self, query: str, result_index: int = 1) -> ResolvedAudioResult:
        report = SearchService(self.config, self.registry).search(query)
        if not report.candidates:
            raise LyraError(f"no results found for query: {query}")
        if result_index < 1 or result_index > len(report.candidates):
            raise LyraError(f"result must be between 1 and {len(report.candidates)}")
        candidate = report.candidates[result_index - 1]
        site = next((site for site in self.config.sites if site.id == candidate.site_id and site.enabled), None)
        if site is None:
            raise LyraError(f"site is not enabled: {candidate.site_id}")
        client = HttpClient(self.config.timeout, self.config.retries, self.config.max_response_bytes)
        adapter = self.registry.get(site.adapter)
        details = adapter.get_details(candidate, site, client)
        audio = adapter.get_audio(details, site, client)
        if audio is None:
            raise LyraError(f"provider has no audio resource: {candidate.site_id}")
        if not audio.url:
            raise LyraError(f"provider has no direct audio URL: {candidate.site_id}")
        return ResolvedAudioResult(candidate=candidate, url=normalize_direct_url(audio.url), content_type=audio.content_type)


@dataclass
class ProviderTestService:
    config: AppConfig
    registry: AdapterRegistry

    def test(self, provider_id: str, query: str, result_index: int = 1, download_audio: bool = False) -> ProviderTestResult:
        site = enabled_provider(self.config, provider_id)
        client = HttpClient(self.config.timeout, self.config.retries, self.config.max_response_bytes)
        adapter = self.registry.get(site.adapter)
        candidates = adapter.search(query, site, client)
        if not candidates:
            raise LyraError(f"provider returned no results for query: {query}")
        if result_index < 1 or result_index > len(candidates):
            raise LyraError(f"result must be between 1 and {len(candidates)}")
        candidate = candidates[result_index - 1]
        details = adapter.get_details(candidate, site, client)
        audio = adapter.get_audio(details, site, client)
        if audio is None:
            return ProviderTestResult(provider_id, query, candidate, False, False, None, "audio resource unavailable")
        if not download_audio:
            return ProviderTestResult(provider_id, query, candidate, True, False, len(audio.data) if audio.data is not None else None)
        if audio.data is not None:
            return ProviderTestResult(provider_id, query, candidate, True, True, len(audio.data))
        if not audio.url:
            return ProviderTestResult(provider_id, query, candidate, True, False, None, "audio resource has no URL")
        data = client.fetch_bytes(audio.url, site, accept=audio.content_type)
        return ProviderTestResult(provider_id, query, candidate, True, True, len(data))
