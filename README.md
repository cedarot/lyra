# Lyra

Lyra is a Python command-line tool for searching configured music-site adapters and downloading authorized lyrics and audio resources.

Implementation is tracked in [REQ-001](https://github.com/cedarot/lyra/issues/1).

## Install

Lyra supports Python 3.11+ and has no runtime dependencies in the MVP:

```sh
python -m pip install -e .
```

## Quick start

```sh
lyra init config
lyra config validate --config tests/fixtures/valid-config.toml
lyra search "fixture song" --config tests/fixtures/valid-config.toml --provider fixture --json
lyra download --config tests/fixtures/valid-config.toml --result 1
lyra download "fixture song" --config tests/fixtures/valid-config.toml
```

`lyra init config` creates a starter TOML file in the platform configuration directory. Use `--config PATH` to choose a location and `--force` to replace an existing file. The generated file contains a commented fixture adapter example; enable and configure lawful site adapters before searching. The fixture adapter is intentionally offline and demonstrates the adapter contract. Live adapters are only added for sites whose terms and access rules permit the requested automation.

## Configuration

```toml
[settings]
output_dir = "./downloads"
timeout = 10.0
retries = 1
max_response_bytes = 10485760

[[sites]]
id = "fixture"
name = "Fixture Site"
adapter = "fixture"
enabled = true
priority = 10
fixture_path = "tests/fixtures/site"
```

`search` writes a short-lived selection cache under `<output_dir>/.lyra/search-results.json`, which lets `download --result N` select the result from the most recent search. Pass `--provider PROVIDER_ID` to `search` to query only one enabled provider. The same option on `download` limits a new query to that provider, or filters cached results when no query is supplied. Both download commands use `settings.output_dir` by default; `--output DIR` is optional and means “save inside this directory”. Use `--json` for scripts. Existing output files are not overwritten unless `--overwrite` is supplied.

Providers use `access_mode = "http"` by default. For a provider that requires JavaScript, configure `access_mode = "browser"` (install with `python -m pip install -e '.[browser]'` and then `python -m playwright install chromium`). Browser mode launches an ephemeral headless browser automatically, keeps its session only for the current Lyra command, and runs silently without requiring a pre-opened browser. Use `--browser-visible` only when a provider requires manual interaction; `--browser-headless` is also accepted explicitly. It does not persist cookies or automate CAPTCHA/access-control bypasses.

If a provider repeatedly challenges a Playwright-launched browser, Lyra can manage a background Chrome profile automatically. It first reuses a browser already available at the conventional local endpoint `http://127.0.0.1:9222`; otherwise the 24bit adapter starts Chrome with a dedicated profile and configures the endpoint internally. The managed browser remains available after a command so a later `download` can reuse its session. `--browser-endpoint` is only needed for a different endpoint. Lyra connects to an external browser without closing it; JSON-only requests do not create a blank tab, and any Lyra-created page is opened lazily and closed after use. The browser owns its session and the user remains responsible for completing site verification.

If 24bit reports that today's access quota is exhausted, use a visible browser session for the normal site login flow, then retry the command. For example, configure the provider with `--browser-visible` (or use a visible browser's `--browser-endpoint`); Lyra pauses for you to complete login and never reads or stores your credentials.

`resolve "歌曲名"` searches the enabled providers, resolves the selected result's direct audio URL, and prints it. `download "歌曲名"` performs the same search and automatically downloads the highest-ranked result; use `--result N` to choose a different result.

## Website providers

Add a new website without specifying an ID, name, base URL, or selectors. Lyra derives provider metadata from the URL and applies generic HTML selectors:

```sh
lyra provider add https://music.example
```

For sites with non-standard HTML, override individual defaults such as `--search-path`, `--result-selector`, `--title-selector`, or `--audio-selector`. Add a browser-backed provider with `lyra provider add https://music.example --access-mode browser`. List providers with `lyra provider list` or `lyra provider list --json`; delete one with `lyra provider delete PROVIDER_ID`. Test a provider with `lyra provider test PROVIDER_ID --query song title --audio`. The provider ID is required and the query accepts multiple unquoted words. `--audio` downloads the resource into memory and reports the byte count without saving it. Provider definitions contain selectors and URLs only; do not add credentials, cookies, or tokens.

24bit has a dedicated adapter because its search is a JavaScript JSON request rather than a stable search URL. Lyra recognizes the 24bit hostname and defaults to the dedicated browser-backed adapter, headless access, and 96 kHz quality. Configure it with one command:

```sh
lyra provider add https://www.24bit.net
```

The adapter posts to `/api/player/searchOnlineMusicOne` with `keyword` and `page`, then resolves each result through `/music/c/{id}` for 24-bit 96 kHz or `/music/a/{id}` for 24-bit 192 kHz. It reads the currently signed audio URL from the detail page's `audio source[src]`; signed URLs are intentionally not persisted because they expire. Future searches use these fixed routes directly and do not perform page or endpoint discovery. Use `--quality 192` to select the 192 kHz route. Use `--browser-endpoint` only when deliberately reusing a user-launched Chrome session.

## Safety and access boundaries

Lyra does not bypass DRM, paywalls, CAPTCHA, login restrictions, or other access controls. Users are responsible for complying with the target site's terms, copyright rules, and applicable law. Credentials, cookies, and access tokens are not stored by the MVP.

## Direct audio URLs

When a site provides an authorized, currently valid audio resource URL, download it without HTML parsing:

```sh
lyra download-url 'https://cdn.example/audio/song.flac?signature=...'
lyra download-url 'https://cdn.example/audio/song.flac?signature=...' --output ./downloads
```

`--output` is optional: without it, Lyra uses `settings.output_dir` from the selected configuration. When supplied, it is treated as a directory and Lyra saves the file inside it using the filename from the URL path. `--filename` can override that name. Lyra preserves the URL extension, supports `--overwrite` and `--json`, and reports HTTP status failures such as 403 or 404. Signed URLs may expire quickly; Lyra does not bypass or refresh access-control tokens.

Lyra also accepts common shell-escaped query delimiters copied into the argument, such as `\?`, `\=`, and `\&`, and normalizes them before making the request.

## Adapter development

Adapters implement `search`, `get_details`, `get_lyrics`, and `get_audio` behind `lyra.adapters.SiteAdapter`. Keep HTML parsing inside the adapter, add offline fixtures for normal and changed page structures, and return the shared domain models so the CLI and application layer remain site-agnostic.
