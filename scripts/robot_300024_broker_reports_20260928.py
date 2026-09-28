from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import requests
from pypdf import PdfReader

CODE = "300024"
COMPANY = "机器人"
TODAY = "2026-09-28"
WORK = Path("robot_300024_work")
DOWNLOADS = WORK / "downloads"
FINAL_ROOT = WORK / "final"
ART = Path("artifact_out")
for path in (DOWNLOADS, FINAL_ROOT, ART):
    path.mkdir(parents=True, exist_ok=True)

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


def safe(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", text).strip(" ._")[:90]


def get_json(url: str, params: dict) -> dict:
    last: Exception | None = None
    for attempt in range(1, 5):
        try:
            response = session.get(url, params=params, timeout=(20, 120))
            print("API", attempt, response.status_code, len(response.content), response.url, flush=True)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            print("API_ERR", attempt, repr(exc), flush=True)
            time.sleep(attempt * 2)
    raise RuntimeError(f"API failed: {last!r}")


api = "https://reportapi.eastmoney.com/report/list"
records: list[dict] = []
for page in range(1, 7):
    params = {
        "industryCode": "*",
        "pageSize": "100",
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": "2010-01-01",
        "endTime": TODAY,
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
    data = get_json(api, params)
    batch = data.get("data") or []
    print("BATCH", page, len(batch), flush=True)
    if not batch:
        break
    records.extend(batch)
    if len(batch) < 100:
        break

unique: dict[str, dict] = {}
for row in records:
    blob = json.dumps(row, ensure_ascii=False)
    if CODE not in blob and COMPANY not in blob:
        continue
    key = str(row.get("infoCode") or row.get("id") or blob)
    unique[key] = row
metadata = list(unique.values())
metadata.sort(key=lambda row: str(row.get("publishDate") or ""), reverse=True)
(WORK / "all_metadata.json").write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("METADATA_COUNT", len(metadata), flush=True)
if not metadata:
    raise RuntimeError("No broker-report metadata found for 300024")

deep_terms = [
    "深度",
    "首次覆盖",
    "公司深度",
    "新松",
    "工业机器人",
    "机器人龙头",
    "智能制造",
    "核心零部件",
    "国产替代",
    "半导体装备",
    "特种机器人",
    "移动机器人",
    "洁净机器人",
    "具身智能",
    "成长",
    "龙头",
    "平台型",
]
shallow_terms = [
    "点评",
    "季报",
    "年报点评",
    "中报点评",
    "业绩点评",
    "快报",
    "简评",
    "事件点评",
    "跟踪",
    "月报",
    "纪要",
    "交流纪要",
]


def metadata_score(row: dict) -> tuple[int, int, str]:
    title = str(row.get("title") or "")
    low = title.lower()
    score = sum(4 for term in deep_terms if term.lower() in low)
    if "深度" in title or "首次覆盖" in title:
        score += 18
    score -= sum(6 for term in shallow_terms if term in title)
    year = int(str(row.get("publishDate") or "0")[:4] or 0)
    return score, year, str(row.get("publishDate") or "")


candidates = sorted(metadata, key=metadata_score, reverse=True)[:70]
valid: list[dict] = []
seen_sha: set[str] = set()


def download_report(info_code: str, target: Path) -> str:
    urls = [
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_01.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}_01.pdf",
    ]
    errors: list[str] = []
    for url in urls:
        try:
            response = session.get(url, timeout=(25, 180), allow_redirects=True)
            print(
                "PDF",
                info_code,
                response.status_code,
                len(response.content),
                url,
                "FINAL",
                response.url,
                flush=True,
            )
            if (
                response.status_code == 200
                and len(response.content) > 100_000
                and response.content.startswith(b"%PDF-")
            ):
                target.write_bytes(response.content)
                return str(response.url)
        except Exception as exc:
            errors.append(repr(exc))
    raise RuntimeError("; ".join(errors) or "no valid PDF variant")


strong_aliases = [
    "300024",
    "沈阳新松机器人自动化股份有限公司",
    "沈阳新松机器人",
    "新松机器人",
    "siasun",
]

for row in candidates:
    info_code = str(row.get("infoCode") or "")
    if not info_code:
        continue
    title = str(row.get("title") or "").strip() or info_code
    broker = str(row.get("orgSName") or row.get("orgName") or "未知券商").strip()
    publish_date = str(row.get("publishDate") or "")[:10]
    temporary = DOWNLOADS / f"{info_code}.pdf"
    try:
        source_url = download_report(info_code, temporary)
        reader = PdfReader(str(temporary), strict=False)
        pages = len(reader.pages)
        sample_indices = list(range(min(14, pages)))
        if pages > 18:
            sample_indices.extend(range(max(0, pages - 3), pages))
        text_parts: list[str] = []
        for index in dict.fromkeys(sample_indices):
            try:
                text_parts.append(reader.pages[index].extract_text() or "")
            except Exception:
                pass
        sample = "\n".join(text_parts)
        low = sample.lower()
        identity = any(alias.lower() in low for alias in strong_aliases)
        research = (
            any(
                marker in sample
                for marker in ["证券研究报告", "公司研究", "深度研究", "首次覆盖", "公司深度"]
            )
            or "equity research" in low
            or "深度" in title
            or "首次覆盖" in title
        )
        if not identity or not research or pages < 12:
            print("REJECT", info_code, title, pages, identity, research, flush=True)
            temporary.unlink(missing_ok=True)
            continue
        check = subprocess.run(
            ["qpdf", "--check", str(temporary)], capture_output=True, text=True
        )
        if check.returncode not in (0, 3):
            raise RuntimeError("qpdf check failed: " + check.stderr[-1000:])
        data = temporary.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 in seen_sha:
            temporary.unlink(missing_ok=True)
            continue
        seen_sha.add(sha256)
        title_low = title.lower()
        keyword_score = sum(5 for term in deep_terms if term.lower() in title_low)
        if "深度" in title or "首次覆盖" in title:
            keyword_score += 18
        keyword_score -= sum(7 for term in shallow_terms if term in title)
        page_score = (
            14
            if pages >= 55
            else 12
            if pages >= 40
            else 10
            if pages >= 30
            else 8
            if pages >= 22
            else 6
            if pages >= 16
            else 4
        )
        recent_score = (
            max(0, int(publish_date[:4] or 0) - 2018)
            if re.match(r"20\d{2}", publish_date)
            else 0
        )
        score = keyword_score + page_score + recent_score
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
        print(
            "VALID",
            json.dumps(
                {
                    key: record[key]
                    for key in ("title", "broker", "publish_date", "pages", "score")
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as exc:
        print("PROBE_ERR", info_code, title, repr(exc), flush=True)
        temporary.unlink(missing_ok=True)

if len(valid) < 2:
    raise RuntimeError(f"Only {len(valid)} valid long-form reports found")

valid.sort(
    key=lambda record: (record["score"], record["pages"], record["publish_date"]),
    reverse=True,
)
selected: list[dict] = []
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

package = f"机器人_300024_券商深度报告_{len(selected)}份_公开完整PDF"
final_dir = FINAL_ROOT / package
final_dir.mkdir(parents=True, exist_ok=True)
final_records: list[dict] = []
for index, record in enumerate(selected, 1):
    filename = (
        f'{index:02d}_{safe(record["publish_date"])}_{safe(record["broker"])}_'
        f'{safe(record["title"])}.pdf'
    )
    destination = final_dir / filename
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
    "机器人（300024.SZ，沈阳新松机器人自动化股份有限公司）券商深度报告合集",
    "整理日期：2026-09-28",
    "",
    "筛选标准：公开可直接下载的完整PDF；公司身份及券商研究属性可核验；优先长篇深度、首次覆盖或专题研究；尽量分散券商来源。",
    "",
    "文件清单：",
]
for index, record in enumerate(final_records, 1):
    readme += [
        f'{index}. {record["filename"]}',
        f'   券商：{record["broker"]}',
        f'   标题：{record["title"]}',
        f'   日期：{record["publish_date"]}；页数：{record["pages"]}',
        f'   来源：{record["source_url"]}',
        f'   SHA-256：{record["sha256"]}',
        "",
    ]
readme += [
    "完整性核验：已检查PDF文件头、公司身份、券商研究标识、实际页数、qpdf结构、重复文件及SHA-256；ZIP已通过CRC测试。"
]
(final_dir / "00_文件清单与范围说明.txt").write_text(
    "\n".join(readme), encoding="utf-8"
)
(final_dir / "manifest.json").write_text(
    json.dumps(final_records, ensure_ascii=False, indent=2), encoding="utf-8"
)
(final_dir / "SHA256SUMS.txt").write_text(
    "".join(f'{record["sha256"]}  {record["filename"]}\n' for record in final_records),
    encoding="utf-8",
)

zip_path = ART / f"{package}.zip"
with zipfile.ZipFile(
    zip_path,
    "w",
    compression=zipfile.ZIP_DEFLATED,
    compresslevel=6,
    allowZip64=True,
) as archive:
    for path in sorted(final_dir.iterdir()):
        archive.write(path, arcname=f"{package}/{path.name}")
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    pdfs = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
    if bad:
        raise RuntimeError("ZIP CRC failure: " + bad)
    if len(pdfs) != len(final_records):
        raise RuntimeError(f"ZIP PDF count mismatch: {len(pdfs)} vs {len(final_records)}")
zip_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
print("SELECTED_FINAL", json.dumps(final_records, ensure_ascii=False, indent=2), flush=True)
print(
    "FINAL_ZIP",
    zip_path.name,
    zip_path.stat().st_size,
    "SHA256",
    zip_sha256,
    flush=True,
)
