from __future__ import annotations

from dataclasses import dataclass, replace

from .adapters.registry import AdapterRegistry
from .errors import LyraError, SiteError
from .http import HttpClient
from .models import AppConfig, DownloadResult, ProviderTestFailure, ProviderTestResult, SearchReport, SiteFailure, SongCandidate
from .storage import save_download


@dataclass
class SearchService:
    config: AppConfig
    registry: AdapterRegistry

    def search(self, query: str) -> SearchReport:
        if not query.strip():
            raise LyraError("search query must not be empty")
        client = HttpClient(self.config.timeout, self.config.retries, self.config.max_response_bytes)
        report = SearchReport()
        for site in sorted((s for s in self.config.sites if s.enabled), key=lambda item: -item.priority):
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
        site = next((site for site in self.config.sites if site.id == candidate.site_id and site.enabled), None)
        if site is None:
            raise LyraError(f"site is not enabled: {candidate.site_id}")
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
class ProviderTestService:
    config: AppConfig
    registry: AdapterRegistry

    def test(self, provider_id: str, query: str, result_index: int = 1, download_audio: bool = False) -> ProviderTestResult:
        site = next((site for site in self.config.sites if site.id == provider_id and site.enabled), None)
        if site is None:
            raise LyraError(f"enabled provider not found: {provider_id}")
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

    def test_many(self, provider_ids: list[str], query: str, result_index: int = 1, download_audio: bool = False) -> tuple[list[ProviderTestResult], list[ProviderTestFailure]]:
        results: list[ProviderTestResult] = []
        failures: list[ProviderTestFailure] = []
        for provider_id in provider_ids:
            try:
                results.append(self.test(provider_id, query, result_index, download_audio))
            except LyraError as exc:
                failures.append(ProviderTestFailure(provider_id, str(exc)))
        return results, failures
