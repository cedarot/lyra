from __future__ import annotations

import json
from pathlib import Path

import pytest

from lyra.adapters.registry import default_registry
from lyra.adapters.html import HtmlAdapter
from lyra.adapters.twentyfourbit import TwentyFourBitAdapter
from lyra.application import DirectDownloadService, DownloadService, ResolveService, SearchService
import lyra.application as application
from lyra.cli import main
from lyra.config import load_config
from lyra.errors import ConfigError, LyraError
from lyra.http import BrowserClient
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


def test_search_can_target_one_provider(tmp_path):
    fixture_path = (ROOT / "fixtures/site").resolve()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""[settings]\noutput_dir = \"./downloads\"\n\n[[sites]]\nid = \"first\"\nname = \"First\"\nadapter = \"fixture\"\nfixture_path = \"{fixture_path}\"\n\n[[sites]]\nid = \"second\"\nname = \"Second\"\nadapter = \"fixture\"\nfixture_path = \"{fixture_path}\"\n""",
        encoding="utf-8",
    )
    config = load_config(config_path, default_registry().ids)

    report = SearchService(config, default_registry()).search("fixture song", "second")

    assert [candidate.site_id for candidate in report.candidates] == ["second"]


def test_search_rejects_unknown_provider():
    config = load_config(ROOT / "fixtures/valid-config.toml", default_registry().ids)

    with pytest.raises(LyraError, match="enabled provider not found: missing"):
        SearchService(config, default_registry()).search("fixture song", "missing")


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


def test_provider_add_supports_browser_access_mode(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    assert main(["init", "config", "--config", str(config_path)]) == 0
    capsys.readouterr()

    assert main([
        "provider", "add", "https://music.example.com", "--access-mode", "browser",
        "--browser-endpoint", "http://127.0.0.1:9222",
        "--config", str(config_path),
    ]) == 0
    capsys.readouterr()

    config = load_config(config_path, default_registry().ids)
    assert config.sites[0].access_mode == "browser"
    assert config.sites[0].options["browser_endpoint"] == "http://127.0.0.1:9222"


def test_provider_add_infers_24bit_defaults(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    assert main(["init", "config", "--config", str(config_path)]) == 0
    capsys.readouterr()

    assert main(["provider", "add", "https://www.24bit.net", "--config", str(config_path)]) == 0
    capsys.readouterr()

    config = load_config(config_path, default_registry().ids)
    provider = config.sites[0]
    assert provider.id == "24bit"
    assert provider.adapter == "24bit"
    assert provider.access_mode == "browser"
    assert provider.options["quality"] == "96"
    assert "browser_endpoint" not in provider.options


def test_browser_access_defaults_to_headless_and_allows_visible_override(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    assert main(["init", "config", "--config", str(config_path)]) == 0
    capsys.readouterr()

    assert main([
        "provider", "add", "https://music.example.com", "--access-mode", "browser",
        "--config", str(config_path),
    ]) == 0
    capsys.readouterr()
    config = load_config(config_path, default_registry().ids)
    assert config.sites[0].options.get("browser_headless", True) is True

    assert main([
        "provider", "add", "https://music.example.com", "--access-mode", "browser",
        "--browser-visible", "--config", str(config_path), "--force",
    ]) == 0
    capsys.readouterr()
    config = load_config(config_path, default_registry().ids)
    assert config.sites[0].options["browser_headless"] is False


def test_provider_add_supports_24bit_adapter(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    assert main(["init", "config", "--config", str(config_path)]) == 0
    capsys.readouterr()

    assert main([
        "provider", "add", "https://www.24bit.net", "--id", "24bit",
        "--adapter", "24bit", "--access-mode", "browser",
        "--browser-endpoint", "http://127.0.0.1:9222", "--quality", "192",
        "--config", str(config_path),
    ]) == 0
    capsys.readouterr()

    config = load_config(config_path, default_registry().ids)
    provider = config.sites[0]
    assert provider.adapter == "24bit"
    assert provider.options["quality"] == "192"
    assert provider.options["browser_endpoint"] == "http://127.0.0.1:9222"


class FakeTwentyFourBitClient:
    def __init__(self):
        self.posts = []

    def post_json(self, url, payload, site):
        self.posts.append((url, payload, site.id))
        return {
            "status": True,
            "result": [{
                "id": "song-1", "name": "Test Song", "player": "Test Artist", "album": "Test Album",
            }],
        }

    def fetch_text(self, url, site):
        return "<audio><source src='https://cdn.example/song.flac?signature=temporary' type='audio/flac'></audio>"


def test_24bit_adapter_uses_fixed_search_and_detail_interfaces():
    site = SiteConfig(
        id="24bit", name="24bit", adapter="24bit", base_url="https://www.24bit.net",
        options={"quality": "192"},
    )
    client = FakeTwentyFourBitClient()
    adapter = TwentyFourBitAdapter()

    candidates = adapter.search("十年人间", site, client)
    details = adapter.get_details(candidates[0], site, client)

    assert client.posts == [(
        "https://www.24bit.net/api/player/searchOnlineMusicOne",
        {"keyword": "%E5%8D%81%E5%B9%B4%E4%BA%BA%E9%97%B4", "page": 1},
        "24bit",
    )]
    assert candidates[0].details_url == "https://www.24bit.net/music/a/song-1"
    assert candidates[0].adapter_ref == "song-1"
    assert details.audio.url == "https://cdn.example/song.flac?signature=temporary"
    assert details.audio.extension == ".flac"


def test_config_rejects_unknown_access_mode(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text(
        "[settings]\noutput_dir = \"./downloads\"\n\n[[sites]]\nid = \"x\"\nadapter = \"fixture\"\naccess_mode = \"proxy\"\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="access_mode"):
        load_config(path, default_registry().ids)


def test_browser_access_mode_selects_browser_transport(monkeypatch):
    site = SiteConfig(id="browser", name="Browser", adapter="html", access_mode="browser")
    config = AppConfig(output_dir="./downloads", sites=(site,))

    class FakeBrowserClient:
        def __init__(self, timeout, retries, max_response_bytes, selected_site):
            self.selected_site = selected_site

        def close(self):
            pass

    monkeypatch.setattr(application, "BrowserClient", FakeBrowserClient)

    client = application.client_for_provider(config, site)

    assert isinstance(client, FakeBrowserClient)
    assert client.selected_site is site


def test_browser_client_launches_headless_without_cdp(monkeypatch):
    calls = {}

    class FakePage:
        pass

    class FakeContext:
        def __init__(self):
            self.pages_created = 0

        def new_page(self):
            self.pages_created += 1
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        contexts = []

        def new_context(self):
            context = FakeContext()
            calls["context"] = context
            return context

        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kwargs):
            calls.update(kwargs)
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def start(self):
            return self

        def stop(self):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakePlaywright())
    site = SiteConfig(id="browser", name="Browser", adapter="html", access_mode="browser")
    client = BrowserClient(10.0, 1, 1024, site)

    client._start()
    assert calls["context"].pages_created == 0
    client.close()

    assert calls["headless"] is True


def test_browser_client_detects_local_cdp_endpoint(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("lyra.http.urlopen", lambda *args, **kwargs: FakeResponse())
    client = BrowserClient(10.0, 1, 1024, SiteConfig(id="browser", name="Browser", adapter="html"))

    assert client._detect_auto_cdp_endpoint() == "http://127.0.0.1:9222"


class FakeHttpClient:
    def fetch_text(self, url, site):
        if "/search" in url:
            return """<article class='song'><span class='title'>New Song</span><span class='artist'>New Artist</span><a class='details' href='/song/1'>details</a></article>"""
        return """<div id='lyrics'>[00:01]hello</div><audio id='audio' src='/audio/new.mp3'></audio>"""

    def close(self):
        pass


class FakeResolveHttpClient(FakeHttpClient):
    def __init__(self, *args):
        pass


def html_test_site() -> SiteConfig:
    return SiteConfig(
        id="example", name="Example", adapter="html", base_url="https://example.test",
        options={
            "search_path": "/search?q={query}", "result_selector": "article.song", "title_selector": ".title",
            "details_selector": "a.details", "artist_selector": ".artist", "lyrics_selector": "#lyrics",
            "audio_selector": "audio", "audio_attr": "src", "audio_extension": "mp3",
        },
    )


def test_html_adapter_uses_configured_selectors():
    site = html_test_site()
    adapter = HtmlAdapter()
    candidates = adapter.search("new song", site, FakeHttpClient())
    details = adapter.get_details(candidates[0], site, FakeHttpClient())

    assert candidates[0].details_url == "https://example.test/song/1"
    assert details.lyrics.content == "[00:01]hello\n"
    assert details.audio.url == "https://example.test/audio/new.mp3"


def test_resolve_returns_direct_audio_url(monkeypatch):
    monkeypatch.setattr(application, "HttpClient", FakeResolveHttpClient)
    config = AppConfig(output_dir="./downloads", sites=(html_test_site(),))

    result = ResolveService(config, default_registry()).resolve("new song")

    assert result.url == "https://example.test/audio/new.mp3"
    assert result.candidate.title == "New Song"


def test_download_query_auto_selects_top_result(tmp_path, capsys):
    config_path = tmp_path / "config.toml"
    output_dir = tmp_path / "downloads"
    fixture_path = (ROOT / "fixtures/site").resolve()
    config_path.write_text(
        f"""[settings]\noutput_dir = \"{output_dir}\"\n\n[[sites]]\nid = \"fixture\"\nname = \"Fixture Site\"\nadapter = \"fixture\"\nfixture_path = \"{fixture_path}\"\n""",
        encoding="utf-8",
    )

    assert main(["download", "fixture song", "--config", str(config_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["audio_path"]).parent == output_dir


def test_download_can_target_provider_for_query_and_cached_results(tmp_path, capsys):
    fixture_path = (ROOT / "fixtures/site").resolve()
    output_dir = tmp_path / "downloads"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""[settings]\noutput_dir = \"{output_dir}\"\n\n[[sites]]\nid = \"first\"\nname = \"First\"\nadapter = \"fixture\"\nfixture_path = \"{fixture_path}\"\npriority = 20\n\n[[sites]]\nid = \"second\"\nname = \"Second\"\nadapter = \"fixture\"\nfixture_path = \"{fixture_path}\"\npriority = 10\n""",
        encoding="utf-8",
    )

    assert main(["search", "fixture song", "--config", str(config_path)]) == 0
    capsys.readouterr()
    assert main([
        "download", "--config", str(config_path), "--provider", "second", "--result", "1", "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["site"] == "Second"
    assert json.loads(Path(payload["metadata_path"]).read_text(encoding="utf-8"))["site_id"] == "second"


def test_download_query_provider_filters_search_results(tmp_path, capsys):
    fixture_path = (ROOT / "fixtures/site").resolve()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""[settings]\noutput_dir = \"{tmp_path / 'downloads'}\"\n\n[[sites]]\nid = \"first\"\nname = \"First\"\nadapter = \"fixture\"\nfixture_path = \"{fixture_path}\"\n\n[[sites]]\nid = \"second\"\nname = \"Second\"\nadapter = \"fixture\"\nfixture_path = \"{fixture_path}\"\n""",
        encoding="utf-8",
    )

    assert main([
        "download", "fixture song", "--provider", "second", "--config", str(config_path), "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["site"] == "Second"


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
