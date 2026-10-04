import ipaddress
import os
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urlparse, urlunparse

import httpx

# Applied to the extracted text, not the raw HTML - a whole tool result
# goes into the model's context, so keep it well inside a small local
# model's window. Raise it for a model with more room.
MAX_CHARS = int(os.environ.get("AGENTIC_FETCH_MAX_CHARS", "8000"))
TIMEOUT_SECONDS = 10.0
DNS_RETRY_DELAY_SECONDS = 1.0
# Descriptive, with a link back to the project - some sites (Wikipedia, for
# one) refuse requests from a bare generic agent string under their bot
# policies.
USER_AGENT = "agentic-harness/0.1 (+https://github.com/domtowers-rgb/agentic-harness)"

# Content of these is never readable text (code, styling, graphics) or is
# page furniture repeated on every page (menus, footers, sidebars, forms) -
# dropped entirely rather than spending the model's context on it.
_SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "canvas", "iframe", "object",
    "head", "nav", "footer", "aside", "form", "button", "select",
}
# Void elements never get an end tag, so they mustn't count toward the
# skip-depth bookkeeping below.
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# The same kind of page furniture, marked by ARIA role instead of a tag -
# e.g. <div role="navigation">, common on sites that predate <nav>.
_SKIP_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "menu", "menubar", "dialog"}
# ...or marked only by a class/id naming it as such - the usual
# reader-mode heuristic. Matched as a whole hyphen/underscore-separated
# word, and kept to names that are rarely real content (no "menu", say,
# which could be a restaurant's actual menu).
_SKIP_CLASS_RE = re.compile(
    r"(?:^|[\s_-])(?:dropdown|sidebar|breadcrumbs?|cookies?|consent|social|share|navbox|related|advert|adverts|newsletter|popup)(?:$|[\s_-])",
    re.IGNORECASE,
)
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "br", "hr", "li", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "pre", "blockquote", "figcaption", "ul", "ol",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []  # text from the whole page
        self.main_parts = []  # text from inside <main> / role="main" only
        self.title = ""
        # Only the document's own <title> counts - the first one. Later
        # ones are labels inside inline <svg> icons, and would otherwise
        # pile up into the title ("...double quotation mark" x10).
        self._in_title = False
        self._title_done = False
        self._main_tag = None  # same open-tag bookkeeping as _skip_tag below
        self._main_nesting = 0
        # While skipping, the tag that started it and how deeply that same
        # tag is nested inside itself - so its own matching end tag (not
        # just any end tag of that name) is what ends the skip.
        self._skip_tag = None
        self._skip_nesting = 0

    def _emit(self, text):
        self.parts.append(text)
        if self._main_tag:
            self.main_parts.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        role = (attrs.get("role") or "").lower()
        if tag == "title" and not self._title_done:
            self._in_title = True
        elif self._skip_tag:
            if tag == self._skip_tag:
                self._skip_nesting += 1
        elif tag not in _VOID_TAGS and (
            tag in _SKIP_TAGS or role in _SKIP_ROLES
            or "hidden" in attrs or (attrs.get("aria-hidden") or "").lower() == "true"
            or (tag not in ("html", "body", "main") and _SKIP_CLASS_RE.search(f"{attrs.get('class') or ''} {attrs.get('id') or ''}"))
        ):
            self._skip_tag, self._skip_nesting = tag, 1
        else:
            if self._main_tag:
                if tag == self._main_tag:
                    self._main_nesting += 1
            elif tag == "main" or role == "main":
                self._main_tag, self._main_nesting = tag, 1
            if tag in _BLOCK_TAGS:
                self._emit("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        elif self._skip_tag:
            if tag == self._skip_tag:
                self._skip_nesting -= 1
                if not self._skip_nesting:
                    self._skip_tag = None
        else:
            if tag in _BLOCK_TAGS:
                self._emit("\n")
            if tag == self._main_tag:
                self._main_nesting -= 1
                if not self._main_nesting:
                    self._main_tag = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_tag:
            self._emit(data)


def html_to_text(html: str):
    """Readable text from an HTML page, plus its <title>: scripts, styles,
    menus, footers, hidden elements and the like dropped, entities decoded,
    whitespace collapsed, one line per block element. If the page marks its
    main content (<main> or role="main"), only that is kept - which drops
    cookie banners and other furniture outside it. Returns (title, text).
    Standard library only - deliberately simple rather than a full
    readability algorithm, but typically a small fraction of the raw
    HTML's size."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass  # malformed markup - keep whatever was extracted before it
    raw = "".join(parser.main_parts) if "".join(parser.main_parts).strip() else "".join(parser.parts)
    lines = (re.sub(r"[ \t\r\f\v\u00a0]+", " ", line).strip() for line in raw.split("\n"))
    text = "\n".join(line for line in lines if line and line != "-")
    return " ".join(parser.title.split()), text


def _resolve_pinned_ip(hostname: str):
    """Resolve hostname and return (ip, None) - one validated IP literal to
    connect to directly - or (None, error) if resolution failed or any
    resolved address is private/internal. The two failures get different
    errors: a temporary DNS hiccup reported as "private address" sends the
    model (and whoever reads the reply) in the wrong direction.

    We deliberately connect to this pinned IP instead of handing the
    hostname to the HTTP client. If we didn't, the client would do its own,
    separate DNS resolution at connect time - a gap an attacker-controlled
    DNS server can exploit (DNS rebinding) by answering this check with a
    public IP and the real connection moments later with an internal one.
    """
    try:
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror as exc:
            # EAI_AGAIN is the resolver's own "temporary failure, try
            # again" - seen intermittently on a real setup - so it gets one
            # quick retry. Anything else (e.g. no such host) fails at once.
            if exc.errno != socket.EAI_AGAIN:
                raise
            time.sleep(DNS_RETRY_DELAY_SECONDS)
            infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return None, f"could not look up {hostname} ({exc.strerror or exc}) - it may not exist, or DNS may be temporarily unavailable"
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return None, "refusing to fetch a private, loopback, or internal address"
        ips.append(info[4][0])
    if not ips:
        return None, f"could not look up {hostname}"
    return ips[0], None


def fetch_url(url: str):
    """Fetch a public http(s) URL. Refuses private/internal addresses and
    does not follow redirects, to reduce SSRF risk from a URL the model was
    handed by untrusted page content."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "only http/https URLs are allowed"}
    if not parsed.hostname:
        return {"error": "URL has no hostname"}

    pinned_ip, error = _resolve_pinned_ip(parsed.hostname)
    if error:
        return {"error": error}

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
                headers={"User-Agent": USER_AGENT, "Host": parsed.hostname},
                extensions={"sni_hostname": parsed.hostname},
            )
            resp = client.send(request)
    except Exception as exc:
        return {"error": f"request failed: {exc}"}

    if resp.is_redirect:
        return {"status": resp.status_code, "redirect_to": resp.headers.get("location")}

    result = {"status": resp.status_code}
    content_type = resp.headers.get("content-type", "").lower()
    if "html" in content_type:
        title, text = html_to_text(resp.text)
        if title:
            result["title"] = title
    elif content_type.startswith("text/") or "json" in content_type or "xml" in content_type or not content_type:
        text = resp.text
    else:
        return {**result, "error": f"not a text page (content-type: {content_type}) - can't show its content"}

    result["content"] = text[:MAX_CHARS]
    result["truncated"] = len(text) > MAX_CHARS
    return result


def register(registry):
    registry.register("fetch_url", fetch_url, {
        "name": "fetch_url",
        "description": "Fetch a public web page and return its readable text.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    })
