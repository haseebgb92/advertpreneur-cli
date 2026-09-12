from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import List, Tuple


UA = "AdvertpreneurCLI/0.12 (+local engineering research)"


def _get(url: str, timeout: int = 20, max_bytes: int = 1_500_000) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes)
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript"}:
            self.skip += 1
        elif not self.skip and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "br", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"} and self.skip:
            self.skip -= 1
        elif not self.skip and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def search_web(query: str, max_results: int = 6) -> str:
    q_raw = str(query or "").strip()
    q = urllib.parse.quote_plus(q_raw)
    if not q:
        return "No query provided."
    limit = max(1, min(10, max_results))
    try:
        body = _get(f"https://html.duckduckgo.com/html/?q={q}")
        links = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, flags=re.I | re.S)
        snippets = re.findall(r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>', body, flags=re.I | re.S)
        rows: List[str] = []
        for i, (href, title_html) in enumerate(links[:limit]):
            href = html.unescape(href)
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                href = qs["uddg"][0]
            title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", title_html)).split())
            snippet = ""
            if i < len(snippets):
                snippet = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", snippets[i])).split())
            rows.append(f"{i+1}. {title}\n   {href}\n   {snippet[:420]}")
        if rows:
            return "\n".join(rows)
    except Exception:
        pass

    # Lightweight Bing HTML fallback. Search remains local; no LLM call is made.
    body = _get(f"https://www.bing.com/search?q={q}")
    blocks = re.findall(r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', body, flags=re.I | re.S)
    rows = []
    for block in blocks[:limit]:
        m = re.search(r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.I | re.S)
        if not m:
            continue
        href, title_html = m.groups()
        title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", title_html)).split())
        sm = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.I | re.S)
        snippet = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", sm.group(1) if sm else "")).split())
        rows.append(f"{len(rows)+1}. {title}\n   {html.unescape(href)}\n   {snippet[:420]}")
    return "\n".join(rows) if rows else "No search results parsed. The search providers may have blocked automated HTML access."


def fetch_web(url: str, max_chars: int = 14000) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("web_fetch supports only http/https URLs")
    body = _get(url)
    parser = _Text()
    parser.feed(body)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[: max(500, min(50000, int(max_chars)))] + ("\n…[web page clipped locally]" if len(text) > max_chars else "")
