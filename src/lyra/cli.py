from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .adapters.registry import default_registry
from .application import DirectDownloadService, DownloadService, ProviderTestService, ResolveService, SearchService, enabled_provider
from .config import add_provider, delete_provider, default_config_path, init_config, load_config
from .errors import ConfigError, LyraError, NoResultsError, SelectionError
from .models import AppConfig, SearchReport, SongCandidate
from .storage import read_search_cache, write_search_cache

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_NO_RESULTS = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lyra", description="Search configured music sites and download authorized lyrics/audio.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    config_parser = subparsers.add_parser("config", help="validate configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_validate = config_subparsers.add_parser("validate", help="validate a TOML configuration")
    config_validate.add_argument("--config", type=Path, default=default_config_path())

    init = subparsers.add_parser("init", help="create Lyra user configuration")
    init_subparsers = init.add_subparsers(dest="init_command", required=True)
    init_config_parser = init_subparsers.add_parser("config", help="create a starter TOML configuration")
    init_config_parser.add_argument("--config", type=Path, default=default_config_path())
    init_config_parser.add_argument("--force", action="store_true", help="replace an existing configuration")

    provider = subparsers.add_parser("provider", help="manage configured website providers")
    provider_subparsers = provider.add_subparsers(dest="provider_command", required=True)
    provider_add = provider_subparsers.add_parser("add", help="add a configured website provider")
    provider_add.add_argument("website_url", nargs="?", help="HTTPS website URL used to derive provider defaults")
    provider_add.add_argument("--config", type=Path, default=default_config_path())
    provider_add.add_argument("--adapter", choices=sorted(default_registry().ids), default="html", help="provider adapter")
    provider_add.add_argument("--id", dest="provider_id", help="override the ID derived from the URL")
    provider_add.add_argument("--name", help="override the name derived from the URL")
    provider_add.add_argument("--base-url", help="override the base URL derived from the URL")
    provider_add.add_argument("--search-path", help="URL path containing {query}")
    provider_add.add_argument("--result-selector")
    provider_add.add_argument("--title-selector")
    provider_add.add_argument("--details-selector")
    provider_add.add_argument("--artist-selector")
    provider_add.add_argument("--album-selector")
    provider_add.add_argument("--duration-selector")
    provider_add.add_argument("--lyrics-selector")
    provider_add.add_argument("--lyrics-attr")
    provider_add.add_argument("--lyrics-extension", default="lrc")
    provider_add.add_argument("--audio-selector")
    provider_add.add_argument("--audio-attr", default="href")
    provider_add.add_argument("--audio-extension", default="mp3")
    provider_add.add_argument("--priority", type=int, default=0)
    provider_add.add_argument("--rate-limit", type=float, default=0.0, help="seconds between requests")
    provider_add.add_argument("--access-mode", choices=("http", "browser"), default="http", help="provider access transport")
    browser_visibility = provider_add.add_mutually_exclusive_group()
    browser_visibility.add_argument("--browser-headless", action="store_true", help="run browser access without a visible window (default)")
    browser_visibility.add_argument("--browser-visible", action="store_true", help="open a visible browser for manual interaction")
    provider_add.add_argument("--browser-endpoint", help="credential-free CDP endpoint for a user-launched browser")
    provider_add.add_argument("--quality", choices=("96", "192"), default="96", help="24bit audio quality")
    provider_add.add_argument("--force", action="store_true", help="replace an existing provider")

    provider_list = provider_subparsers.add_parser("list", help="list configured website providers")
    provider_list.add_argument("--config", type=Path, default=default_config_path())
    provider_list.add_argument("--json", action="store_true", dest="as_json")

    provider_delete = provider_subparsers.add_parser("delete", help="delete a configured website provider")
    provider_delete.add_argument("provider_id")
    provider_delete.add_argument("--config", type=Path, default=default_config_path())

    provider_test = provider_subparsers.add_parser("test", help="test a provider and optionally download one audio resource")
    provider_test.add_argument("provider_id")
    provider_test.add_argument("--query", required=True, nargs="+")
    provider_test.add_argument("--config", type=Path, default=default_config_path())
    provider_test.add_argument("--result", type=int, default=1)
    provider_test.add_argument("--audio", action="store_true", help="download bytes to verify audio access")
    provider_test.add_argument("--json", action="store_true", dest="as_json")

    search = subparsers.add_parser("search", help="search enabled music sites")
    search.add_argument("query")
    search.add_argument("--provider", dest="provider_id", help="search only this configured provider")
    search.add_argument("--config", type=Path, default=default_config_path())
    search.add_argument("--json", action="store_true", dest="as_json")
    search.add_argument("--verbose", action="store_true")

    resolve = subparsers.add_parser("resolve", help="resolve a song query to a direct audio URL")
    resolve.add_argument("query")
    resolve.add_argument("--config", type=Path, default=default_config_path())
    resolve.add_argument("--result", type=int, default=1, help="one-based result index")
    resolve.add_argument("--json", action="store_true", dest="as_json")

    download = subparsers.add_parser("download", help="search and download a song, or download a cached result")
    download.add_argument("query", nargs="?")
    download.add_argument("--provider", dest="provider_id", help="search or select cached results from this provider")
    download.add_argument("--config", type=Path, default=default_config_path())
    download.add_argument("--result", type=int, help="one-based cached result index")
    download.add_argument("--output", type=str, help="output directory; defaults to settings.output_dir")
    download.add_argument("--overwrite", action="store_true")
    download.add_argument("--json", action="store_true", dest="as_json")

    direct = subparsers.add_parser("download-url", help="download an authorized HTTPS audio URL directly")
    direct.add_argument("url")
    direct.add_argument("--config", type=Path, default=default_config_path(), help="TOML configuration path")
    direct.add_argument("--output", type=str, help="output directory; defaults to settings.output_dir")
    direct.add_argument("--filename", type=str, help="optional filename; defaults to the URL path filename")
    direct.add_argument("--overwrite", action="store_true")
    direct.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _load(path: Path):
    registry = default_registry()
    return load_config(path, registry.ids), registry


def _load_download_config(path: Path) -> AppConfig:
    if path.exists():
        return _load(path)[0]
    return AppConfig(output_dir="./downloads")


def _print_report(report: SearchReport, as_json: bool) -> None:
    if as_json:
        payload = {
            "candidates": [candidate.to_dict(index=i) for i, candidate in enumerate(report.candidates, start=1)],
            "failures": [failure.__dict__ for failure in report.failures],
            "no_result_sites": report.no_result_sites,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not report.candidates:
        print("No results found.")
    for index, candidate in enumerate(report.candidates, start=1):
        artist = f" — {candidate.artist}" if candidate.artist else ""
        album = f" [{candidate.album}]" if candidate.album else ""
        duration = f" ({candidate.duration})" if candidate.duration else ""
        print(f"{index}. {candidate.title}{artist}{album}{duration}  [{candidate.site_name}]")
    for failure in report.failures:
            print(f"Site {failure.site_id} failed ({failure.category}): {failure.message}", file=sys.stderr)


def _provider_options(args: argparse.Namespace) -> dict[str, str]:
    option_names = (
        "search_path", "result_selector", "title_selector", "details_selector", "artist_selector",
        "album_selector", "duration_selector", "lyrics_selector", "lyrics_attr", "lyrics_extension",
        "audio_selector", "audio_attr", "audio_extension",
    )
    return {name: getattr(args, name) for name in option_names if getattr(args, name) is not None}


def _provider_defaults(website_url: str) -> tuple[str, str, str, dict[str, str]]:
    parsed = urlparse(website_url)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise ConfigError("website URL must be an HTTPS URL")
    host_parts = [part for part in parsed.hostname.lower().split(".") if part and part != "www"]
    domain_parts = host_parts[:-1] if len(host_parts) > 1 else host_parts
    provider_id = re.sub(r"[^a-z0-9]+", "-", "-".join(domain_parts)).strip("-")
    if not provider_id:
        raise ConfigError("website URL does not contain a usable provider ID")
    provider_name = " ".join(part.capitalize() for part in domain_parts) or provider_id
    base_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    options = {
        "search_path": "/search?q={query}",
        "result_selector": "article.song, article.track, .song-result, .track-result",
        "title_selector": ".title, .song-title, [data-title], h1, h2",
        "details_selector": "a.details, a.track, a.song, a[href]",
        "artist_selector": ".artist, .artist-name, [data-artist]",
        "album_selector": ".album, .album-name, [data-album]",
        "duration_selector": ".duration, [data-duration]",
        "lyrics_selector": "#lyrics, .lyrics, [data-lyrics]",
        "lyrics_extension": "lrc",
        "audio_selector": "audio, audio source, a.audio, a[href$='.mp3'], a[href$='.m4a'], a[href$='.ogg'], a[href$='.wav']",
        "audio_attr": "href",
        "audio_extension": "mp3",
    }
    return provider_id, provider_name, base_url, options


def _print_provider_test(result, as_json: bool, downloaded: bool) -> int:
    candidate = result.candidate.to_dict() if result.candidate else None
    payload = {
        "provider_id": result.provider_id,
        "query": result.query,
        "candidate": candidate,
        "audio_available": result.audio_available,
        "audio_downloaded": result.audio_downloaded,
        "audio_bytes": result.audio_bytes,
        "error": result.error,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        selected = result.candidate.title if result.candidate else "none"
        print(f"Provider: {result.provider_id}")
        print(f"Selected: {selected}")
        print(f"Audio available: {'yes' if result.audio_available else 'no'}")
        if downloaded:
            print(f"Audio downloaded: {'yes' if result.audio_downloaded else 'no'}")
        if result.audio_bytes is not None:
            print(f"Audio bytes: {result.audio_bytes}")
        if result.error:
            print(f"Error: {result.error}", file=sys.stderr)
    passed = result.audio_downloaded if downloaded else result.audio_available
    return EXIT_OK if passed else EXIT_RUNTIME


def _print_provider_list(config, as_json: bool) -> int:
    providers = [{
        "id": site.id,
        "name": site.name,
        "adapter": site.adapter,
        "access_mode": site.access_mode,
        "base_url": site.base_url,
        "enabled": site.enabled,
        "priority": site.priority,
    } for site in config.sites]
    if as_json:
        print(json.dumps(providers, ensure_ascii=False, indent=2))
    else:
        if not providers:
            print("No providers configured.")
        else:
            print("ID\tNAME\tADAPTER\tACCESS\tENABLED\tBASE URL")
            for provider in providers:
                print(f"{provider['id']}\t{provider['name']}\t{provider['adapter']}\t{provider['access_mode']}\t{str(provider['enabled']).lower()}\t{provider['base_url']}")
    return EXIT_OK


def _select(candidates: list[SongCandidate], requested: int | None, auto_select: bool = False) -> SongCandidate:
    if requested is not None:
        if requested < 1 or requested > len(candidates):
            raise SelectionError(f"result must be between 1 and {len(candidates)}")
        return candidates[requested - 1]
    if auto_select:
        return candidates[0]
    if not sys.stdin.isatty():
        raise SelectionError("non-interactive download requires --result")
    try:
        answer = input("Select a result number: ").strip()
        return _select(candidates, int(answer))
    except (ValueError, EOFError) as exc:
        raise SelectionError("please select a result number") from exc


def _run(args: argparse.Namespace) -> int:
    if args.command == "init":
        path = init_config(args.config, force=args.force)
        print(f"Initialized configuration: {path}")
        return EXIT_OK
    if args.command == "config":
        _load(args.config)
        print(f"Configuration is valid: {args.config}")
        return EXIT_OK

    if args.command == "download-url":
        result = DirectDownloadService(_load_download_config(args.config)).download(
            args.url, args.output, args.filename, args.overwrite
        )
        if args.as_json:
            print(json.dumps({"url": result.url, "path": result.path, "bytes": result.bytes_written}, ensure_ascii=False, indent=2))
        else:
            print(f"Downloaded {result.bytes_written} bytes to {result.path}")
        return EXIT_OK

    if args.command == "resolve":
        config, registry = _load(args.config)
        result = ResolveService(config, registry).resolve(args.query, args.result)
        if args.as_json:
            print(json.dumps({
                "url": result.url,
                "content_type": result.content_type,
                "candidate": result.candidate.to_dict(),
            }, ensure_ascii=False, indent=2))
        else:
            print(result.url)
        return EXIT_OK

    if args.command == "provider":
        if args.provider_command == "add":
            website_url = args.website_url or args.base_url
            if not website_url:
                raise ConfigError("provide a website URL")
            default_id, default_name, default_base_url, default_options = _provider_defaults(website_url)
            provider_id = args.provider_id or default_id
            provider_name = args.name or default_name
            base_url = args.base_url or default_base_url
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", provider_id):
                raise ConfigError("provider id must contain lowercase letters, digits, '-' or '_'")
            if args.adapter == "html" and "{query}" not in (args.search_path or default_options["search_path"]):
                raise ConfigError("search path must contain {query}")
            parsed_base_url = urlparse(base_url)
            if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
                raise ConfigError("base URL must be an HTTPS URL")
            if args.rate_limit < 0:
                raise ConfigError("rate limit must be non-negative")
            if args.adapter != "24bit" and args.quality != "96":
                raise ConfigError("--quality requires --adapter 24bit")
            if (args.browser_headless or args.browser_visible) and args.access_mode != "browser":
                raise ConfigError("browser visibility flags require --access-mode browser")
            if args.browser_endpoint:
                endpoint = urlparse(args.browser_endpoint)
                if args.access_mode != "browser":
                    raise ConfigError("--browser-endpoint requires --access-mode browser")
                if endpoint.scheme not in {"http", "https", "ws", "wss"} or not endpoint.netloc or endpoint.username or endpoint.password:
                    raise ConfigError("--browser-endpoint must be a credential-free CDP URL")
            provider = {
                "id": provider_id,
                "name": provider_name,
                "adapter": args.adapter,
                "access_mode": args.access_mode,
                "base_url": base_url,
                "enabled": True,
                "priority": args.priority,
                "rate_limit": args.rate_limit,
            }
            if args.adapter == "html":
                provider.update(default_options)
                provider.update(_provider_options(args))
            else:
                provider["quality"] = args.quality
            if args.browser_headless:
                provider["browser_headless"] = True
            if args.browser_visible:
                provider["browser_headless"] = False
            if args.browser_endpoint:
                provider["browser_endpoint"] = args.browser_endpoint
            add_provider(args.config, provider, force=args.force)
            print(f"Added provider: {provider_id}")
            return EXIT_OK
        if args.provider_command == "delete":
            delete_provider(args.config, args.provider_id)
            print(f"Deleted provider: {args.provider_id}")
            return EXIT_OK
        config, registry = _load(args.config)
        if args.provider_command == "list":
            return _print_provider_list(config, args.as_json)
        query = " ".join(args.query)
        service = ProviderTestService(config, registry)
        result = service.test(args.provider_id, query, args.result, args.audio)
        return _print_provider_test(result, args.as_json, args.audio)

    config, registry = _load(args.config)
    if args.command == "search":
        report = SearchService(config, registry).search(args.query, args.provider_id)
        write_search_cache(config.output_dir, report)
        _print_report(report, args.as_json)
        return EXIT_OK if report.candidates else EXIT_NO_RESULTS

    if args.query:
        report = SearchService(config, registry).search(args.query, args.provider_id)
        if not report.candidates:
            raise NoResultsError("no results found for query")
        write_search_cache(config.output_dir, report)
        candidates = report.candidates
    else:
        if args.provider_id is not None:
            enabled_provider(config, args.provider_id)
        candidates = read_search_cache(config.output_dir)
        if args.provider_id is not None:
            candidates = [candidate for candidate in candidates if candidate.site_id == args.provider_id]
            if not candidates:
                raise NoResultsError(f"no cached results for provider: {args.provider_id}")
    candidate = _select(candidates, args.result, auto_select=bool(args.query))
    result = DownloadService(config, registry).download(candidate, args.output, args.overwrite)
    payload = {
        "lyrics_path": result.lyrics_path,
        "audio_path": result.audio_path,
        "metadata_path": result.metadata_path,
        "partial": result.partial,
        "site": result.candidate.site_name,
        "title": result.candidate.title,
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Saved: {result.candidate.title} [{result.candidate.site_name}]")
        for label, path in (("Lyrics", result.lyrics_path), ("Audio", result.audio_path), ("Metadata", result.metadata_path)):
            if path:
                print(f"{label}: {path}")
        if result.partial:
            print("Completed partially: one resource was unavailable.", file=sys.stderr)
    return EXIT_RUNTIME if result.partial else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except NoResultsError as exc:
        print(f"No results: {exc}", file=sys.stderr)
        return EXIT_NO_RESULTS
    except (SelectionError, LyraError, FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
