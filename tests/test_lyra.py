from __future__ import annotations

import json
from pathlib import Path

import pytest

from lyra.adapters.registry import default_registry
from lyra.adapters.html import HtmlAdapter
from lyra.application import DirectDownloadService, DownloadService, SearchService
import lyra.application as application
from lyra.cli import main
from lyra.config import load_config
from lyra.errors import ConfigError, LyraError
from lyra.models import AppConfig, SiteConfig
from lyra.storage import safe_component


ROOT = Path(__file__).parent


def test_fixture_search_returns_normalized_candidate():
    config = load_config(ROOT / "fixtures/valid-config.toml", default_registry().ids)
    report = SearchService(config, default_registry()).search("fixture song")

    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.site_id == "fixture"
    assert candidate.title == "Fixture Song"
    assert candidate.artist == "Fixture Artist"
    assert report.failures == []


def test_one_site_failure_does_not_block_other_sites(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[settings]\noutput_dir = \"./downloads\"\n\n[[sites]]\nid = \"bad\"\nname = \"Bad\"\nadapter = \"fixture\"\nfixture_path = \"missing\"\npriority = 20\n\n[[sites]]\nid = \"good\"\nname = \"Good\"\nadapter = \"fixture\"\nfixture_path = \"tests/fixtures/site\"\npriority = 10\n""",
        encoding="utf-8",
    )
    config = load_config(config_path, default_registry().ids)
    report = SearchService(config, default_registry()).search("fixture song")

    assert [candidate.site_id for candidate in report.candidates] == ["good"]
    assert report.failures[0].site_id == "bad"
    assert report.failures[0].category == "configuration"


def test_download_writes_lyrics_audio_and_metadata(tmp_path):
    config = load_config(ROOT / "fixtures/valid-config.toml", default_registry().ids)
    candidate = SearchService(config, default_registry()).search("fixture song").candidates[0]
    result = DownloadService(config, default_registry()).download(candidate, str(tmp_path))

    assert Path(result.lyrics_path).read_text(encoding="utf-8").startswith("[00:01.00]")
    assert Path(result.audio_path).read_bytes() == b"LYRA AUDIO FIXTURE\n"
    metadata = json.loads(Path(result.metadata_path).read_text(encoding="utf-8"))
    assert metadata["title"] == "Fixture Song"
    assert result.partial is False


def test_download_does_not_overwrite_or_leave_partial_files(tmp_path):
    config = load_config(ROOT / "fixtures/valid-config.toml", default_registry().ids)
    candidate = SearchService(config, default_registry()).search("fixture song").candidates[0]
    DownloadService(config, default_registry()).download(candidate, str(tmp_path))
    with pytest.raises(FileExistsError):
        DownloadService(config, default_registry()).download(candidate, str(tmp_path))
    assert len(list(tmp_path.glob(".*"))) == 0


def test_config_rejects_unknown_adapter(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text(
        "[settings]\noutput_dir = \"./downloads\"\n\n[[sites]]\nid = \"x\"\nadapter = \"unknown\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown adapter"):
        load_config(path, {"fixture"})


def test_safe_component_removes_path_controls():
    value = safe_component("../Artist:Song?*")
    assert "/" not in value
    assert ".." not in value
    assert value == "__Artist_Song__"


def test_init_config_creates_template_and_requires_force(tmp_path, capsys):
    config_path = tmp_path / "lyra" / "config.toml"

    assert main(["init", "config", "--config", str(config_path)]) == 0
    assert config_path.exists()
    content = config_path.read_text(encoding="utf-8")
    assert "[settings]" in content
    assert "[[sites]]" in content
    assert "password" not in content.lower()
    assert "Initialized configuration" in capsys.readouterr().out

    assert main(["init", "config", "--config", str(config_path)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert main(["init", "config", "--config", str(config_path), "--force"]) == 0


def test_provider_add_and_delete_updates_config(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    assert main(["init", "config", "--config", str(config_path)]) == 0
    capsys.readouterr()

    assert main(["provider", "add", "https://music.example.com", "--config", str(config_path)]) == 0
    capsys.readouterr()
    config = load_config(config_path, default_registry().ids)
    provider = config.sites[0]
    assert provider.id == "music-example"
    assert provider.name == "Music Example"
    assert provider.adapter == "html"
    assert provider.options["search_path"] == "/search?q={query}"

    assert main(["provider", "delete", "music-example", "--config", str(config_path)]) == 0
    assert "Deleted provider" in capsys.readouterr().out
    assert "sites" not in config_path.read_text(encoding="utf-8")


class FakeHttpClient:
    def fetch_text(self, url, site):
        if "/search" in url:
            return """<article class='song'><span class='title'>New Song</span><span class='artist'>New Artist</span><a class='details' href='/song/1'>details</a></article>"""
        return """<div id='lyrics'>[00:01]hello</div><audio id='audio' src='/audio/new.mp3'></audio>"""


def test_html_adapter_uses_configured_selectors():
    site = SiteConfig(
        id="example", name="Example", adapter="html", base_url="https://example.test",
        options={
            "search_path": "/search?q={query}", "result_selector": "article.song", "title_selector": ".title",
            "details_selector": "a.details", "artist_selector": ".artist", "lyrics_selector": "#lyrics",
            "audio_selector": "audio", "audio_attr": "src", "audio_extension": "mp3",
        },
    )
    adapter = HtmlAdapter()
    candidates = adapter.search("new song", site, FakeHttpClient())
    details = adapter.get_details(candidates[0], site, FakeHttpClient())

    assert candidates[0].details_url == "https://example.test/song/1"
    assert details.lyrics.content == "[00:01]hello\n"
    assert details.audio.url == "https://example.test/audio/new.mp3"


def test_provider_test_reports_audio_download(capsys):
    config_path = ROOT / "fixtures/valid-config.toml"
    assert main(["provider", "test", "fixture", "--config", str(config_path), "--query", "fixture song", "--audio", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["audio_available"] is True
    assert payload["audio_downloaded"] is True
    assert payload["audio_bytes"] > 0


def test_provider_test_accepts_unquoted_multiword_query(capsys):
    config_path = ROOT / "fixtures/valid-config.toml"
    assert main(["provider", "test", "fixture", "--config", str(config_path), "--query", "fixture", "song", "--audio", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "fixture song"
    assert payload["audio_downloaded"] is True


def test_provider_list_outputs_configured_sites(capsys):
    config_path = ROOT / "fixtures/valid-config.toml"
    assert main(["provider", "list", "--config", str(config_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == "fixture"
    assert payload[0]["adapter"] == "fixture"


class FakeDirectHttpClient:
    def __init__(self, *args):
        pass

    def fetch_bytes(self, url, site, *, accept):
        assert url.startswith("https://cdn.example/")
        assert accept.startswith("audio/")
        return b"FLAC DATA"


def test_direct_download_preserves_extension_and_supports_custom_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "HttpClient", FakeDirectHttpClient)
    service = DirectDownloadService(AppConfig(output_dir=str(tmp_path)))

    result = service.download("https://cdn.example/path/song.flac?signature=temporary", filename="十年人间.flac")

    assert Path(result.path).name == "十年人间.flac"
    assert Path(result.path).read_bytes() == b"FLAC DATA"
    assert result.bytes_written == 9


def test_direct_download_uses_configured_directory_and_url_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "HttpClient", FakeDirectHttpClient)
    configured_dir = tmp_path / "configured-downloads"
    service = DirectDownloadService(AppConfig(output_dir=str(configured_dir)))

    result = service.download("https://cdn.example/path/network-name.flac?signature=temporary")

    assert Path(result.path) == configured_dir / "network-name.flac"
    assert Path(result.path).read_bytes() == b"FLAC DATA"


def test_direct_download_normalizes_shell_escaped_url_delimiters(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "HttpClient", FakeDirectHttpClient)
    service = DirectDownloadService(AppConfig(output_dir=str(tmp_path)))

    result = service.download(r"https://cdn.example/path/network-name.flac\?signature\=temporary")

    assert result.url == "https://cdn.example/path/network-name.flac?signature=temporary"
    assert Path(result.path).name == "network-name.flac"


def test_direct_download_output_is_a_directory_with_url_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "HttpClient", FakeDirectHttpClient)
    explicit_dir = tmp_path / "explicit-downloads"
    service = DirectDownloadService(AppConfig(output_dir=str(tmp_path / "unused")))

    result = service.download(
        "https://cdn.example/path/network-name.flac?signature=temporary",
        output_dir=str(explicit_dir),
    )

    assert Path(result.path) == explicit_dir / "network-name.flac"
    assert Path(result.path).read_bytes() == b"FLAC DATA"


def test_direct_download_rejects_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "HttpClient", FakeDirectHttpClient)
    output_file = tmp_path / "output"
    output_file.write_text("not a directory", encoding="utf-8")
    service = DirectDownloadService(AppConfig(output_dir=str(tmp_path / "unused")))

    with pytest.raises(LyraError, match="not a directory"):
        service.download("https://cdn.example/path/song.flac", output_dir=str(output_file))


def test_direct_download_cli_works_without_config_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(application, "HttpClient", FakeDirectHttpClient)
    result = main([
        "download-url", "https://cdn.example/path/song.flac?signature=temporary",
        "--config", str(tmp_path / "missing.toml"), "--output", str(tmp_path), "--json",
    ])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["bytes"] == 9
    assert Path(payload["path"]) == tmp_path / "song.flac"
