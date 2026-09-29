import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from backend.crawler import CrawlError, FixtureCrawler, _PinnedHTTPTransport, validate_target


def test_path_policy_decodes_traversal_before_checking():
    with pytest.raises(CrawlError, match="traversal"):
        validate_target("http://127.0.0.1/allowed/%2e%2e/admin", "http://127.0.0.1", ["/allowed"])


def test_path_policy_rejects_nested_encoding_and_encoded_allowed_paths():
    with pytest.raises(CrawlError, match="traversal"):
        validate_target("http://127.0.0.1/allowed/%252e%252e/admin", "http://127.0.0.1", ["/allowed"])
    with pytest.raises(CrawlError, match="traversal"):
        validate_target("http://127.0.0.1/allowed", "http://127.0.0.1", ["/allowed/%252e%252e/admin"])


@pytest.mark.parametrize(
    ("url", "base_url"),
    [
        ("https://example.com:80/path", "http://example.com:80/"),
        ("http://example.com:443/path", "https://example.com:443/"),
    ],
)
def test_target_rejects_scheme_changes_even_when_host_and_port_match(url, base_url):
    with pytest.raises(CrawlError, match="cross-origin"):
        validate_target(url, base_url, ["/"])


def test_pinned_transport_connects_to_validated_ip_without_resolving_site_host():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"pinned"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = _PinnedHTTPTransport("fixture.invalid", {ipaddress.ip_address("127.0.0.1")})
    try:
        with httpx.Client(transport=transport, trust_env=False) as client:
            response = client.get(f"http://fixture.invalid:{server.server_port}/")
        assert response.status_code == 200
        assert response.text == "pinned"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_redirect_is_revalidated_before_following():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    crawler = FixtureCrawler(client=client)
    try:
        with pytest.raises(CrawlError, match="cross-origin"):
            crawler.fetch("http://127.0.0.1/start", base_url="http://127.0.0.1", allowed_paths=["/"], allow_loopback=True)
    finally:
        client.close()
    assert len(requests) == 1
