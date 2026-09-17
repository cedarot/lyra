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
lyra search "fixture song" --config tests/fixtures/valid-config.toml --json
lyra download --config tests/fixtures/valid-config.toml --result 1 --output ./downloads
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

`search` writes a short-lived selection cache under `<output_dir>/.lyra/search-results.json`, which lets `download --result N` select the result from the most recent search. Use `--json` for scripts. Existing output files are not overwritten unless `--overwrite` is supplied.

## Website providers

Add a new website without specifying an ID, name, base URL, or selectors. Lyra derives provider metadata from the URL and applies generic HTML selectors:

```sh
lyra provider add https://music.example
```

For sites with non-standard HTML, override individual defaults such as `--search-path`, `--result-selector`, `--title-selector`, or `--audio-selector`. Manage providers with `lyra provider delete PROVIDER_ID`. Test search and audio access with `lyra provider test PROVIDER_ID --query "song title" --audio`; `--audio` downloads the resource into memory and reports the byte count without saving it. Provider definitions contain selectors and URLs only; do not add credentials, cookies, or tokens.

## Safety and access boundaries

Lyra does not bypass DRM, paywalls, CAPTCHA, login restrictions, or other access controls. Users are responsible for complying with the target site's terms, copyright rules, and applicable law. Credentials, cookies, and access tokens are not stored by the MVP.

## Adapter development

Adapters implement `search`, `get_details`, `get_lyrics`, and `get_audio` behind `lyra.adapters.SiteAdapter`. Keep HTML parsing inside the adapter, add offline fixtures for normal and changed page structures, and return the shared domain models so the CLI and application layer remain site-agnostic.
