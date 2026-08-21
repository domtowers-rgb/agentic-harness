import ipaddress
import socket
from urllib.parse import urlparse

import httpx

MAX_CHARS = 20_000
TIMEOUT_SECONDS = 10.0


def _is_safe_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def fetch_url(url: str):
    """Fetch a public http(s) URL. Refuses private/internal addresses and
    does not follow redirects, to reduce SSRF risk from a URL the model was
    handed by untrusted page content."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "only http/https URLs are allowed"}
    if not parsed.hostname or not _is_safe_host(parsed.hostname):
        return {"error": "refusing to fetch a private, loopback, or internal address"}

    try:
        resp = httpx.get(
            url,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": "agentic-harness/0.1"},
        )
    except Exception as exc:
        return {"error": f"request failed: {exc}"}

    if resp.is_redirect:
        return {"status": resp.status_code, "redirect_to": resp.headers.get("location")}

    text = resp.text
    return {
        "status": resp.status_code,
        "content": text[:MAX_CHARS],
        "truncated": len(text) > MAX_CHARS,
    }


def register(registry):
    registry.register("fetch_url", fetch_url, {
        "name": "fetch_url",
        "description": "Fetch the text content of a public http(s) URL. Does not follow redirects.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The URL to fetch"}},
            "required": ["url"],
        },
    })
