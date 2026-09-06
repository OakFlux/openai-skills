#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
s = requests.Session()
s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
out = Path("ascletis_probe")
out.mkdir(exist_ok=True)

QUERIES = [
    '"Ascletis Pharma" research report PDF',
    '"Ascletis Pharma" broker report',
    '"Ascletis Pharma" CMBI',
    '"Ascletis Pharma" Phillip Securities',
    '"歌礼制药" 券商 深度报告 PDF',
    '"歌礼制药" 研究报告 PDF',
    '"01672" "research report" PDF',
    '"1672.HK" research report',
]


def decode(r: requests.Response) -> str:
    choices = []
    for enc in (r.encoding, r.apparent_encoding, "utf-8", "gb18030"):
        if not enc:
            continue
        try:
            txt = r.content.decode(enc)
            choices.append((txt.count("�"), -len(txt), txt))
        except Exception:
            pass
    if not choices:
        return r.content.decode("utf-8", errors="replace")
    choices.sort()
    return choices[0][2]


def norm_url(value: str, base: str = "") -> str:
    value = unquote(value or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    if base:
        value = urljoin(base, value)
    if "uddg=" in value:
        try:
            q = parse_qs(urlparse(value).query)
            if q.get("uddg"):
                value = q["uddg"][0]
        except Exception:
            pass
    return value


def search_duckduckgo(query: str) -> list[dict]:
    url = "https://html.duckduckgo.com/html/"
    try:
        r = s.get(url, params={"q": query}, timeout=(20, 90))
        text = decode(r)
        soup = BeautifulSoup(text, "html.parser")
        results = []
        for block in soup.select(".result"):
            a = block.select_one("a.result__a")
            if not a:
                continue
            href = norm_url(a.get("href", ""), r.url)
            snippet = block.select_one(".result__snippet")
            results.append({
                "engine": "duckduckgo",
                "query": query,
                "title": a.get_text(" ", strip=True),
                "url": href,
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            })
        print("DDG", query, r.status_code, len(r.content), len(results), flush=True)
        for item in results[:20]:
            print("SEARCH_RESULT", json.dumps(item, ensure_ascii=False), flush=True)
        return results
    except Exception as exc:
        print("DDG_ERROR", query, repr(exc), flush=True)
        return []


def search_bing(query: str) -> list[dict]:
    try:
        r = s.get("https://www.bing.com/search", params={"q": query, "count": 50}, timeout=(20, 90))
        text = decode(r)
        soup = BeautifulSoup(text, "html.parser")
        results = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a:
                continue
            p = li.select_one(".b_caption p")
            results.append({
                "engine": "bing",
                "query": query,
                "title": a.get_text(" ", strip=True),
                "url": norm_url(a.get("href", ""), r.url),
                "snippet": p.get_text(" ", strip=True) if p else "",
            })
        print("BING", query, r.status_code, len(r.content), len(results), flush=True)
        for item in results[:20]:
            print("SEARCH_RESULT", json.dumps(item, ensure_ascii=False), flush=True)
        return results
    except Exception as exc:
        print("BING_ERROR", query, repr(exc), flush=True)
        return []


def eastmoney_query(begin: str, end: str, code: str, qtype: int) -> list[dict]:
    api = "https://reportapi.eastmoney.com/report/list"
    rows = []
    page = 1
    total = 1
    while page <= total and page <= 30:
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*", "rating": "*",
            "ratingChange": "*", "beginTime": begin, "endTime": end, "pageNo": page,
            "fields": "", "qType": qtype, "orgCode": "", "code": code, "rcode": "",
            "p": page, "pageNum": page, "pageNumber": page,
        }
        try:
            r = s.get(api, params=params, timeout=(20, 120))
            payload = r.json()
            total = int(payload.get("TotalPage") or 1)
            data = payload.get("data") or []
            print("EASTMONEY", begin, end, code, qtype, page, r.status_code, total, len(data), flush=True)
            rows.extend(data)
        except Exception as exc:
            print("EASTMONEY_ERROR", begin, end, code, qtype, page, repr(exc), flush=True)
            break
        page += 1
        time.sleep(0.1)
    return rows


def scan_eastmoney() -> list[dict]:
    rows = []
    for code in ("01672", "1672", "01672.HK", "1672.HK"):
        for qtype in (0, 1, 2):
            rows.extend(eastmoney_query("2018-01-01", "2026-09-06", code, qtype))
    # Short broad windows around likely annual-results seasons, filtering locally.
    windows = [
        ("2026-03-01", "2026-04-30"), ("2025-03-01", "2025-04-30"),
        ("2024-03-01", "2024-04-30"), ("2023-03-01", "2023-04-30"),
        ("2022-03-01", "2022-04-30"), ("2021-03-01", "2021-04-30"),
        ("2020-03-01", "2020-04-30"), ("2019-03-01", "2019-04-30"),
        ("2026-08-01", "2026-09-06"), ("2025-08-01", "2025-09-30"),
        ("2024-08-01", "2024-09-30"), ("2023-08-01", "2023-09-30"),
    ]
    for begin, end in windows:
        data = eastmoney_query(begin, end, "", 0)
        for row in data:
            hay = " ".join(str(row.get(k) or "") for k in ("title", "stockName", "stockCode", "orgName", "orgSName"))
            if any(k.lower() in hay.lower() for k in ("歌礼", "Ascletis", "01672", "1672.HK")):
                rows.append(row)
    seen = set()
    hits = []
    for row in rows:
        key = str(row.get("infoCode") or json.dumps(row, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        hay = " ".join(str(row.get(k) or "") for k in ("title", "stockName", "stockCode", "orgName", "orgSName"))
        if any(k.lower() in hay.lower() for k in ("歌礼", "Ascletis", "01672", "1672.HK")):
            hits.append(row)
            print("EASTMONEY_MATCH", json.dumps(row, ensure_ascii=False, default=str), flush=True)
    return hits


def scan_cmbi_pages() -> list[dict]:
    hits = []
    # CMBI archive is paginated; scan enough pages to cover the listing period.
    for lang in ("en", "cn", "tc"):
        consecutive_empty = 0
        for page in range(1, 420):
            try:
                url = "https://www.cmbi.com.hk/market-stockreview"
                r = s.get(url, params={"lang": lang, "page": page}, timeout=(15, 60))
                text = decode(r)
                low = text.lower()
                if any(key in low for key in ("ascletis", "歌礼", "歌禮", "01672")):
                    soup = BeautifulSoup(text, "html.parser")
                    for a in soup.find_all("a", href=True):
                        context = " ".join([a.get_text(" ", strip=True), a.parent.get_text(" ", strip=True) if a.parent else ""])
                        if any(key in context.lower() for key in ("ascletis", "歌礼", "歌禮", "01672")):
                            item = {"lang": lang, "page": page, "title": context[:500], "url": norm_url(a["href"], r.url)}
                            hits.append(item)
                            print("CMBI_MATCH", json.dumps(item, ensure_ascii=False), flush=True)
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
                if page % 50 == 0:
                    print("CMBI_PROGRESS", lang, page, r.status_code, len(r.content), flush=True)
                # Archive pages beyond their available range often repeat/empty.
                if consecutive_empty > 220 and page > 250:
                    break
            except Exception as exc:
                print("CMBI_ERROR", lang, page, repr(exc), flush=True)
            time.sleep(0.03)
    return hits


def inspect_candidate_pages(search_results: list[dict]) -> list[dict]:
    hits = []
    seen = set()
    for item in search_results:
        url = item.get("url", "")
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        if any(x in url.lower() for x in ("google.com", "bing.com", "duckduckgo.com")):
            continue
        try:
            r = s.get(url, timeout=(20, 90), allow_redirects=True)
            text = decode(r)
            soup = BeautifulSoup(text, "html.parser")
            links = []
            for tag in soup.find_all(True):
                for attr in ("href", "src", "data-src", "data-url", "content"):
                    value = tag.get(attr)
                    if isinstance(value, str) and value.strip():
                        u = norm_url(value.strip(), r.url)
                        if ".pdf" in u.lower() or any(k in u.lower() for k in ("download", "attachment", "report")):
                            links.append(u)
            for u in re.findall(r'(?:https?:)?//[^\s\"\'<>]+', text):
                u = norm_url(u, r.url).rstrip('),];}\"\'')
                if ".pdf" in u.lower():
                    links.append(u)
            links = list(dict.fromkeys(links))
            rec = {
                "source": item,
                "requested": url,
                "resolved": r.url,
                "status": r.status_code,
                "content_type": r.headers.get("content-type"),
                "bytes": len(r.content),
                "title": soup.title.get_text(" ", strip=True) if soup.title else "",
                "pdf_links": links[:100],
            }
            hits.append(rec)
            print("PAGE_INSPECT", json.dumps(rec, ensure_ascii=False, default=str), flush=True)
        except Exception as exc:
            print("PAGE_ERROR", url, repr(exc), flush=True)
    return hits


def main() -> None:
    search_results = []
    for query in QUERIES:
        search_results.extend(search_duckduckgo(query))
        search_results.extend(search_bing(query))
    eastmoney = scan_eastmoney()
    cmbi = scan_cmbi_pages()
    pages = inspect_candidate_pages(search_results + [{"url": x.get("url", ""), "title": x.get("title", ""), "engine": "cmbi"} for x in cmbi])
    output = {
        "search_results": search_results,
        "eastmoney_matches": eastmoney,
        "cmbi_matches": cmbi,
        "inspected_pages": pages,
    }
    Path("ascletis_probe_results.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("SUMMARY", {k: len(v) for k, v in output.items()}, flush=True)


if __name__ == "__main__":
    main()
