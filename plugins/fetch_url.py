import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

import httpx

MAX_CHARS = 20_000
TIMEOUT_SECONDS = 10.0


def _resolve_pinned_ip(hostname: str):
    """Resolve hostname and return one validated IP literal to connect to
    directly, or None if resolution failed or any resolved address is
    private/internal.

    We deliberately connect to this pinned IP instead of handing the
    hostname to the HTTP client. If we didn't, the client would do its own,
    separate DNS resolution at connect time - a gap an attacker-controlled
    DNS server can exploit (DNS rebinding) by answering this check with a
    public IP and the real connection moments later with an internal one.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return None
        ips.append(info[4][0])
    return ips[0] if ips else None


def fetch_url(url: str):
    """Fetch a public http(s) URL. Refuses private/internal addresses and
    does not follow redirects, to reduce SSRF risk from a URL the model was
    handed by untrusted page content."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "only http/https URLs are allowed"}
    if not parsed.hostname:
        return {"error": "URL has no hostname"}

    pinned_ip = _resolve_pinned_ip(parsed.hostname)
    if pinned_ip is None:
        return {"error": "refusing to fetch a private, loopback, or internal address"}

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    netloc = f"[{pinned_ip}]:{port}" if ":" in pinned_ip else f"{pinned_ip}:{port}"
    pinned_url = urlunparse(parsed._replace(netloc=netloc))

    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            # Host header set explicitly (rather than derived from the
            # pinned-IP URL) preserves virtual-hosting; sni_hostname keeps
            # TLS SNI and certificate-hostname checks validating against the
            # real domain instead of the IP.
            request = client.build_request(
                "GET",
                pinned_url,
                headers={"User-Agent": "agentic-harness/0.1", "Host": parsed.hostname},
                extensions={"sni_hostname": parsed.hostname},
            )
            resp = client.send(request)
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
