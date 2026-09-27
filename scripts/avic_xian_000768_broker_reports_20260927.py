from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader

CODE = "000768"
COMPANY = "中航西飞"
PACKAGE = "中航西飞_000768_券商深度报告_3份_公开完整PDF"
WORK = Path("avic_xian_work")
DOWNLOADS = WORK / "downloads"
FINAL = WORK / PACKAGE
ARTIFACT_OUT = Path("artifact_out")
for directory in (DOWNLOADS, FINAL, ARTIFACT_OUT):
    directory.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.trust_env = False
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Referer": "https://data.eastmoney.com/",
        "Accept": "application/json,text/javascript,application/pdf,*/*;q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", text).strip(" ._")[:100]


def get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(1, 5):
        try:
            response = session.get(url, params=params, timeout=(20, 120), allow_redirects=True)
            print("API", attempt, response.status_code, len(response.content), response.url, flush=True)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            print("API_ERR", attempt, repr(exc), flush=True)
            time.sleep(attempt * 2)
    raise RuntimeError(f"API request failed: {last!r}")


api = "https://reportapi.eastmoney.com/report/list"
all_rows: list[dict[str, Any]] = []
for page in range(1, 8):
    params = {
        "industryCode": "*",
        "pageSize": "100",
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": "2015-01-01",
        "endTime": "2026-09-27",
        "pageNo": str(page),
        "fields": "",
        "qType": "0",
        "orgCode": "",
        "code": CODE,
        "rcode": "",
        "p": str(page),
        "pageNum": str(page),
        "pageNumber": str(page),
    }
    payload = get_json(api, params)
    batch = payload.get("data") or []
    print("BATCH", page, len(batch), flush=True)
    if not batch:
        break
    all_rows.extend(batch)
    if len(batch) < 100:
        break

unique: dict[str, dict[str, Any]] = {}
for row in all_rows:
    blob = json.dumps(row, ensure_ascii=False)
    if CODE not in blob and COMPANY not in blob:
        continue
    key = str(row.get("infoCode") or row.get("id") or blob)
    unique[key] = row
metadata = list(unique.values())
metadata.sort(key=lambda item: str(item.get("publishDate") or ""), reverse=True)
(WORK / "all_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
print("METADATA_COUNT", len(metadata), flush=True)

if not metadata:
    raise RuntimeError("No broker-report metadata returned for 中航西飞 000768")

strong_terms = [
    "深度",
    "首次覆盖",
    "公司深度",
    "军贸",
    "大飞机",
    "运输机",
    "轰炸机",
    "军机",
    "民机",
    "军民机",
    "航空制造",
    "航空工业",
    "产业链",
    "龙头",
    "新空间",
    "成长",
    "红利",
]
shallow_terms = ["点评", "季报", "年报", "中报", "半年报", "快报", "简评", "更新报告", "业绩预告", "事件点评"]


def metadata_score(row: dict[str, Any]) -> tuple[int, int, str]:
    title = str(row.get("title") or "")
    lower = title.lower()
    score = sum(5 for term in strong_terms if term.lower() in lower)
    if "深度" in title or "首次覆盖" in title:
        score += 25
    score -= sum(7 for term in shallow_terms if term in title)
    year_text = str(row.get("publishDate") or "")[:4]
    year = int(year_text) if year_text.isdigit() else 0
    return score, year, str(row.get("publishDate") or "")


candidates = sorted(metadata, key=metadata_score, reverse=True)[:55]
seen_hashes: set[str] = set()
valid: list[dict[str, Any]] = []


def download_pdf(info_code: str, destination: Path) -> str:
    variants = [
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_01.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}_01.pdf",
    ]
    errors: list[str] = []
    for url in variants:
        try:
            response = session.get(url, timeout=(25, 180), allow_redirects=True)
            print("PDF", info_code, response.status_code, len(response.content), url, "FINAL", response.url, flush=True)
            if response.status_code == 200 and len(response.content) > 100_000 and response.content.startswith(b"%PDF-"):
                destination.write_bytes(response.content)
                return str(response.url)
        except Exception as exc:
            errors.append(repr(exc))
    raise RuntimeError("; ".join(errors) or "No valid PDF variant")


def sample_text(reader: PdfReader) -> str:
    count = len(reader.pages)
    indexes = list(range(min(16, count)))
    if count > 20:
        indexes.extend(range(max(0, count - 3), count))
    parts: list[str] = []
    for index in dict.fromkeys(indexes):
        try:
            parts.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    return "\n".join(parts)


for row in candidates:
    info_code = str(row.get("infoCode") or "")
    if not info_code:
        continue
    title = str(row.get("title") or "").strip() or info_code
    broker = str(row.get("orgSName") or row.get("orgName") or "未知券商").strip()
    publish_date = str(row.get("publishDate") or "")[:10]
    temporary = DOWNLOADS / f"{info_code}.pdf"
    try:
        source_url = download_pdf(info_code, temporary)
        reader = PdfReader(str(temporary), strict=False)
        pages = len(reader.pages)
        text = sample_text(reader)
        lower = text.lower()
        identity_ok = (
            COMPANY in text
            or CODE in text
            or "西安飞机国际航空制造" in text
            or "avic xi'an aircraft" in lower
            or "avic xian aircraft" in lower
        )
        research_ok = (
            any(marker in text for marker in ["证券研究报告", "公司研究", "深度研究", "首次覆盖", "军工行业研究"])
            or "equity research" in lower
            or "深度" in title
            or "首次覆盖" in title
        )
        if pages < 15 or not identity_ok or not research_ok:
            print("REJECT", info_code, title, pages, identity_ok, research_ok, flush=True)
            temporary.unlink(missing_ok=True)
            continue
        check = subprocess.run(["qpdf", "--check", str(temporary)], capture_output=True, text=True)
        if check.returncode not in (0, 3):
            raise RuntimeError("qpdf check failed: " + check.stderr[-1000:])
        data = temporary.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 in seen_hashes:
            temporary.unlink(missing_ok=True)
            continue
        seen_hashes.add(sha256)
        title_score = metadata_score(row)[0]
        page_score = 16 if pages >= 50 else 13 if pages >= 35 else 10 if pages >= 25 else 7 if pages >= 20 else 4
        year_text = publish_date[:4]
        recency_score = max(0, int(year_text) - 2018) if year_text.isdigit() else 0
        score = title_score + page_score + recency_score
        record = {
            "info_code": info_code,
            "title": title,
            "broker": broker,
            "publish_date": publish_date,
            "pages": pages,
            "bytes": len(data),
            "sha256": sha256,
            "source_url": source_url,
            "score": score,
            "file": str(temporary),
        }
        valid.append(record)
        print("VALID", json.dumps({key: record[key] for key in ["title", "broker", "publish_date", "pages", "score"]}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print("PROBE_ERR", info_code, title, repr(exc), flush=True)
        temporary.unlink(missing_ok=True)

if len(valid) < 2:
    raise RuntimeError(f"Only {len(valid)} valid long-form reports found")

valid.sort(key=lambda item: (item["score"], item["pages"], item["publish_date"]), reverse=True)
selected: list[dict[str, Any]] = []
used_brokers: set[str] = set()
for record in valid:
    if record["broker"] in used_brokers:
        continue
    selected.append(record)
    used_brokers.add(record["broker"])
    if len(selected) == 3:
        break
if len(selected) < 3:
    for record in valid:
        if record not in selected:
            selected.append(record)
        if len(selected) == 3:
            break
selected = selected[:3]

final_records: list[dict[str, Any]] = []
for index, record in enumerate(selected, 1):
    filename = f"{index:02d}_{safe_name(record['publish_date'])}_{safe_name(record['broker'])}_{safe_name(record['title'])}.pdf"
    destination = FINAL / filename
    shutil.copy2(record["file"], destination)
    final_records.append(
        {
            "filename": filename,
            "broker": record["broker"],
            "title": record["title"],
            "publish_date": record["publish_date"],
            "pages": record["pages"],
            "bytes": record["bytes"],
            "sha256": record["sha256"],
            "source_url": record["source_url"],
            "info_code": record["info_code"],
        }
    )

readme = [
    "中航西飞（000768.SZ）券商深度报告合集",
    "整理日期：2026-09-27",
    "",
    "筛选标准：公开可直接下载的完整PDF；公司身份及券商研究属性可核验；优先长篇深度、首次覆盖或专题研究；尽量分散券商来源；剔除短篇业绩点评与季报点评。",
    "",
    "文件清单：",
]
for index, record in enumerate(final_records, 1):
    readme.extend(
        [
            f"{index}. {record['filename']}",
            f"   券商：{record['broker']}",
            f"   标题：{record['title']}",
            f"   日期：{record['publish_date']}；页数：{record['pages']}",
            f"   来源：{record['source_url']}",
            f"   SHA-256：{record['sha256']}",
            "",
        ]
    )
readme.append("完整性核验：已检查PDF文件头、公司身份、券商研究属性、实际页数、qpdf结构、重复文件及SHA-256；ZIP已通过CRC测试。")
(FINAL / "00_文件清单与范围说明.txt").write_text("\n".join(readme), encoding="utf-8")
(FINAL / "manifest.json").write_text(json.dumps(final_records, ensure_ascii=False, indent=2), encoding="utf-8")
(FINAL / "SHA256SUMS.txt").write_text(
    "".join(f"{record['sha256']}  {record['filename']}\n" for record in final_records),
    encoding="utf-8",
)

zip_path = ARTIFACT_OUT / f"{PACKAGE}.zip"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
    for path in sorted(FINAL.iterdir()):
        archive.write(path, arcname=f"{PACKAGE}/{path.name}")
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    pdf_names = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
    if bad:
        raise RuntimeError("ZIP CRC failure: " + bad)
    if len(pdf_names) != len(final_records):
        raise RuntimeError(f"ZIP PDF count mismatch: {len(pdf_names)} vs {len(final_records)}")

zip_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
print("SELECTED_FINAL", json.dumps(final_records, ensure_ascii=False, indent=2), flush=True)
print("FINAL_ZIP", zip_path.name, zip_path.stat().st_size, "SHA256", zip_sha256, flush=True)
