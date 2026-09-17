from .base import SiteAdapter
from .fixture import FixtureAdapter
from .html import HtmlAdapter
from .registry import AdapterRegistry, default_registry

__all__ = ["AdapterRegistry", "FixtureAdapter", "HtmlAdapter", "SiteAdapter", "default_registry"]
