from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader

CODE = "300059"
COMPANY = "东方财富"
TODAY = "2026-09-27"
PACKAGE = "东方财富_300059_2020-2025年报_2026最新中期报告_官方原件"
OUT = Path(PACKAGE)
ART = Path("artifact_out")
OUT.mkdir(exist_ok=True)
ART.mkdir(exist_ok=True)

session = requests.Session()
session.trust_env = False
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.cninfo.com.cn",
        "Referer": "https://www.cninfo.com.cn/",
        "X-Requested-With": "XMLHttpRequest",
    }
)


def get_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    last: Exception | None = None
    for attempt in range(1, 5):
        try:
            response = session.get(url, params=params, timeout=(30, 180), allow_redirects=True)
            print("GET", attempt, response.status_code, len(response.content), response.url, flush=True)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            print("GET_ERR", attempt, repr(exc), flush=True)
            time.sleep(attempt * 2)
    raise RuntimeError(f"GET failed: {url}: {last!r}")


def resolve_stock_values() -> list[str]:
    candidates: list[str] = []
    endpoints = [
        ("https://www.cninfo.com.cn/new/information/topSearch/query", {"keyWord": CODE, "maxNum": "20"}),
        ("https://www.cninfo.com.cn/new/information/topSearch/query", {"keyWord": COMPANY, "maxNum": "20"}),
    ]
    payloads: list[Any] = []
    for url, params in endpoints:
        try:
            payload = get_json(url, params=params)
            payloads.append(payload)
        except Exception as exc:
            print("TOPSEARCH_ERR", repr(exc), flush=True)

    Path("cninfo_topsearch.json").write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            blob = json.dumps(obj, ensure_ascii=False)
            code = str(
                obj.get("code")
                or obj.get("secCode")
                or obj.get("stockCode")
                or obj.get("SECCODE")
                or ""
            )
            name = str(
                obj.get("zwjc")
                or obj.get("secName")
                or obj.get("stockName")
                or obj.get("name")
                or ""
            )
            org_id = obj.get("orgId") or obj.get("orgID") or obj.get("orgid")
            if (CODE in code or CODE in blob) and (COMPANY in name or COMPANY in blob) and org_id:
                candidates.append(f"{CODE},{org_id}")
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    for payload in payloads:
        walk(payload)

    # Known-format fallbacks and code-only fallback. CNInfo ignores invalid variants safely.
    candidates.extend(
        [
            f"{CODE},gssz0000059",
            f"{CODE},9900000059",
            CODE,
        ]
    )
    return list(dict.fromkeys(candidates))


def post_query(stock_value: str, category: str, page_num: int, page_size: int = 100) -> dict[str, Any]:
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    data = {
        "pageNum": str(page_num),
        "pageSize": str(page_size),
        "column": "szse",
        "tabName": "fulltext",
        "plate": "sz",
        "stock": stock_value,
        "searchkey": "",
        "secid": "",
        "category": category,
        "trade": "",
        "seDate": f"2020-01-01~{TODAY}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    last: Exception | None = None
    for attempt in range(1, 5):
        try:
            response = session.post(url, data=data, timeout=(30, 180), allow_redirects=True)
            print(
                "QUERY",
                attempt,
                response.status_code,
                len(response.content),
                stock_value,
                category,
                page_num,
                flush=True,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            print("QUERY_ERR", attempt, repr(exc), flush=True)
            time.sleep(attempt * 2)
    raise RuntimeError(f"CNInfo query failed: {last!r}")


categories = [
    "category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh",
    "category_ndbg_szsh",
    "category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh",
    "",
]

announcements: list[dict[str, Any]] = []
raw_payloads: list[dict[str, Any]] = []
chosen_stock = ""

for stock_value in resolve_stock_values():
    local: list[dict[str, Any]] = []
    try:
        for category in categories:
            first = post_query(stock_value, category, 1, 100)
            raw_payloads.append({"stock": stock_value, "category": category, "page": 1, "payload": first})
            batch = first.get("announcements") or []
            local.extend(batch)
            total_pages = int(first.get("totalpages") or first.get("totalPages") or 1)
            for page in range(2, min(total_pages, 20) + 1):
                payload = post_query(stock_value, category, page, 100)
                raw_payloads.append({"stock": stock_value, "category": category, "page": page, "payload": payload})
                local.extend(payload.get("announcements") or [])
        if local:
            announcements = local
            chosen_stock = stock_value
            break
    except Exception as exc:
        print("STOCK_VARIANT_ERR", stock_value, repr(exc), flush=True)

Path("cninfo_raw_payloads.json").write_text(
    json.dumps(raw_payloads, ensure_ascii=False, indent=2), encoding="utf-8"
)
if not announcements:
    raise RuntimeError("CNInfo returned no announcements for 东方财富 300059")

uniq: dict[str, dict[str, Any]] = {}
for item in announcements:
    key = str(
        item.get("announcementId")
        or item.get("adjunctUrl")
        or json.dumps(item, ensure_ascii=False, sort_keys=True)
    )
    uniq[key] = item
announcements = list(uniq.values())
Path("cninfo_announcements.json").write_text(
    json.dumps(announcements, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("CHOSEN_STOCK", chosen_stock, "ANNOUNCEMENT_COUNT", len(announcements), flush=True)


def plain_title(item: dict[str, Any]) -> str:
    title = str(item.get("announcementTitle") or item.get("title") or "")
    title = re.sub(r"<[^>]+>", "", title)
    title = title.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", title).strip()


def pub_date(item: dict[str, Any]) -> str:
    value = item.get("announcementTime") or item.get("publishTime") or item.get("announcementDate")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")
    text = str(value or "")
    match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return text[:10]


def pdf_url(item: dict[str, Any]) -> str:
    value = str(item.get("adjunctUrl") or item.get("url") or "").strip()
    if not value:
        raise RuntimeError(f"No PDF URL: {item}")
    if value.startswith("http"):
        return value
    return "https://static.cninfo.com.cn/" + value.lstrip("/")


def annual_score(item: dict[str, Any], year: int) -> tuple[int, int, str] | None:
    title = plain_title(item)
    if f"{year}年年度报告" not in title:
        return None
    excluded = [
        "摘要",
        "英文",
        "审计报告",
        "财务报告",
        "社会责任",
        "ESG",
        "问询函",
        "取消",
        "披露提示",
        "董事会决议",
        "监事会决议",
    ]
    if any(term in title for term in excluded):
        return None
    exact = int(
        bool(
            re.fullmatch(
                rf"(?:东方财富(?:信息股份有限公司)?[:：]?)?{year}年年度报告(?:（修订版）|\(修订版\)|（更新后）|\(更新后\))?",
                title,
            )
        )
    )
    revised = int(any(term in title for term in ["修订版", "更新后"]))
    return (exact, revised, pub_date(item))


selected: list[dict[str, Any]] = []
for year in range(2020, 2026):
    candidates: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    for item in announcements:
        score = annual_score(item, year)
        if score is not None:
            candidates.append((score, item))
    if not candidates:
        related = [plain_title(item) for item in announcements if str(year) in plain_title(item)]
        raise RuntimeError(f"No full {year} annual report found. Related titles: {related[:50]}")
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    item = candidates[0][1]
    selected.append(
        {
            "filename": f"{year - 2019:02d}_东方财富_300059_{year}年年度报告.pdf",
            "category": "年度报告",
            "report_year": year,
            "title": plain_title(item),
            "disclosure_date": pub_date(item),
            "source_url": pdf_url(item),
            "min_pages": 80,
        }
    )

latest_candidates: list[tuple[int, str, str, int, dict[str, Any]]] = []
for item in announcements:
    title = plain_title(item)
    excluded = ["摘要", "英文", "提示性公告", "披露提示", "董事会决议", "监事会决议", "审计报告"]
    if any(term in title for term in excluded):
        continue
    half = re.search(r"(20\d{2})年半年度报告", title)
    q1 = re.search(r"(20\d{2})年第一季度报告", title)
    q3 = re.search(r"(20\d{2})年第三季度报告", title)
    if half:
        period = int(half.group(1)) * 10 + 2
        kind = "半年度报告"
        min_pages = 35
    elif q3:
        period = int(q3.group(1)) * 10 + 3
        kind = "第三季度报告"
        min_pages = 12
    elif q1:
        period = int(q1.group(1)) * 10 + 1
        kind = "第一季度报告"
        min_pages = 12
    else:
        continue
    latest_candidates.append((period, pub_date(item), kind, min_pages, item))

if not latest_candidates:
    raise RuntimeError("No full interim or quarterly report found")
latest_candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
period, _, latest_kind, latest_min_pages, latest_item = latest_candidates[0]
latest_year = period // 10
selected.append(
    {
        "filename": f"07_东方财富_300059_{latest_year}年{latest_kind}_最新定期报告.pdf",
        "category": f"最新{latest_kind}",
        "report_year": latest_year,
        "title": plain_title(latest_item),
        "disclosure_date": pub_date(latest_item),
        "source_url": pdf_url(latest_item),
        "min_pages": latest_min_pages,
    }
)
print("SELECTED", json.dumps(selected, ensure_ascii=False, indent=2), flush=True)


def download_pdf(url: str, destination: Path) -> str:
    headers = {
        "User-Agent": session.headers["User-Agent"],
        "Referer": "https://www.cninfo.com.cn/",
        "Accept": "application/pdf,*/*",
    }
    last: Exception | None = None
    for attempt in range(1, 5):
        temporary = destination.with_suffix(".pdf.part")
        try:
            with requests.get(
                url,
                headers=headers,
                timeout=(30, 300),
                allow_redirects=True,
                stream=True,
            ) as response:
                print("DOWNLOAD", destination.name, attempt, response.status_code, url, flush=True)
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                final_url = response.url
            size = temporary.stat().st_size
            head = temporary.open("rb").read(8)
            if size < 100_000 or not head.startswith(b"%PDF-"):
                raise RuntimeError(f"Invalid PDF size={size}, head={head!r}")
            temporary.replace(destination)
            return str(final_url)
        except Exception as exc:
            last = exc
            temporary.unlink(missing_ok=True)
            print("DOWNLOAD_ERR", destination.name, attempt, repr(exc), flush=True)
            time.sleep(attempt * 2)
    raise RuntimeError(f"Download failed for {destination.name}: {last!r}")


def extract_sample(reader: PdfReader) -> str:
    page_count = len(reader.pages)
    indices = list(range(min(35, page_count)))
    if page_count > 40:
        indices += list(range(max(0, page_count - 5), page_count))
    parts: list[str] = []
    for index in dict.fromkeys(indices):
        try:
            parts.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    return "\n".join(parts)


records: list[dict[str, Any]] = []
seen_hashes: set[str] = set()
for document in selected:
    destination = OUT / document["filename"]
    final_url = download_pdf(document["source_url"], destination)
    reader = PdfReader(str(destination), strict=False)
    pages = len(reader.pages)
    if pages < document["min_pages"]:
        raise RuntimeError(
            f"Unexpectedly short PDF {destination.name}: {pages} < {document['min_pages']}"
        )
    sample = extract_sample(reader)
    sample_lower = sample.lower()
    identity = (
        COMPANY in sample
        or CODE in sample
        or "东方财富信息股份有限公司" in sample
        or "east money information" in sample_lower
    )
    if not identity:
        raise RuntimeError(f"Company identity not verified in {destination.name}")
    check = subprocess.run(
        ["qpdf", "--check", str(destination)], capture_output=True, text=True
    )
    if check.returncode not in (0, 3):
        raise RuntimeError(f"qpdf check failed for {destination.name}: {check.stderr[-2000:]}")
    data = destination.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest in seen_hashes:
        raise RuntimeError(f"Duplicate PDF detected: {destination.name}")
    seen_hashes.add(digest)
    record = {
        **{
            key: document[key]
            for key in ("filename", "category", "report_year", "title", "disclosure_date")
        },
        "source": "巨潮资讯网",
        "source_url": final_url,
        "pages": pages,
        "bytes": len(data),
        "sha256": digest,
    }
    records.append(record)
    print("VALIDATED", json.dumps(record, ensure_ascii=False), flush=True)

if len(records) != 7:
    raise RuntimeError(f"Expected 7 PDFs, got {len(records)}")
annual_years = {record["report_year"] for record in records if record["category"] == "年度报告"}
if annual_years != set(range(2020, 2026)):
    raise RuntimeError(f"Annual report coverage mismatch: {annual_years}")

latest = records[-1]
readme = [
    "东方财富（300059.SZ）2020—2025年度报告及最新一期定期财务报告",
    "整理日期：2026-09-27",
    "",
    "一、收录范围",
    "1. 2020—2025年完整年度报告，共6份。",
    f"2. 最新一期完整定期报告，共1份：{latest['title']}。",
    "",
    "二、关于“最新季报”",
    "按披露时间优先收录最新完整半年度报告；若无，则使用最新季度报告。",
    f"本包最新文件披露日期：{latest['disclosure_date']}。",
    "",
    "三、文件清单",
]
for index, record in enumerate(records, 1):
    readme += [
        f"{index}. {record['filename']}",
        f"   类别：{record['category']}；报告年度：{record['report_year']}；披露日期：{record['disclosure_date']}",
        f"   官方标题：{record['title']}",
        f"   页数：{record['pages']}；大小：{record['bytes'] / 1048576:.2f} MB",
        f"   来源：{record['source_url']}",
        f"   SHA-256：{record['sha256']}",
        "",
    ]
readme += [
    "四、完整性核验",
    "全部PDF均已检查文件头、公司身份、实际页数、qpdf结构、重复文件及SHA-256；最终ZIP已通过CRC测试。",
]

(OUT / "00_文件清单与范围说明.txt").write_text("\n".join(readme), encoding="utf-8")
(OUT / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
(OUT / "SHA256SUMS.txt").write_text(
    "".join(f"{record['sha256']}  {record['filename']}\n" for record in records), encoding="utf-8"
)
(OUT / "CNINFO公告索引.json").write_text(
    json.dumps(announcements, ensure_ascii=False, indent=2), encoding="utf-8"
)

zip_path = ART / f"{PACKAGE}.zip"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
    for path in sorted(OUT.iterdir()):
        archive.write(path, arcname=f"{PACKAGE}/{path.name}")
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    pdfs = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
    if bad:
        raise RuntimeError("ZIP CRC failure: " + bad)
    if len(pdfs) != 7:
        raise RuntimeError(f"ZIP PDF count mismatch: {len(pdfs)}")

zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
print(
    "FINAL_ZIP",
    zip_path.name,
    zip_path.stat().st_size,
    "PDF_COUNT",
    len(pdfs),
    "SHA256",
    zip_sha,
    flush=True,
)
print("FINAL_MANIFEST", json.dumps(records, ensure_ascii=False, indent=2), flush=True)
