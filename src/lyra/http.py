from __future__ import annotations

import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import SiteError
from .models import SiteConfig


class HttpClient:
    def __init__(self, timeout: float, retries: int, max_response_bytes: int):
        self.timeout = timeout
        self.retries = retries
        self.max_response_bytes = max_response_bytes
        self._last_request: dict[str, float] = {}

    def fetch_bytes(self, url: str, site: SiteConfig, *, accept: str = "*/*") -> bytes:
        if not url.startswith("https://"):
            raise SiteError(site.id, "security", "only HTTPS resources are allowed")
        if site.rate_limit:
            elapsed = time.monotonic() - self._last_request.get(site.id, 0.0)
            if elapsed < site.rate_limit:
                time.sleep(site.rate_limit - elapsed)
        self._last_request[site.id] = time.monotonic()
        timeout = site.timeout or self.timeout
        retries = self.retries if site.retries is None else site.retries
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                request = Request(url, headers={"User-Agent": "Lyra/0.1", "Accept": accept})
                with urlopen(request, timeout=timeout) as response:
                    body = response.read(self.max_response_bytes + 1)
                    if len(body) > self.max_response_bytes:
                        raise SiteError(site.id, "response_too_large", "response exceeds configured size limit")
                    return body
            except SiteError:
                raise
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < retries:
                time.sleep(min(0.25 * (2 ** attempt), 2.0))
        raise SiteError(site.id, "network", "request failed") from last_error

    def fetch_text(self, url: str, site: SiteConfig) -> str:
        return self.fetch_bytes(url, site, accept="text/html,text/plain;q=0.9").decode("utf-8", errors="replace")
