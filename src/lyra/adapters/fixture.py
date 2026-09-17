from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from ..errors import SiteError
from ..http import HttpClient
from ..models import AudioResource, LyricsResource, SiteConfig, SongCandidate, SongDetails
from .base import SiteAdapter


class _FixtureParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.details: dict[str, str] = {}
        self._current: dict[str, str] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs):
        values = dict(attrs)
        if tag == "article" and values.get("class") == "song-result":
            self._current = dict(values)
            self._field = None
        elif self._current is not None and tag == "span" and values.get("class") in {"title", "artist", "album", "duration"}:
            self._field = values["class"]
        elif tag == "div" and values.get("id") == "lyrics":
            self._field = "lyrics"
            self.details["lyrics_extension"] = values.get("data-format", "txt")
        elif tag == "a" and values.get("id") == "audio":
            self.details["audio"] = values.get("href", "")
            self.details["audio_extension"] = values.get("data-extension", "mp3")

    def handle_endtag(self, tag: str):
        if tag == "article" and self._current is not None:
            self.results.append(self._current)
            self._current = None
            self._field = None
        elif tag in {"span", "div"}:
            self._field = None

    def handle_data(self, data: str):
        text = " ".join(data.split())
        if not text:
            return
        if self._current is not None and self._field:
            self._current[self._field] = (self._current.get(self._field, "") + " " + text).strip()
        elif self._field == "lyrics":
            self.details["lyrics"] = self.details.get("lyrics", "") + data


class FixtureAdapter(SiteAdapter):
    adapter_id = "fixture"

    def _root(self, site: SiteConfig) -> Path:
        if not site.fixture_path:
            raise SiteError(site.id, "configuration", "fixture adapter requires fixture_path")
        root = Path(site.fixture_path).expanduser()
        if not root.is_dir():
            raise SiteError(site.id, "configuration", f"fixture path is not a directory: {root}")
        return root

    def _parse(self, path: Path, site: SiteConfig) -> _FixtureParser:
        try:
            parser = _FixtureParser()
            parser.feed(path.read_text(encoding="utf-8"))
            parser.close()
            return parser
        except (OSError, UnicodeError) as exc:
            raise SiteError(site.id, "parser", f"cannot parse fixture: {path.name}") from exc

    def _inside_root(self, root: Path, relative_name: str, site: SiteConfig) -> Path:
        root = root.resolve()
        candidate = (root / unquote(relative_name)).resolve()
        if not candidate.is_relative_to(root):
            raise SiteError(site.id, "security", "fixture resource escapes configured fixture path")
        return candidate

    def search(self, query: str, site: SiteConfig, client: HttpClient) -> list[SongCandidate]:
        root = self._root(site)
        parser = self._parse(root / "search.html", site)
        normalized_query = " ".join(query.casefold().split())
        candidates: list[SongCandidate] = []
        for item in parser.results:
            haystack = " ".join(item.get(key, "") for key in ("title", "artist", "album")).casefold()
            if normalized_query not in haystack:
                continue
            candidates.append(SongCandidate(
                site_id=site.id,
                site_name=site.name,
                title=item.get("title", "Untitled"),
                artist=item.get("artist", ""),
                album=item.get("album", ""),
                duration=item.get("duration", ""),
                details_url=f"fixture://{site.id}/{item.get('data-id', '')}",
                adapter_ref=item.get("data-id", ""),
                match_score=1.0 if normalized_query in item.get("title", "").casefold() else 0.5,
            ))
        return candidates

    def get_details(self, candidate: SongCandidate, site: SiteConfig, client: HttpClient) -> SongDetails:
        root = self._root(site)
        parser = self._parse(root / f"details_{candidate.adapter_ref}.html", site)
        lyrics = None
        if parser.details.get("lyrics"):
            extension = parser.details.get("lyrics_extension", "txt").lower()
            lyrics = LyricsResource(parser.details["lyrics"].strip() + "\n", f".{extension.lstrip('.')}")
        audio = None
        audio_name = parser.details.get("audio")
        if audio_name:
            audio_path = self._inside_root(root, audio_name, site)
            try:
                audio_data = audio_path.read_bytes()
            except OSError as exc:
                raise SiteError(site.id, "resource", f"cannot read audio fixture: {audio_name}") from exc
            extension = parser.details.get("audio_extension", audio_path.suffix.lstrip(".")) or "bin"
            audio = AudioResource(data=audio_data, extension=f".{extension.lstrip('.')}")
        return SongDetails(candidate=candidate, lyrics=lyrics, audio=audio)
