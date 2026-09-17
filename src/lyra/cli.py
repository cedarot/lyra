from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters.registry import default_registry
from .application import DownloadService, SearchService
from .config import default_config_path, init_config, load_config
from .errors import ConfigError, LyraError, NoResultsError, SelectionError
from .models import SearchReport, SongCandidate
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

    search = subparsers.add_parser("search", help="search enabled music sites")
    search.add_argument("query")
    search.add_argument("--config", type=Path, default=default_config_path())
    search.add_argument("--json", action="store_true", dest="as_json")
    search.add_argument("--verbose", action="store_true")

    download = subparsers.add_parser("download", help="download the selected search result")
    download.add_argument("query", nargs="?")
    download.add_argument("--config", type=Path, default=default_config_path())
    download.add_argument("--result", type=int, help="one-based cached result index")
    download.add_argument("--output", type=str)
    download.add_argument("--overwrite", action="store_true")
    download.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _load(path: Path):
    registry = default_registry()
    return load_config(path, registry.ids), registry


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


def _select(candidates: list[SongCandidate], requested: int | None) -> SongCandidate:
    if requested is not None:
        if requested < 1 or requested > len(candidates):
            raise SelectionError(f"result must be between 1 and {len(candidates)}")
        return candidates[requested - 1]
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

    config, registry = _load(args.config)
    if args.command == "search":
        report = SearchService(config, registry).search(args.query)
        write_search_cache(config.output_dir, report)
        _print_report(report, args.as_json)
        return EXIT_OK if report.candidates else EXIT_NO_RESULTS

    if args.query:
        report = SearchService(config, registry).search(args.query)
        if not report.candidates:
            raise NoResultsError("no results found for query")
        write_search_cache(config.output_dir, report)
        candidates = report.candidates
    else:
        candidates = read_search_cache(config.output_dir)
    candidate = _select(candidates, args.result)
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
