#!/usr/bin/env python3
"""WebPeek gateway - the HTTPS half of a System 7 web fetch.

A 68K Macintosh cannot speak modern TLS, so the guest app sends one line -
a URL - over plain TCP, and this gateway does the https:// fetch on its
behalf, strips the HTML down to text, folds it to MacRoman-safe ASCII with
CR line endings, and streams it back. Connection close marks the end.

Runs with /usr/bin/python3 (stdlib only), listens on 0.0.0.0:9080 so the
emulated Mac can reach it through slirp at the host's LAN address.
"""
import socket
import socketserver
import ssl
import unicodedata
import urllib.request
from html.parser import HTMLParser

PORT = 9080
MAX_BYTES = 28000          # TextEdit tops out at 32K; leave headroom
WRAP = 76

class TextExtract(HTMLParser):
    SKIP = {"script", "style", "head", "title"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "ul", "ol",
             "table", "blockquote", "pre", "hr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.depth = 0
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        if tag == "title":
            self.in_title = True
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        if tag == "title":
            self.in_title = False
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data.strip()
        elif not self.depth:
            self.out.append(data)

def fetch_as_text(url):
    if "://" not in url:
        url = "https://" + url
    req = urllib.request.Request(url, headers={
        "User-Agent": "WebPeek/1.0 (Macintosh; 68K; System 7.6.1; via AppleBridge)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read(400000)
        charset = r.headers.get_content_charset() or "utf-8"
        final = r.geturl()
    p = TextExtract()
    p.feed(raw.decode(charset, errors="replace"))
    text = "".join(p.out)
    lines = []
    if p.title:
        lines += [p.title, "=" * min(len(p.title), WRAP), ""]
    lines.append("[" + final + "]")
    lines.append("")
    for para in text.split("\n"):
        words = para.split()
        if not words:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > WRAP:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            lines.append(cur)
    body = "\r".join(lines)
    # MacRoman-safe: normalize, then transliterate what survives
    body = unicodedata.normalize("NFKD", body)
    return body.encode("mac_roman", errors="replace")[:MAX_BYTES]

class Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def handle(self):
        try:
            line = self.rfile.readline(1024).decode("ascii", "replace").strip()
            if not line:
                return
            print(f"[gateway] fetch: {line}", flush=True)
            try:
                payload = fetch_as_text(line)
            except Exception as e:
                payload = ("WebPeek gateway error:\r" + str(e)).encode(
                    "mac_roman", errors="replace")
            self.wfile.write(payload)
            print(f"[gateway] sent {len(payload)} bytes", flush=True)
        except Exception as e:
            print(f"[gateway] handler error: {e}", flush=True)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

def _run_proxy_8080():
    # Netscape on the guest is already configured for 10.0.2.2:8080 (slirp's
    # host alias) - serve the same proxy there so its config needs no touching.
    with Server(("0.0.0.0", 8080), ProxyHandler) as p:
        print("[proxy] listening on 0.0.0.0:8080", flush=True)
        p.serve_forever()

def _main():
    import threading
    threading.Thread(target=_run_proxy, daemon=True).start()
    threading.Thread(target=_run_proxy_8080, daemon=True).start()
    with Server(("0.0.0.0", PORT), Handler) as srv:
        print(f"[gateway] listening on 0.0.0.0:{PORT}", flush=True)
        srv.serve_forever()

# ---------------------------------------------------------------------------
# HTTP proxy mode (port 9081): a real proxy for a real 1996 browser.
# Netscape 3 sends "GET http://host/path HTTP/1.0" to the proxy; we fetch the
# same URL over https upstream and pass the bytes back verbatim. The browser
# never sees TLS. https:// typed in the browser would arrive as CONNECT,
# which era-SSL cannot complete against a modern site - answered with 501.
# ---------------------------------------------------------------------------
PROXY_PORT = 9081

import re as _re
_SCRIPT_RE = _re.compile(rb"<script\b.*?</script>", _re.I | _re.S)

def _strip_scripts(html):
    """Navigator 3 executes JavaScript 1.1; a 2020s page's scripts only throw
    dialogs. Remove them, and the inline event handlers cannot fire either."""
    return _SCRIPT_RE.sub(b"", html)
MAX_PROXY_BYTES = 2000000

class ProxyHandler(socketserver.StreamRequestHandler):
    timeout = 40

    def handle(self):
        try:
            req = self.rfile.readline(2048).decode("latin-1", "replace").strip()
            while True:
                h = self.rfile.readline(2048)
                if not h or h in (b"\r\n", b"\n"):
                    break
            parts = req.split()
            if len(parts) < 2:
                return
            method, target = parts[0].upper(), parts[1]
            print(f"[proxy] {method} {target}", flush=True)
            if method == "CONNECT":
                self.wfile.write(b"HTTP/1.0 501 No era TLS - use http:// and let the proxy upgrade\r\n\r\n")
                return
            if target.startswith("http://"):
                upstream = "https://" + target[7:]
            elif target.startswith("https://"):
                upstream = target
            else:
                upstream = "https://" + target.lstrip("/")
            # Navigator 3 knows HTML, plain text, GIF and JPEG. Anything else
            # (JavaScript, CSS, JSON, web fonts, SVG, PNG) raises a "You have
            # started to download the file" alert - a MODAL that starves the
            # AppleBridge daemon (measured 2026-08-28, a Drupal js_*.js asset).
            # Answer those with an empty 200 of a type it accepts, so the
            # page finishes loading without a dialog.
            low = upstream.lower().split("?", 1)[0]
            if low.endswith((".js", ".css", ".json", ".woff", ".woff2", ".ttf",
                             ".svg", ".png", ".ico", ".webp", ".map")):
                body, ctype, status = b"", "text/plain", 200
                self.wfile.write((f"HTTP/1.0 200 OK\r\nContent-Type: {ctype}\r\n"
                                  f"Content-Length: 0\r\nConnection: close\r\n\r\n").encode("latin-1"))
                print(f"[proxy] swallowed asset {upstream}", flush=True)
                return
            try:
                rq = urllib.request.Request(upstream, headers={
                    "User-Agent": "Mozilla/3.04 (Macintosh; 68K) via WebPeek proxy",
                    "Accept": "*/*"})
                with urllib.request.urlopen(rq, timeout=30) as r:
                    body = r.read(MAX_PROXY_BYTES)
                    ctype = r.headers.get("Content-Type", "text/html")
                    base = ctype.split(";")[0].strip().lower()
                    if base in ("text/html", "application/xhtml+xml"):
                        body = _strip_scripts(body)
                        ctype = "text/html"
                    elif not (base.startswith("image/gif") or base.startswith("image/jpeg")
                              or base.startswith("text/")):
                        body, ctype = b"", "text/plain"     # unknown to a 1996 browser
                status = 200
            except urllib.error.HTTPError as e:
                body = e.read(200000) if e.fp else str(e).encode()
                ctype = e.headers.get("Content-Type", "text/html") if e.headers else "text/html"
                status = e.code
            except Exception as e:
                body = ("<html><body><h1>Proxy error</h1><pre>%s</pre></body></html>"
                        % str(e)).encode("latin-1", "replace")
                ctype = "text/html"
                status = 502
            head = (f"HTTP/1.0 {status} OK\r\nContent-Type: {ctype}\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
            self.wfile.write(head.encode("latin-1") + body)
            print(f"[proxy] {status} {len(body)}b {ctype.split(';')[0]}", flush=True)
        except Exception as e:
            print(f"[proxy] handler error: {e}", flush=True)

def _run_proxy():
    with Server(("0.0.0.0", PROXY_PORT), ProxyHandler) as p:
        print(f"[proxy] listening on 0.0.0.0:{PROXY_PORT}", flush=True)
        p.serve_forever()

if __name__ == "__main__":
    _main()
