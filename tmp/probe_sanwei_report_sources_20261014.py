#!/usr/bin/env python3
from __future__ import annotations

import re
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

inner = "http://www.sgpjbg.com/sgpjbg/View.aspx?id=399121"
URLS = [
    "https://www.fxbaogao.com/view?id=4736599",
    "https://www.fxbaogao.com/view?id=4665308",
    "https://www.sgpjbg.com/sgpjbg/View.aspx?id=399121",
    "https://www.sgpjbg.com/BookRead.aspx?bhtype=2&url=" + quote(inner, safe=""),
    "https://www.sgpjbg.com/baogao/651928.html",
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}

for url in URLS:
    print("\n" + "=" * 120)
    print("URL", url)
    try:
        r = requests.get(url, headers=headers, timeout=(20, 120), allow_redirects=True)
        print("STATUS", r.status_code, "LEN", len(r.content), "FINAL", r.url, "CTYPE", r.headers.get("content-type"))
        raw = r.content
        text = None
        for enc in (r.encoding, "utf-8", "gb18030"):
            if not enc:
                continue
            try:
                text = raw.decode(enc)
                break
            except Exception:
                pass
        if text is None:
            text = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        print("TITLE", soup.title.get_text(" ", strip=True) if soup.title else "")
        found = []
        for tag in soup.find_all(["a", "iframe", "embed", "object", "script", "img", "source", "link"]):
            for attr in ("href", "src", "data", "data-src", "data-url", "url", "value"):
                val = tag.get(attr)
                if not val:
                    continue
                full = urljoin(r.url, val)
                label = tag.get_text(" ", strip=True)[:120]
                low = full.lower()
                if any(k in low for k in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", "download", "view", "report", "file", "attachment", "preview", "page", "bookread", "flexpaper")) or any(k in label.lower() for k in ("下载", "pdf", "完整", "原文")):
                    found.append((tag.name, attr, full, label))
        seen = set()
        for item in found:
            if item[2] in seen:
                continue
            seen.add(item[2])
            print("LINK", item)
        for pattern in (
            r"\.pdf",
            r"report-image",
            r"fileroot",
            r"page(?:url|image|src|count|index)?",
            r"image(?:url|src|list)?",
            r"download",
            r"fileUrl",
            r"pdfUrl",
            r"attachment",
            r"bookread",
            r"view\.aspx",
            r"399121",
            r"4736599",
            r"4665308",
        ):
            hits = list(re.finditer(pattern, text, flags=re.I))
            print("PATTERN", pattern, "HITS", len(hits))
            for m in hits[:20]:
                start = max(0, m.start() - 250)
                end = min(len(text), m.end() + 420)
                snippet = re.sub(r"\s+", " ", text[start:end])
                print("SNIP", snippet[:900])
    except Exception as exc:
        print("ERROR", type(exc).__name__, exc)
