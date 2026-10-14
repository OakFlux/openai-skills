#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

OUT = Path("debon_sanwei_probe")
OUT.mkdir(exist_ok=True)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    "Referer": "https://www.sgpjbg.com/baogao/130314.html",
}
folder = "https://file.sgpjbg.com/fileroot2/2023-6/21/c08c0704-f787-422b-92c4-9a1faca49570/"
uuid = "c08c0704-f787-422b-92c4-9a1faca49570"
cover = "8104571cd984465bbe28e038f0b6c801"
patterns = [
    folder + uuid + "{page}.{ext}",
    folder + cover + "{page}.{ext}",
    folder + cover + "_{page}.{ext}",
    folder + "{page}.{ext}",
]
results = []
for pattern in patterns:
    rec = {"pattern": pattern, "pages": []}
    pages = []
    for page in range(1, 36):
        found = None
        attempts = []
        for ext in ("gif", "png", "jpg", "webp"):
            url = pattern.format(page=page, ext=ext)
            try:
                r = requests.get(url, headers=HEADERS, timeout=(15, 60), allow_redirects=True)
                attempts.append({"url": url, "status": r.status_code, "bytes": len(r.content), "type": r.headers.get("content-type")})
                if r.status_code != 200 or len(r.content) < 10000:
                    continue
                image = Image.open(BytesIO(r.content))
                image.load()
                w, h = image.size
                if w < 500 or h < 500:
                    continue
                found = {
                    "page": page,
                    "url": r.url,
                    "bytes": len(r.content),
                    "format": image.format,
                    "width": w,
                    "height": h,
                    "sha256": hashlib.sha256(r.content).hexdigest(),
                }
                break
            except Exception as exc:
                attempts.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
        if not found:
            rec["first_missing_page"] = page
            rec["missing_attempts"] = attempts
            print("MISS", pattern, page, attempts)
            break
        pages.append(found)
        print("OK", pattern, found)
    rec["pages"] = pages
    rec["consecutive_pages"] = len(pages)
    results.append(rec)
    print("SEQUENCE", pattern, len(pages))
(OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
