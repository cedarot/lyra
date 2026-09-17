from __future__ import annotations

from .base import SiteAdapter
from .fixture import FixtureAdapter
from .html import HtmlAdapter


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, SiteAdapter] = {}

    def register(self, adapter: SiteAdapter) -> None:
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"adapter already registered: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> SiteAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise KeyError(f"unknown adapter: {adapter_id}") from exc

    @property
    def ids(self) -> set[str]:
        return set(self._adapters)


def default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(FixtureAdapter())
    registry.register(HtmlAdapter())
    return registry
