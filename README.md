# Lyra

Lyra is a Python command-line tool for searching configured music websites and downloading resources that you are authorized to access. It provides one workflow for:

- searching one or more configured providers;
- selecting a normalized song result;
- resolving a provider's current direct audio URL;
- saving available lyrics, audio, and metadata with safe filenames.

Site-specific HTML or API behavior stays inside adapters. Network failures, parser failures, and unavailable resources are reported per provider instead of silently producing incomplete files.

Implementation is tracked in [REQ-001](https://github.com/cedarot/lyra/issues/1).

## Requirements

- Python 3.11 or newer;
- `beautifulsoup4` for the standard runtime;
- optional Playwright and Chromium for browser-backed providers such as 24bit;
- optional pytest for development and verification.

Lyra does not require an account and does not store passwords, cookies, or access tokens.

## Installation

Create a virtual environment, activate it, and install Lyra in editable mode:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

On Windows PowerShell, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install browser support only when you need a JavaScript-rendered provider:

```sh
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

Install the test dependencies for development:

```sh
python -m pip install -e ".[test]"
```

Check the installation:

```sh
lyra --help
```

You can use `python -m lyra` instead of `lyra` in every example.

## First-time setup

Create a configuration file. The default location is platform-dependent; using an explicit path makes scripts and projects easier to reproduce:

```sh
lyra init config --config ./lyra.toml
```

The generated file contains the default output directory, timeout, retry count, and response-size limit. It does not contain a provider yet, so add at least one provider before validating it. Provider commands update this file without storing credentials.

## Supported providers

Lyra currently bundles these adapters:

| Provider or adapter | ID | Access | Purpose |
| --- | --- | --- | --- |
| 24bit | `24bit` | Browser | Dedicated adapter for 24bit search and 96/192 kHz audio detail routes. |
| Generic HTML site | `html` | HTTP or browser | Configurable adapter for sites using CSS selectors and conventional search/detail pages. |
| Offline fixture | `fixture` | Local files | Test and demonstration adapter; it does not access a live music website. |

Only 24bit is a named live provider included in the project. Other websites must be configured with the generic HTML adapter or implemented as a separate adapter. Provider availability still depends on the site's current markup, terms, and access limits.

## Browser-backed providers

For a provider that requires JavaScript, add it in browser mode:

```sh
lyra provider add https://music.example \
  --access-mode browser \
  --config ./lyra.toml
```

Browser mode is headless by default. Use a visible browser only when the provider requires a human to complete an ordinary verification step:

```sh
lyra provider add https://music.example \
  --access-mode browser \
  --browser-visible \
  --config ./lyra.toml \
  --force
```

Lyra can also connect to a user-launched Chrome session through a credential-free CDP endpoint:

```sh
lyra provider add https://music.example \
  --access-mode browser \
  --browser-endpoint http://127.0.0.1:9222 \
  --config ./lyra.toml \
  --force
```

Lyra does not close an externally managed browser. It does not automate CAPTCHA, Cloudflare bypasses, login restrictions, or other access-control workarounds.

## Generic website providers

For a site with conventional HTML selectors, Lyra can derive a provider configuration from its HTTPS URL:

```sh
lyra provider add https://music.example --config ./lyra.toml
```

Override selectors when the site uses different markup:

```sh
lyra provider add https://music.example \
  --search-path "/search?q={query}" \
  --result-selector "article.track" \
  --title-selector ".track-title" \
  --details-selector "a.track-link" \
  --audio-selector "audio source" \
  --audio-attr src \
  --config ./lyra.toml
```

Manage providers with:

```sh
lyra provider list --config ./lyra.toml
lyra provider list --config ./lyra.toml --json
lyra provider test PROVIDER_ID --query song title --config ./lyra.toml
lyra provider test PROVIDER_ID --query song title --audio --config ./lyra.toml
lyra provider delete PROVIDER_ID --config ./lyra.toml
```

Provider definitions contain URLs, selectors, and request settings only. Do not put passwords, cookies, authorization headers, or tokens in the TOML file.

## Search, resolve, and download

Search all enabled providers:

```sh
lyra search "song title" --config ./lyra.toml
```

Search one provider and save machine-readable output:

```sh
lyra search "song title" --provider PROVIDER_ID \
  --config ./lyra.toml --json
```

Resolve a selected result to the provider's current direct audio URL:

```sh
lyra resolve "song title" --config ./lyra.toml
lyra resolve "song title" --result 2 --config ./lyra.toml --json
```

Download a song directly from a query:

```sh
lyra download "song title" --result 1 \
  --config ./lyra.toml --output ./downloads
```

After a search, Lyra stores a short-lived result cache under `<output_dir>/.lyra/search-results.json`. You can download a cached result without searching again:

```sh
lyra download --result 1 --config ./lyra.toml --output ./downloads
```

Use `--provider PROVIDER_ID` to restrict a search or cached-result selection. Existing files are not overwritten unless `--overwrite` is supplied.

Lyrics and audio are saved separately when the provider exposes them. If only one resource is available, Lyra reports a partial result instead of claiming full success. Downloads use temporary files and atomic finalization, so interrupted transfers do not appear as completed files.

## Direct audio URLs

When you already have a currently valid, authorized HTTPS audio URL, download it without provider parsing:

```sh
lyra download-url \
  'https://cdn.example/audio/song.flac?signature=...' \
  --config ./lyra.toml \
  --output ./downloads
```

Use `--filename` to choose a safe output filename:

```sh
lyra download-url 'https://cdn.example/audio/song.flac?signature=...' \
  --filename 'Artist - Song.flac' \
  --config ./lyra.toml \
  --output ./downloads
```

The command preserves common audio extensions, supports `--overwrite` and `--json`, and accepts shell-escaped query delimiters such as `\?`, `\=`, and `\&`. Signed URLs may expire quickly; Lyra does not refresh, forge, or bypass signed access tokens.
