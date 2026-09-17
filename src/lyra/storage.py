from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path

from .models import AudioResource, DownloadResult, LyricsResource, SearchReport, SongCandidate, SongDetails


def safe_component(value: str, fallback: str = "untitled") -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    value = re.sub(r"\.{2,}", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or fallback)[:100]


def _target(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"file already exists: {path}")


def atomic_write(path: Path, data: bytes, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _target(path, overwrite)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
            temp_name = temp.name
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def write_search_cache(output_dir: str, report: SearchReport) -> Path:
    cache_path = Path(output_dir).expanduser() / ".lyra" / "search-results.json"
    payload = {
        "candidates": [candidate.to_dict(index=i) for i, candidate in enumerate(report.candidates, start=1)],
        "failures": [failure.__dict__ for failure in report.failures],
        "no_result_sites": report.no_result_sites,
    }
    atomic_write(cache_path, json.dumps(payload, ensure_ascii=False, indent=2).encode(), overwrite=True)
    return cache_path


def read_search_cache(output_dir: str) -> list[SongCandidate]:
    path = Path(output_dir).expanduser() / ".lyra" / "search-results.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [SongCandidate(
            site_id=item["site_id"], site_name=item["site"], title=item["title"], artist=item.get("artist", ""),
            album=item.get("album", ""), duration=item.get("duration", ""), details_url=item.get("details_url", ""),
            adapter_ref=item.get("adapter_ref", ""), match_score=float(item.get("match_score", 0.0)),
        ) for item in payload["candidates"]]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FileNotFoundError("no usable search cache; run lyra search first or provide a query") from exc


def save_download(
    details: SongDetails,
    lyrics: LyricsResource | None,
    audio: AudioResource | None,
    output_dir: str,
    *,
    overwrite: bool,
) -> DownloadResult:
    directory = Path(output_dir).expanduser()
    stem = safe_component(f"{details.candidate.artist} - {details.candidate.title}", "song")
    lyrics_path: Path | None = None
    audio_path: Path | None = None
    metadata_path = directory / f"{stem}.json"
    if lyrics is not None:
        lyrics_path = directory / f"{stem}{lyrics.extension if lyrics.extension.startswith('.') else '.' + lyrics.extension}"
    if audio is not None:
        audio_path = directory / f"{stem}{audio.extension if audio.extension.startswith('.') else '.' + audio.extension}"
        if audio.data is None:
            raise ValueError("remote audio resources are not implemented by the fixture-only MVP")
    for path in (lyrics_path, audio_path, metadata_path):
        if path is not None:
            _target(path, overwrite)
    if lyrics_path is not None and lyrics is not None:
        atomic_write(lyrics_path, lyrics.content.encode(lyrics.encoding), overwrite=True)
    if audio_path is not None and audio is not None and audio.data is not None:
        atomic_write(audio_path, audio.data, overwrite=True)
    metadata = {
        "site": details.candidate.site_name,
        "site_id": details.candidate.site_id,
        "title": details.candidate.title,
        "artist": details.candidate.artist,
        "album": details.candidate.album,
        "duration": details.candidate.duration,
        "details_url": details.candidate.details_url,
    }
    atomic_write(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2).encode(), overwrite=True)
    return DownloadResult(
        candidate=details.candidate,
        lyrics_path=str(lyrics_path) if lyrics_path else None,
        audio_path=str(audio_path) if audio_path else None,
        metadata_path=str(metadata_path),
        partial=lyrics is None or audio is None,
    )
