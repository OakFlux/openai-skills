#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image

OUT = Path("debon_sanwei_probe")
OUT.mkdir(exist_ok=True)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
REPORT_URL = "https://www.sgpjbg.com/baogao/130314.html"
results: dict[str, object] = {"report_url": REPORT_URL}

r = requests.get(REPORT_URL, headers=HEADERS, timeout=(20, 120), allow_redirects=True)
r.raise_for_status()
text = r.content.decode(r.encoding or "utf-8", errors="replace")
(OUT / "report_130314.html").write_text(text, encoding="utf-8")
soup = BeautifulSoup(text, "html.parser")

print("STATUS", r.status_code, "BYTES", len(r.content), "FINAL", r.url)
print("TITLE", soup.title.get_text(" ", strip=True) if soup.title else "")

bases: list[str] = []
dp = soup.find(id="dp")
if dp and dp.get("value"):
    bases.append(str(dp.get("value")))
    print("DP", dp.get("value"))
for tag in soup.find_all(True):
    for value in tag.attrs.values():
        vals = value if isinstance(value, list) else [value]
        for val in vals:
            s = str(val)
            if "file.sgpjbg.com/fileroot2/2023-6/21/" in s:
                m = re.search(r"https?://[^\"'<>\s]+", s)
                if m:
                    candidate = m.group(0)
                    candidate = re.sub(r"(?:\d+\.(?:gif|png|jpg|jpeg|webp))?.*$", "", candidate, flags=re.I)
                    bases.append(candidate)
                    print("ATTR_BASE", candidate)
for m in re.finditer(r"https?://file\.sgpjbg\.com/fileroot2/2023-6/21/[^\"'<>\s]+", text, flags=re.I):
    candidate = m.group(0)
    candidate = re.sub(r"(?:\d+\.(?:gif|png|jpg|jpeg|webp))?.*$", "", candidate, flags=re.I)
    bases.append(candidate)
    print("TEXT_BASE", candidate)

bases = list(dict.fromkeys(x.rstrip("/") for x in bases if x))
print("BASES", bases)
results["bases"] = bases

all_tests = []
for base in bases:
    base_rec: dict[str, object] = {"base": base, "pages": []}
    pages_list: list[dict[str, object]] = []
    consecutive = 0
    for page in range(1, 36):
        ok = False
        attempts = []
        for ext in ("gif", "png", "jpg", "webp"):
            url = f"{base}{page}.{ext}"
            try:
                resp = requests.get(url, headers={**HEADERS, "Referer": REPORT_URL}, timeout=(15, 60), allow_redirects=True)
                rec = {"url": url, "status": resp.status_code, "bytes": len(resp.content), "content_type": resp.headers.get("content-type")}
                attempts.append(rec)
                if resp.status_code != 200 or len(resp.content) < 10000:
                    continue
                image = Image.open(BytesIO(resp.content))
                image.load()
                w, h = image.size
                if w < 500 or h < 500:
                    continue
                sha = hashlib.sha256(resp.content).hexdigest()
                page_rec = {"page": page, "url": resp.url, "bytes": len(resp.content), "format": image.format, "width": w, "height": h, "sha256": sha}
                pages_list.append(page_rec)
                print("OK", page_rec)
                consecutive += 1
                ok = True
                break
            except Exception as exc:
                attempts.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
        if not ok:
            print("MISS", page, attempts)
            base_rec["first_missing_page"] = page
            break
    base_rec["pages"] = pages_list
    base_rec["consecutive_pages"] = consecutive
    print("SEQUENCE", base, consecutive)
    all_tests.append(base_rec)
results["tests"] = all_tests
(OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
