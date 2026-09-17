from __future__ import annotations

import os
import json
import tomllib
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .models import AppConfig, SiteConfig


DEFAULT_CONFIG_TEMPLATE = '''# Lyra configuration
# Add one or more enabled site adapters before running search.

[settings]
output_dir = "./downloads"
timeout = 10.0
retries = 1
max_response_bytes = 10485760

# Example offline adapter. Replace this with a lawful site adapter
# supported by your installation and permitted by the site's terms.
# [[sites]]
# id = "fixture"
# name = "Fixture Site"
# adapter = "fixture"
# enabled = true
# priority = 10
# fixture_path = "./tests/fixtures/site"
'''

_SITE_RESERVED_KEYS = {
    "id", "name", "adapter", "base_url", "enabled", "priority", "fixture_path",
    "timeout", "retries", "rate_limit",
}


def default_config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "lyra" / "config.toml"


def init_config(path: Path, *, force: bool = False) -> Path:
    if path.exists() and not force:
        raise FileExistsError(f"configuration file already exists: {path}; use --force to replace it")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot initialize configuration: {exc}") from exc
    return path


def _read_raw_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML configuration: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc


def _toml_value(value: Any) -> str:
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else str(value)
    raise ConfigError("provider configuration contains an unsupported TOML value")


def _write_raw_config(path: Path, raw: dict[str, Any]) -> None:
    settings = raw.get("settings", {})
    sites = raw.get("sites", [])
    if not isinstance(settings, dict) or not isinstance(sites, list):
        raise ConfigError("configuration must contain a settings table and sites array")
    lines = ["# Lyra configuration", "", "[settings]"]
    for key, value in settings.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for site in sites:
        if not isinstance(site, dict):
            raise ConfigError("each site must be a TOML table")
        lines.extend(["", "[[sites]]"])
        for key, value in site.items():
            if isinstance(value, dict):
                raise ConfigError("nested site settings are not supported by provider commands")
            lines.append(f"{key} = {_toml_value(value)}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot write configuration: {exc}") from exc


def add_provider(path: Path, provider: dict[str, Any], *, force: bool = False) -> None:
    raw = _read_raw_config(path)
    sites = raw.setdefault("sites", [])
    if not isinstance(sites, list):
        raise ConfigError("sites must be an array of tables")
    existing = next((index for index, site in enumerate(sites) if site.get("id") == provider.get("id")), None)
    if existing is not None and not force:
        raise ConfigError(f"provider already exists: {provider['id']}; use --force to replace it")
    if existing is None:
        sites.append(provider)
    else:
        sites[existing] = provider
    _write_raw_config(path, raw)


def delete_provider(path: Path, provider_id: str) -> None:
    raw = _read_raw_config(path)
    sites = raw.get("sites", [])
    if not isinstance(sites, list):
        raise ConfigError("sites must be an array of tables")
    remaining = [site for site in sites if site.get("id") != provider_id]
    if len(remaining) == len(sites):
        raise ConfigError(f"provider not found: {provider_id}")
    raw["sites"] = remaining
    _write_raw_config(path, raw)


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} must be a number")
    if value <= 0:
        raise ConfigError(f"{field_name} must be greater than zero")
    return float(value)


def _as_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{field_name} must be a non-negative integer")
    return value


def load_config(path: Path, known_adapters: set[str] | None = None) -> AppConfig:
    raw = _read_raw_config(path)

    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError("[settings] must be a table")

    output_dir = settings.get("output_dir", "./downloads")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ConfigError("settings.output_dir must be a non-empty string")
    timeout = _as_float(settings.get("timeout", 10.0), "settings.timeout")
    retries = _as_non_negative_int(settings.get("retries", 1), "settings.retries")
    max_response_bytes = _as_non_negative_int(
        settings.get("max_response_bytes", 10 * 1024 * 1024),
        "settings.max_response_bytes",
    )
    if max_response_bytes == 0:
        raise ConfigError("settings.max_response_bytes must be greater than zero")

    raw_sites = raw.get("sites", [])
    if not isinstance(raw_sites, list) or not raw_sites:
        raise ConfigError("at least one [[sites]] entry is required")

    sites: list[SiteConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_sites, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"sites entry {index} must be a table")
        site_id = item.get("id")
        adapter = item.get("adapter")
        name = item.get("name", site_id)
        if not isinstance(site_id, str) or not site_id.strip():
            raise ConfigError(f"sites entry {index}.id must be a non-empty string")
        if site_id in seen_ids:
            raise ConfigError(f"duplicate site id: {site_id}")
        seen_ids.add(site_id)
        if not isinstance(adapter, str) or not adapter.strip():
            raise ConfigError(f"sites.{site_id}.adapter must be a non-empty string")
        if known_adapters is not None and adapter not in known_adapters:
            raise ConfigError(f"unknown adapter '{adapter}' for site '{site_id}'")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"sites.{site_id}.name must be a non-empty string")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"sites.{site_id}.enabled must be boolean")
        priority = item.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ConfigError(f"sites.{site_id}.priority must be an integer")
        base_url = item.get("base_url", "")
        if not isinstance(base_url, str):
            raise ConfigError(f"sites.{site_id}.base_url must be a string")
        fixture_path = item.get("fixture_path")
        if fixture_path is not None and not isinstance(fixture_path, str):
            raise ConfigError(f"sites.{site_id}.fixture_path must be a string")
        site_timeout = item.get("timeout")
        if site_timeout is not None:
            site_timeout = _as_float(site_timeout, f"sites.{site_id}.timeout")
        site_retries = item.get("retries")
        if site_retries is not None:
            site_retries = _as_non_negative_int(site_retries, f"sites.{site_id}.retries")
        rate_limit = item.get("rate_limit", 0.0)
        if isinstance(rate_limit, bool) or not isinstance(rate_limit, (int, float)) or rate_limit < 0:
            raise ConfigError(f"sites.{site_id}.rate_limit must be a non-negative number")
        options = {key: value for key, value in item.items() if key not in _SITE_RESERVED_KEYS}
        sites.append(SiteConfig(
            id=site_id,
            name=name,
            adapter=adapter,
            base_url=base_url,
            enabled=enabled,
            priority=priority,
            fixture_path=fixture_path,
            timeout=site_timeout,
            retries=site_retries,
            rate_limit=float(rate_limit),
            options=options,
        ))

    if not any(site.enabled for site in sites):
        raise ConfigError("at least one site must be enabled")
    return AppConfig(
        output_dir=output_dir,
        timeout=timeout,
        retries=retries,
        max_response_bytes=max_response_bytes,
        sites=tuple(sites),
    )
