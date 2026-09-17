from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

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
        if isinstance(last_error, HTTPError):
            raise SiteError(site.id, "http", f"HTTP {last_error.code}") from last_error
        raise SiteError(site.id, "network", "request failed") from last_error

    def fetch_text(self, url: str, site: SiteConfig) -> str:
        return self.fetch_bytes(url, site, accept="text/html,text/plain;q=0.9").decode("utf-8", errors="replace")

    def post_json(self, url: str, payload: dict, site: SiteConfig) -> dict:
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
            request = Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "User-Agent": "Lyra/0.1",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise SiteError(site.id, "response_too_large", "response exceeds configured size limit")
                result = json.loads(body.decode("utf-8", errors="replace"))
                if not isinstance(result, dict):
                    raise SiteError(site.id, "parser", "JSON response must be an object")
                return result
            except SiteError:
                raise
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < retries:
                time.sleep(min(0.25 * (2 ** attempt), 2.0))
        if isinstance(last_error, HTTPError):
            raise SiteError(site.id, "http", f"HTTP {last_error.code}") from last_error
        raise SiteError(site.id, "network", "JSON request failed") from last_error

    def close(self) -> None:
        """Release transport resources. The HTTP transport has no persistent resources."""


class BrowserClient:
    """Ephemeral headless browser transport for providers requiring a browser session.

    This transport does not persist cookies or automate CAPTCHA/access-control bypasses.
    Browser mode launches silently by default. A visible browser is an explicit opt-in
    for providers that require manual interaction.
    """

    auto_cdp_endpoint = "http://127.0.0.1:9222"

    def __init__(self, timeout: float, retries: int, max_response_bytes: int, site: SiteConfig):
        self.timeout = timeout
        self.retries = retries
        self.max_response_bytes = max_response_bytes
        self.site = site
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._owns_browser = False
        self._owns_context = False
        self._owns_page = False

    def _start(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            endpoint = self.site.options.get("browser_endpoint")
            auto_endpoint = self._detect_auto_cdp_endpoint() if endpoint is None else None
            if endpoint is not None:
                if not isinstance(endpoint, str):
                    raise SiteError(self.site.id, "configuration", "browser_endpoint must be a URL")
                parsed = urlparse(endpoint)
                if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc or parsed.username or parsed.password:
                    raise SiteError(self.site.id, "configuration", "browser_endpoint must be a credential-free CDP URL")
                self._browser = self._playwright.chromium.connect_over_cdp(endpoint)
                self._owns_browser = False
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                    self._owns_context = False
                else:
                    self._context = self._browser.new_context()
                    self._owns_context = True
            elif auto_endpoint:
                self._browser = self._playwright.chromium.connect_over_cdp(auto_endpoint)
                self._owns_browser = False
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                    self._owns_context = False
                else:
                    self._context = self._browser.new_context()
                    self._owns_context = True
            else:
                headless = self.site.options.get("browser_headless", True)
                self._browser = self._playwright.chromium.launch(headless=headless)
                self._owns_browser = True
                self._context = self._browser.new_context()
                self._owns_context = True
        except ImportError as exc:
            raise SiteError(self.site.id, "configuration", "browser access requires 'lyra[browser]' and a Chromium install") from exc
        except Exception as exc:
            self.close()
            raise SiteError(self.site.id, "browser", "cannot start the configured browser") from exc

    def _detect_auto_cdp_endpoint(self) -> str | None:
        """Use the conventional local Chrome CDP endpoint when it is already available."""
        try:
            with urlopen(f"{self.auto_cdp_endpoint}/json/version", timeout=0.25) as response:
                if response.status < 400:
                    return self.auto_cdp_endpoint
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        return None

    def _load(self, url: str) -> None:
        self._start()
        if self._page is None:
            self._page = self._context.new_page()
            self._owns_page = True
        timeout_ms = int((self.site.timeout or self.timeout) * 1000)
        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                self._page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3000))
            except Exception:
                pass
            if self._verification_page():
                self._wait_for_user_verification(url)
                response = self._page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            if response is not None and response.status >= 400:
                raise SiteError(self.site.id, "http", f"HTTP {response.status}")
        except SiteError:
            raise
        except Exception as exc:
            raise SiteError(self.site.id, "browser", "browser navigation failed") from exc

    def _verification_page(self) -> bool:
        title = self._page.title().casefold()
        body = self._page.locator("body").inner_text(timeout=1000).casefold()
        markers = ("just a moment", "verify you are human", "checking your browser", "security check")
        return any(marker in title or marker in body for marker in markers)

    def _wait_for_user_verification(self, url: str) -> None:
        import sys

        if not sys.stdin.isatty() or self.site.options.get("browser_headless", True):
            raise SiteError(self.site.id, "access", "provider requires interactive browser verification; run from a terminal with browser access")
        print(f"Complete the provider verification in the browser window for {url}, then press Enter.", file=sys.stderr)
        input()

    def fetch_text(self, url: str, site: SiteConfig) -> str:
        if site.id != self.site.id:
            raise SiteError(site.id, "configuration", "browser transport used with a different provider")
        if not url.startswith("https://"):
            raise SiteError(site.id, "security", "only HTTPS resources are allowed")
        self._load(url)
        body = self._page.content().encode("utf-8")
        if len(body) > self.max_response_bytes:
            raise SiteError(site.id, "response_too_large", "response exceeds configured size limit")
        return body.decode("utf-8", errors="replace")

    def fetch_bytes(self, url: str, site: SiteConfig, *, accept: str = "*/*") -> bytes:
        if site.id != self.site.id:
            raise SiteError(site.id, "configuration", "browser transport used with a different provider")
        if not url.startswith("https://"):
            raise SiteError(site.id, "security", "only HTTPS resources are allowed")
        self._start()
        timeout_ms = int((site.timeout or self.timeout) * 1000)
        try:
            response = self._context.request.get(url, headers={"Accept": accept}, timeout=timeout_ms)
            if not response.ok:
                raise SiteError(site.id, "http", f"HTTP {response.status}")
            body = response.body()
            if len(body) > self.max_response_bytes:
                raise SiteError(site.id, "response_too_large", "response exceeds configured size limit")
            return body
        except SiteError:
            raise
        except Exception as exc:
            raise SiteError(site.id, "browser", "browser resource request failed") from exc

    def post_json(self, url: str, payload: dict, site: SiteConfig) -> dict:
        if site.id != self.site.id:
            raise SiteError(site.id, "configuration", "browser transport used with a different provider")
        if not url.startswith("https://"):
            raise SiteError(site.id, "security", "only HTTPS resources are allowed")
        self._start()
        timeout_ms = int((site.timeout or self.timeout) * 1000)
        try:
            response = self._context.request.post(
                url,
                data=json.dumps(payload, ensure_ascii=False),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=timeout_ms,
            )
            if not response.ok:
                raise SiteError(site.id, "http", f"HTTP {response.status}")
            body = response.body()
            if len(body) > self.max_response_bytes:
                raise SiteError(site.id, "response_too_large", "response exceeds configured size limit")
            result = json.loads(body.decode("utf-8", errors="replace"))
            if not isinstance(result, dict):
                raise SiteError(site.id, "parser", "JSON response must be an object")
            return result
        except SiteError:
            raise
        except Exception as exc:
            raise SiteError(site.id, "browser", "browser JSON request failed") from exc

    def close(self) -> None:
        if self._owns_page and self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
        if self._owns_context and self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        if self._owns_browser and self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._owns_browser = False
        self._owns_context = False
        self._owns_page = False
