#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader

STOCK_CODE = "002469"
COMPANY = "山东三维化学集团股份有限公司"
SHORT_NAME = "三维化学"
OUT = Path("sanwei_chemical_broker_reports")
OUT.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
S = requests.Session()
S.headers.update(
    {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*,application/pdf;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "Referer": f"https://data.eastmoney.com/report/stock.jshtml?stockcode={STOCK_CODE}",
        "Connection": "keep-alive",
    }
)

EXCLUDE_TITLE_WORDS = (
    "年报点评",
    "中报点评",
    "半年报点评",
    "季报点评",
    "一季报点评",
    "三季报点评",
    "业绩点评",
    "业绩预告点评",
    "业绩快报点评",
    "公告点评",
    "事件点评",
    "点评报告",
    "简评",
)
PREFERRED_TITLE_WORDS = (
    "深度",
    "首次覆盖",
    "公司研究",
    "公司报告",
    "龙头",
    "硫磺回收",
    "工程",
    "正丙醇",
    "催化剂",
    "化工",
    "新材料",
    "转型",
    "成长",
    "价值",
    "布局",
    "领先",
    "一体化",
    "高分红",
)


def request(url: str, *, params: dict[str, Any] | None = None, timeout=(20, 180)) -> requests.Response:
    last: Exception | None = None
    for attempt in range(5):
        try:
            r = S.get(url, params=params, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
            time.sleep(min(8, 2**attempt))
    raise RuntimeError(f"request failed: {url}: {last}")


def parse_json_or_jsonp(text: str) -> Any:
    text = text.strip().lstrip("\ufeff")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"^[^(]*\((.*)\)\s*;?\s*$", text, flags=re.S)
        if m:
            return json.loads(m.group(1))
        m = re.search(r"=\s*(\{.*\}|\[.*\])\s*;?\s*$", text, flags=re.S)
        if m:
            return json.loads(m.group(1))
        raise


def collect_records(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if not isinstance(obj, dict):
        return []
    found: list[dict[str, Any]] = []
    for key in ("data", "result", "list", "records", "items"):
        val = obj.get(key)
        if isinstance(val, list):
            found.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            found.extend(collect_records(val))
    if not found:
        for val in obj.values():
            if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
                found.extend(val)
    return found


def field(row: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name])
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        val = lower.get(name.lower())
        if val is not None:
            return str(val)
    return ""


def int_field(row: dict[str, Any], *names: str) -> int:
    raw = field(row, *names)
    try:
        return int(float(raw))
    except Exception:
        return 0


def query_reports() -> list[dict[str, Any]]:
    endpoint = "https://reportapi.eastmoney.com/report/list"
    merged: list[dict[str, Any]] = []
    for qtype in (0, 1):
        params = {
            "pageSize": 100,
            "pageNo": 1,
            "industryCode": "*",
            "industry": "*",
            "rating": "*",
            "ratingchange": "*",
            "beginTime": "2010-01-01",
            "endTime": "2026-10-14",
            "fields": "",
            "qType": qtype,
            "orgCode": "",
            "code": STOCK_CODE,
            "rcode": "",
            "p": 1,
            "pageNum": 1,
            "pageNumber": 1,
            "_": int(time.time() * 1000),
        }
        variants = [params, {**params, "cb": f"datatable{int(time.time() * 1000)}"}]
        for variant in variants:
            try:
                response = request(endpoint, params=variant)
                rows = collect_records(parse_json_or_jsonp(response.text))
                print(f"REPORT QUERY qType={qtype}: {len(rows)} rows; {response.url}")
                merged.extend(rows)
                if rows:
                    break
            except Exception as exc:
                print(f"REPORT QUERY FAILED qType={qtype}: {exc}", file=sys.stderr)

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in merged:
        info = field(row, "infoCode", "INFOCODE", "artCode", "art_code")
        title = field(row, "title", "reportTitle", "REPORTTITLE")
        date = field(row, "publishDate", "PUBLISHDATE", "publish_date")
        key = (info, title, date)
        if key not in seen:
            seen.add(key)
            unique.append(row)

    print(f"UNIQUE REPORTS: {len(unique)}")
    for row in unique:
        print(
            "INDEX",
            field(row, "publishDate", "PUBLISHDATE"),
            field(row, "orgSName", "orgName", "ORGNAME"),
            int_field(row, "attachPages", "ATTACHPAGES"),
            field(row, "infoCode", "INFOCODE"),
            field(row, "title", "reportTitle", "REPORTTITLE"),
        )
    Path("sanwei_chemical_report_index.json").write_text(
        json.dumps(unique, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return unique


def title_score(title: str, pages: int, date: str) -> int:
    score = min(pages, 60) * 4
    if any(word in title for word in PREFERRED_TITLE_WORDS):
        score += 70
    if any(word in title for word in EXCLUDE_TITLE_WORDS):
        score -= 160
    if "点评" in title:
        score -= 80
    if "报告" in title and "点评" not in title:
        score += 20
    try:
        year = int(date[:4])
        score += max(0, min(25, year - 2010))
    except Exception:
        pass
    return score


def ranked_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        title = field(row, "title", "reportTitle", "REPORTTITLE")
        info = field(row, "infoCode", "INFOCODE", "artCode", "art_code")
        broker = field(row, "orgSName", "orgName", "ORGNAME")
        date = field(row, "publishDate", "PUBLISHDATE", "publish_date")[:10]
        pages = int_field(row, "attachPages", "ATTACHPAGES")
        stock_name = field(row, "stockName", "SECURITY_NAME_ABBR", "stock_name")
        if not info or not title or not broker:
            continue
        if stock_name and "三维" not in stock_name:
            continue
        if pages < 6:
            continue
        candidates.append(
            {
                "info_code": info,
                "title": title,
                "broker": broker,
                "date": date,
                "reported_pages": pages,
                "score": title_score(title, pages, date),
                "raw": row,
            }
        )
    candidates.sort(key=lambda x: (x["score"], x["reported_pages"], x["date"]), reverse=True)
    print("RANKED CANDIDATES:")
    for x in candidates[:30]:
        print(x["score"], x["date"], x["broker"], x["reported_pages"], x["info_code"], x["title"])
    return candidates


def pdf_urls(info_code: str) -> list[str]:
    return [
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}_1.pdf",
        f"http://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
    ]


def normalize(text: str) -> str:
    return re.sub(r"[\s　，,。；;：:（）()【】\[\]《》<>·•—–\-_]+", "", text or "").lower()


def extract_text(reader: PdfReader, max_pages: int = 10) -> str:
    chunks: list[str] = []
    for page in reader.pages[: min(max_pages, len(reader.pages))]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(chunks)


def validate_pdf(content: bytes, candidate: dict[str, Any]) -> tuple[int, str]:
    if len(content) < 120_000 or not content.lstrip().startswith(b"%PDF-"):
        raise ValueError(f"not a valid PDF or too small: {len(content)} bytes")
    if b"%%EOF" not in content[-65536:]:
        raise ValueError("PDF EOF marker missing")
    reader = PdfReader(BytesIO(content), strict=False)
    pages = len(reader.pages)
    if pages < 6:
        raise ValueError(f"too few pages: {pages}")
    text = extract_text(reader)
    compact = normalize(text)
    if not any(token in compact for token in ("三维化学", "三维工程", "三维石化", STOCK_CODE)):
        raise ValueError("issuer name/ticker not found in extracted PDF text")
    title = candidate["title"]
    key_chunks = [x for x in re.split(r"[，,:：；;———\-]+", title) if len(normalize(x)) >= 4]
    if key_chunks and not any(normalize(k) in compact for k in key_chunks[:4]):
        # Some older PDFs have sparse text extraction; require the ticker/company check and warn only.
        print(f"WARNING title fragments not extracted: {title}")
    return pages, text[:800]


def download_candidate(candidate: dict[str, Any], sequence: int) -> dict[str, Any]:
    errors: list[str] = []
    for url in pdf_urls(candidate["info_code"]):
        print(f"TRY {candidate['broker']} | {candidate['title']} | {url}")
        try:
            response = request(url)
            content = response.content
            pages, _ = validate_pdf(content, candidate)
            safe_title = re.sub(r"[\\/:*?\"<>|]", "_", candidate["title"])
            filename = (
                f"{sequence:02d}_{candidate['date']}_{candidate['broker']}_"
                f"三维化学_{safe_title}_{pages}页.pdf"
            )
            path = OUT / filename
            path.write_bytes(content)
            sha = hashlib.sha256(content).hexdigest()
            print(f"OK {filename} | {len(content)} bytes | sha256={sha}")
            return {
                "sequence": sequence,
                "broker": candidate["broker"],
                "report_date": candidate["date"],
                "title": candidate["title"],
                "filename": filename,
                "pages": pages,
                "bytes": len(content),
                "sha256": sha,
                "source_url": response.url,
                "info_code": candidate["info_code"],
            }
        except Exception as exc:
            msg = f"{url} :: {type(exc).__name__}: {exc}"
            print("FAIL", msg)
            errors.append(msg)
    raise RuntimeError("all PDF sources failed\n" + "\n".join(errors))


def main() -> None:
    rows = query_reports()
    candidates = ranked_candidates(rows)
    if not candidates:
        raise SystemExit("No usable broker-report candidates found")

    completed: list[dict[str, Any]] = []
    failures: list[str] = []
    used_brokers: set[str] = set()
    used_codes: set[str] = set()

    # First pass: favor reports of at least 10 pages and different brokers.
    passes = [
        [x for x in candidates if x["reported_pages"] >= 10],
        [x for x in candidates if x["reported_pages"] >= 8],
        candidates,
    ]
    for pool in passes:
        for candidate in pool:
            if len(completed) >= 3:
                break
            if candidate["info_code"] in used_codes:
                continue
            if len(completed) < 2 and candidate["broker"] in used_brokers:
                continue
            try:
                item = download_candidate(candidate, len(completed) + 1)
                completed.append(item)
                used_codes.add(candidate["info_code"])
                used_brokers.add(candidate["broker"])
            except Exception as exc:
                failures.append(
                    f"{candidate['broker']} | {candidate['title']} | {candidate['info_code']}\n{exc}"
                )
                print(failures[-1], file=sys.stderr)
        if len(completed) >= 2:
            break

    if len(completed) < 2:
        raise SystemExit(f"Incomplete package: only {len(completed)} valid reports")

    total_pages = sum(x["pages"] for x in completed)
    manifest = {
        "company": COMPANY,
        "stock_code": STOCK_CODE,
        "short_name": SHORT_NAME,
        "selection_rule": "公开可下载的原始券商PDF；优先公司深度/首次覆盖/专题报告；排除短篇财报点评；至少2份",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_pdf_files": len(completed),
        "total_pages": total_pages,
        "files": completed,
        "attempt_failures": failures,
    }
    (OUT / "报告清单与来源.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(f"{x['sha256']}  {x['filename']}\n" for x in completed), encoding="utf-8"
    )
    readme = [
        "三维化学（002469）券商研究报告资料包",
        "==================================",
        "",
        f"本资料包收录 {len(completed)} 份公开可下载的原始券商PDF，共 {total_pages} 页。",
        "筛选时优先保留公司深度、首次覆盖或专题研究，排除只有数页的财报/事件点评。",
        "",
        "文件清单：",
    ]
    for x in completed:
        readme.append(
            f"- {x['report_date']}｜{x['broker']}｜{x['title']}｜{x['pages']}页"
        )
    readme.extend(
        [
            "",
            "每份PDF均执行文件签名、EOF、页数、公司名称/股票代码和可解析性校验。",
            "来源URL、文件大小及SHA-256详见《报告清单与来源.json》和SHA256SUMS.txt。",
        ]
    )
    (OUT / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")

    temp_zip = Path("sanwei_chemical_broker_reports_crc_test.zip")
    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(OUT.iterdir()):
            if p.is_file():
                zf.write(p, p.name)
    with zipfile.ZipFile(temp_zip) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC check failed: {bad}")
    zip_sha = hashlib.sha256(temp_zip.read_bytes()).hexdigest()
    print(f"PACKAGE COMPLETE: {len(completed)} PDFs, {total_pages} pages")
    print(f"CRC TEST ZIP SHA256: {zip_sha}")
    temp_zip.unlink()


if __name__ == "__main__":
    main()
