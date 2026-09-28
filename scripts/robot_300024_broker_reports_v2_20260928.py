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
TODAY = "2026-09-28"
PACKAGE = "机器人_300024_券商研究报告_3份_含1份长篇深度"
WORK = Path("robot_300024_targeted")
DOWNLOADS = WORK / "downloads"
FINAL = WORK / PACKAGE
ART = Path("artifact_out")
for path in (DOWNLOADS, FINAL, ART):
    path.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.trust_env = False
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Referer": "https://data.eastmoney.com/",
        "Accept": "application/json,application/pdf,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def safe(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", text).strip(" ._")[:90]


params = {
    "industryCode": "*",
    "pageSize": "100",
    "industry": "*",
    "rating": "*",
    "ratingChange": "*",
    "beginTime": "2010-01-01",
    "endTime": TODAY,
    "pageNo": "1",
    "fields": "",
    "qType": "0",
    "orgCode": "",
    "code": CODE,
    "rcode": "",
    "p": "1",
    "pageNum": "1",
    "pageNumber": "1",
}
response = session.get(
    "https://reportapi.eastmoney.com/report/list", params=params, timeout=(20, 120)
)
print("API", response.status_code, len(response.content), response.url, flush=True)
response.raise_for_status()
rows = response.json().get("data") or []
metadata = {str(row.get("infoCode")): row for row in rows if row.get("infoCode")}
(WORK / "metadata.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
)

# Publicly downloadable company-specific reports. Priority favors the one genuine
# long-form report, then the most recent and most substantive company studies.
priority_codes = [
    "AP201707050693499011",  # 23 pages, long-form company report
    "AP202308291596600026",  # recent semiconductor/core-parts thematic update
    "AP201808241182423639",  # 7-page company study
    "AP201803301115262502",  # 7-page AI/new-market company study
    "AP201903181306727324",  # 6-page logistics/semiconductor company study
    "AP201903201307596222",  # 6-page logistics/semiconductor company study
]


def download_pdf(info_code: str, destination: Path) -> str:
    variants = [
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{info_code}_01.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{info_code}_1.pdf",
    ]
    last: Exception | None = None
    for url in variants:
        try:
            result = session.get(url, timeout=(25, 180), allow_redirects=True)
            print("PDF", info_code, result.status_code, len(result.content), url, flush=True)
            if (
                result.status_code == 200
                and len(result.content) > 100_000
                and result.content.startswith(b"%PDF-")
            ):
                destination.write_bytes(result.content)
                return str(result.url)
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError(f"download failed for {info_code}: {last!r}")


valid: list[dict] = []
seen_sha: set[str] = set()
for rank, info_code in enumerate(priority_codes):
    row = metadata.get(info_code)
    if not row:
        print("NO_METADATA", info_code, flush=True)
        continue
    title = str(row.get("title") or info_code).strip()
    broker = str(row.get("orgSName") or row.get("orgName") or "未知券商").strip()
    publish_date = str(row.get("publishDate") or "")[:10]
    target = DOWNLOADS / f"{info_code}.pdf"
    try:
        source_url = download_pdf(info_code, target)
        reader = PdfReader(str(target), strict=False)
        pages = len(reader.pages)
        if pages < 5:
            target.unlink(missing_ok=True)
            continue
        parts: list[str] = []
        for index in range(min(10, pages)):
            try:
                parts.append(reader.pages[index].extract_text() or "")
            except Exception:
                pass
        text = "\n".join(parts)
        low = text.lower()
        identity = any(
            token in low
            for token in [
                "300024",
                "沈阳新松机器人自动化股份有限公司".lower(),
                "新松机器人".lower(),
                "siasun",
            ]
        )
        if not identity:
            raise RuntimeError("company identity not verified")
        check = subprocess.run(
            ["qpdf", "--check", str(target)], capture_output=True, text=True
        )
        if check.returncode not in (0, 3):
            raise RuntimeError("qpdf check failed: " + check.stderr[-1000:])
        data = target.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 in seen_sha:
            target.unlink(missing_ok=True)
            continue
        seen_sha.add(sha256)
        valid.append(
            {
                "rank": rank,
                "info_code": info_code,
                "title": title,
                "broker": broker,
                "publish_date": publish_date,
                "pages": pages,
                "bytes": len(data),
                "sha256": sha256,
                "source_url": source_url,
                "file": str(target),
            }
        )
        print(
            "VALID",
            json.dumps(
                {
                    "title": title,
                    "broker": broker,
                    "publish_date": publish_date,
                    "pages": pages,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as exc:
        print("ERROR", info_code, repr(exc), flush=True)
        target.unlink(missing_ok=True)

if len(valid) < 3:
    raise RuntimeError(f"Only {len(valid)} usable reports found")

valid.sort(key=lambda item: item["rank"])
selected: list[dict] = []
used_brokers: set[str] = set()
for item in valid:
    if item["broker"] in used_brokers:
        continue
    selected.append(item)
    used_brokers.add(item["broker"])
    if len(selected) == 3:
        break
if len(selected) < 3:
    for item in valid:
        if item not in selected:
            selected.append(item)
        if len(selected) == 3:
            break
selected = selected[:3]

final_records: list[dict] = []
for index, item in enumerate(selected, 1):
    report_type = "长篇深度" if item["pages"] >= 15 else "公司专题研究"
    filename = (
        f'{index:02d}_{safe(item["publish_date"])}_{safe(item["broker"])}_'
        f'{safe(item["title"])}_{report_type}.pdf'
    )
    shutil.copy2(item["file"], FINAL / filename)
    final_records.append(
        {
            "filename": filename,
            "report_type": report_type,
            "broker": item["broker"],
            "title": item["title"],
            "publish_date": item["publish_date"],
            "pages": item["pages"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "source_url": item["source_url"],
            "info_code": item["info_code"],
        }
    )

readme = [
    "机器人（300024.SZ，沈阳新松机器人自动化股份有限公司）券商研究报告合集",
    "整理日期：2026-09-28",
    "",
    "说明：公开渠道仅检索到1份可直接下载、篇幅达到长篇标准的公司深度报告。为满足2-3份需求，本包另收录2份完整公司专题研究；未使用公司公告、网页摘要或付费预览凑数。",
    "",
    "文件清单：",
]
for index, record in enumerate(final_records, 1):
    readme += [
        f'{index}. {record["filename"]}',
        f'   类型：{record["report_type"]}',
        f'   券商：{record["broker"]}',
        f'   标题：{record["title"]}',
        f'   日期：{record["publish_date"]}；页数：{record["pages"]}',
        f'   来源：{record["source_url"]}',
        f'   SHA-256：{record["sha256"]}',
        "",
    ]
readme += [
    "完整性核验：已检查PDF文件头、公司身份、实际页数、qpdf结构、重复文件及SHA-256；ZIP已通过CRC测试。"
]
(FINAL / "00_文件清单与范围说明.txt").write_text(
    "\n".join(readme), encoding="utf-8"
)
(FINAL / "manifest.json").write_text(
    json.dumps(final_records, ensure_ascii=False, indent=2), encoding="utf-8"
)
(FINAL / "SHA256SUMS.txt").write_text(
    "".join(f'{record["sha256"]}  {record["filename"]}\n' for record in final_records),
    encoding="utf-8",
)

zip_path = ART / f"{PACKAGE}.zip"
with zipfile.ZipFile(
    zip_path,
    "w",
    compression=zipfile.ZIP_DEFLATED,
    compresslevel=6,
    allowZip64=True,
) as archive:
    for path in sorted(FINAL.iterdir()):
        archive.write(path, arcname=f"{PACKAGE}/{path.name}")
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    pdfs = [name for name in archive.namelist() if name.lower().endswith(".pdf")]
    if bad:
        raise RuntimeError("ZIP CRC failure: " + bad)
    if len(pdfs) != 3:
        raise RuntimeError(f"ZIP PDF count mismatch: {len(pdfs)}")
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
