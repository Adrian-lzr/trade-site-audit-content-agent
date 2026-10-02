from __future__ import annotations

import ipaddress
import json
import posixpath
import socket
from time import monotonic, sleep
from dataclasses import dataclass
from collections.abc import Callable
from urllib.parse import unquote, urljoin, urlparse

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend

from .config import settings
from .document_parser import parse_document


class CrawlError(ValueError):
    pass


class RobotsBlocked(CrawlError):
    pass


@dataclass(frozen=True)
class CrawlResult:
    url: str
    status_code: int
    headers: dict[str, str]
    content: str
    title: str | None
    error: str | None = None
    redirect_chain: tuple[dict[str, object], ...] = ()


def _host_ips(host: str) -> set[ipaddress._BaseAddress]:
    try:
        address = ipaddress.ip_address(host)
        return {getattr(address, "ipv4_mapped", None) or address}
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise CrawlError(f"host cannot be resolved: {host}") from exc
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
        return {getattr(address, "ipv4_mapped", None) or address for address in addresses}


def _host_key(host: str) -> str:
    return host.rstrip(".").encode("idna").decode("ascii").lower()


def _validated_target(url: str, base_url: str, allowed_paths: list[str], *, allow_loopback: bool) -> tuple[str, set[ipaddress._BaseAddress]]:
    parsed = urlparse(url)
    base = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CrawlError("only http and https URLs are allowed")
    if parsed.username or parsed.password:
        raise CrawlError("credentials in URLs are not allowed")
    try:
        same_host = _host_key(parsed.hostname) == _host_key(base.hostname or "")
        same_port = parsed.port == base.port
    except (UnicodeError, ValueError) as exc:
        raise CrawlError("invalid target authority") from exc
    if parsed.scheme != base.scheme or not same_host or not same_port:
        raise CrawlError("cross-origin target is not allowed")
    normalized = normalize_path(parsed.path or "/")
    allowed = [normalize_path(path) for path in allowed_paths]
    if not any(normalized == prefix.rstrip("/") or normalized.startswith(prefix.rstrip("/") + "/") or prefix == "/" for prefix in allowed):
        raise CrawlError("path is outside the site's allowed paths")
    addresses = _host_ips(parsed.hostname)
    if not addresses:
        raise CrawlError(f"host cannot be resolved: {parsed.hostname}")
    for address in addresses:
        if address.is_loopback and allow_loopback:
            continue
        if not address.is_global:
            raise CrawlError("target resolves to a non-public address")
    return parsed.hostname, addresses


def normalize_path(path: str) -> str:
    if len(path) > 2048:
        raise CrawlError("URL path is too long")
    decoded = path or "/"
    while True:
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    if "\\" in decoded or "?" in decoded or "#" in decoded or any(ord(char) < 32 for char in decoded):
        raise CrawlError("invalid URL path")
    if any(segment == ".." for segment in decoded.split("/")):
        raise CrawlError("path traversal is not allowed")
    normalized = posixpath.normpath(decoded)
    return normalized if normalized.startswith("/") else "/" + normalized


def is_sitemap_url(value: str) -> bool:
    """Recognize common same-origin sitemap XML resource names."""

    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    filename = path.rstrip("/").rsplit("/", 1)[-1].lower()
    return filename.endswith(".xml") and filename.startswith(("sitemap", "wp-sitemap"))


def validate_target(url: str, base_url: str, allowed_paths: list[str], *, allow_loopback: bool = settings.allow_loopback) -> set[ipaddress._BaseAddress]:
    return _validated_target(url, base_url, allowed_paths, allow_loopback=allow_loopback)[1]


class _PinnedNetworkBackend(SyncBackend):
    def __init__(self, hostname: str, addresses: set[ipaddress._BaseAddress]) -> None:
        self.hostname = _host_key(hostname)
        self.addresses = tuple(sorted(addresses, key=lambda address: (address.version, int(address))))

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None, socket_options=None):
        try:
            matches_site_host = _host_key(host) == self.hostname
        except UnicodeError as exc:
            raise httpcore.ConnectError("request host does not match validated host") from exc
        if not matches_site_host:
            raise httpcore.ConnectError("request host does not match validated host")
        last_error = None
        for address in self.addresses:
            try:
                return super().connect_tcp(str(address), port, timeout=timeout, local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError("no validated addresses available")


class _PinnedHTTPTransport(httpx.HTTPTransport):
    def __init__(self, hostname: str, addresses: set[ipaddress._BaseAddress]) -> None:
        super().__init__(trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpcore.default_ssl_context(),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_PinnedNetworkBackend(hostname, addresses),
        )


class FixtureCrawler:
    def __init__(self, *, timeout: float = settings.request_timeout, max_redirects: int = settings.max_redirects, max_body_bytes: int = 2_000_000, request_interval: float = 1.0, client: httpx.Client | None = None):
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.max_body_bytes = max_body_bytes
        self.request_interval = max(0.0, request_interval)
        self._last_request_at: float | None = None
        self.client = client

    def fetch_control(self, url: str, *, base_url: str, allow_loopback: bool = False) -> CrawlResult:
        """Fetch only same-origin robots.txt or a sitemap XML control resource."""
        parsed = urlparse(url)
        path = (parsed.path or "/").lower()
        is_robots = path == "/robots.txt"
        is_sitemap = is_sitemap_url(url)
        if not (is_robots or is_sitemap):
            raise CrawlError("control resource must be robots.txt or a sitemap XML file")
        return self.fetch(url, base_url=base_url, allowed_paths=["/"], allow_loopback=allow_loopback)

    def fetch(self, url: str, *, base_url: str, allowed_paths: list[str], allow_loopback: bool = False, redirect_allowed: Callable[[str], bool] | None = None) -> CrawlResult:
        current = url
        redirect_chain: list[dict[str, object]] = []
        for _ in range(self.max_redirects + 1):
            hostname, addresses = _validated_target(
                current,
                base_url,
                allowed_paths,
                allow_loopback=settings.allow_loopback and allow_loopback,
            )
            client = None
            try:
                if self._last_request_at is not None and self.request_interval and not allow_loopback:
                    remaining = self.request_interval - (monotonic() - self._last_request_at)
                    if remaining > 0:
                        sleep(remaining)
                self._last_request_at = monotonic()
                if self.client is not None:
                    response_context = self.client.stream("GET", current)
                else:
                    client = httpx.Client(
                        transport=_PinnedHTTPTransport(hostname, addresses),
                        timeout=self.timeout,
                        follow_redirects=False,
                        trust_env=False,
                    )
                    response_context = client.stream("GET", current)
                with response_context as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return CrawlResult(str(response.url), response.status_code, dict(response.headers), "", None, error="redirect response has no Location header", redirect_chain=tuple(redirect_chain))
                        next_url = urljoin(current, location)
                        redirect_chain.append({"url": current, "status_code": response.status_code, "location": next_url})
                        if redirect_allowed is not None and not redirect_allowed(next_url):
                            raise RobotsBlocked("redirect target is disallowed or cannot be verified by robots.txt")
                        current = next_url
                        continue
                    declared_size = response.headers.get("content-length")
                    if declared_size and declared_size.isdigit() and int(declared_size) > self.max_body_bytes:
                        raise CrawlError("response exceeds the configured body size limit")
                    content_parts: list[bytes] = []
                    total = 0
                    async_parts = response.iter_bytes()
                    for chunk in async_parts:
                        total += len(chunk)
                        if total > self.max_body_bytes:
                            raise CrawlError("response exceeds the configured body size limit")
                        content_parts.append(chunk)
                    body = b"".join(content_parts)
                    encoding = response.encoding or "utf-8"
                    content = body.decode(encoding, errors="replace")
                    response_url = str(response.url)
                    status_code = response.status_code
                    headers = dict(response.headers)
            except httpx.HTTPError as exc:
                return CrawlResult(current, 0, {}, "", None, error=str(exc), redirect_chain=tuple(redirect_chain))
            except RobotsBlocked:
                raise
            finally:
                if client is not None:
                    client.close()
            title = parse_document(content, requested_url=url, final_url=response_url).title
            return CrawlResult(response_url, status_code, headers, content, title, redirect_chain=tuple(redirect_chain))
        return CrawlResult(current, 0, {}, "", None, error="too many redirects", redirect_chain=tuple(redirect_chain))


def allowed_paths_json(paths: list[str]) -> str:
    return json.dumps(paths, separators=(",", ":"))
