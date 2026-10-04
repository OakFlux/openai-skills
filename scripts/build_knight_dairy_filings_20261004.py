#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import time
import zipfile
from pathlib import Path

import pymupdf
import requests
from PIL import Image, ImageDraw
from pypdf import PdfReader

OUT = Path("output")
REPORTS = OUT / "reports"
RENDERS = OUT / "renders"
OUT.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)
RENDERS.mkdir(parents=True, exist_ok=True)

COMPANY_FULL = "内蒙古骑士乳业集团股份有限公司"
COMPANY_SHORT = "骑士乳业"
OLD_CODE = "832786"
CURRENT_CODE = "920786"
AS_OF_DATE = "2026-10-04"

# The package covers every full annual report published after the company joined NEEQ
# in 2015, the final offering prospectus, and the newest title-explicit quarterly report.
DOCUMENTS = [
    {
        "filename": "01_骑士乳业_2015年年度报告.pdf",
        "category": "2015年年度报告",
        "year": "2015",
        "kind": "annual",
        "art_code": "AN201604070014270522",
        "title": "骑士乳业:2015年年度报告",
        "min_pages": 80,
    },
    {
        "filename": "02_骑士乳业_2016年年度报告_更正后.pdf",
        "category": "2016年年度报告（更正后）",
        "year": "2016",
        "kind": "annual",
        "art_code": "AN201705190591802548",
        "title": "骑士乳业:2016年年度报告(更正后)",
        "min_pages": 80,
    },
    {
        "filename": "03_骑士乳业_2017年年度报告.pdf",
        "category": "2017年年度报告",
        "year": "2017",
        "kind": "annual",
        "art_code": "AN201804121122108838",
        "title": "骑士乳业:2017年度报告",
        "min_pages": 80,
    },
    {
        "filename": "04_骑士乳业_2018年年度报告.pdf",
        "category": "2018年年度报告",
        "year": "2018",
        "kind": "annual",
        "art_code": "AN201903201307688465",
        "title": "骑士乳业:2018年年度报告",
        "min_pages": 80,
    },
    {
        "filename": "05_骑士乳业_2019年年度报告_更正后.pdf",
        "category": "2019年年度报告（更正后）",
        "year": "2019",
        "kind": "annual",
        "art_code": "AN202206281575413282",
        "title": "骑士乳业:2019年年度报告(更正后)",
        "min_pages": 80,
    },
    {
        "filename": "06_骑士乳业_2020年年度报告_更正后.pdf",
        "category": "2020年年度报告（更正后）",
        "year": "2020",
        "kind": "annual",
        "art_code": "AN202206281575413284",
        "title": "骑士乳业:2020年年度报告(更正后)",
        "min_pages": 80,
    },
    {
        "filename": "07_骑士乳业_2021年年度报告_更正后.pdf",
        "category": "2021年年度报告（更正后）",
        "year": "2021",
        "kind": "annual",
        "art_code": "AN202206291575561551",
        "title": "骑士乳业:2021年年度报告(更正后)",
        "min_pages": 80,
    },
    {
        "filename": "08_骑士乳业_2022年年度报告.pdf",
        "category": "2022年年度报告",
        "year": "2022",
        "kind": "annual",
        "art_code": "AN202304261585898135",
        "title": "骑士乳业:2022年年度报告",
        "min_pages": 80,
    },
    {
        "filename": "09_骑士乳业_2023年年度报告.pdf",
        "category": "2023年年度报告",
        "year": "2023",
        "kind": "annual",
        "art_code": "AN202404031629695152",
        "title": "骑士乳业:2023年年度报告",
        "min_pages": 80,
    },
    {
        "filename": "10_骑士乳业_2024年年度报告.pdf",
        "category": "2024年年度报告",
        "year": "2024",
        "kind": "annual",
        "art_code": "AN202504251662369282",
        "title": "骑士乳业:2024年年度报告",
        "min_pages": 80,
    },
    {
        "filename": "11_骑士乳业_2025年年度报告.pdf",
        "category": "2025年年度报告",
        "year": "2025",
        "kind": "annual",
        "art_code": "AN202604201821349615",
        "title": "骑士乳业:2025年年度报告",
        "min_pages": 80,
    },
    {
        "filename": "12_骑士乳业_向不特定合格投资者公开发行股票并在北京证券交易所上市招股说明书_最终版_2023-09-22.pdf",
        "category": "北交所公开发行上市招股说明书（最终发行版）",
        "year": "2023",
        "kind": "prospectus",
        "art_code": "AN202309221599706987",
        "title": "骑士乳业:招股说明书",
        "min_pages": 300,
    },
    {
        "filename": "13_骑士乳业_2026年第一季度报告_最新季报.pdf",
        "category": "2026年第一季度报告（截至2026-10-04最新正式季报）",
        "year": "2026",
        "kind": "quarter",
        "art_code": "AN202604281821663637",
        "title": "骑士乳业:2026年一季度报告",
        "min_pages": 8,
    },
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://data.eastmoney.com/notices/",
    }
)


def candidate_urls(art_code: str):
    # Eastmoney has used both H2 and H3 prefixes for attachment mirrors.
    return [
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{art_code}.pdf",
    ]


def fetch_pdf(url: str):
    last = None
    for attempt in range(5):
        try:
            response = SESSION.get(url, timeout=240, allow_redirects=True)
            print("FETCH", response.status_code, len(response.content), response.url, response.headers.get("content-type"))
            if response.status_code == 200 and response.content.startswith(b"%PDF-"):
                return response
            last = RuntimeError(
                f"HTTP {response.status_code}; head={response.content[:48]!r}; type={response.headers.get('content-type')}"
            )
        except Exception as exc:
            last = exc
        time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def extract_first_pages_text(path: Path, limit: int = 10):
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    chunks = []
    for index in range(min(limit, len(reader.pages))):
        try:
            chunks.append(reader.pages[index].extract_text() or "")
        except Exception:
            pass
    text = " ".join(chunks)
    if len(text.strip()) < 100:
        doc = pymupdf.open(str(path))
        text = " ".join(doc[index].get_text("text") for index in range(min(limit, doc.page_count)))
        doc.close()
    return re.sub(r"\s+", "", text)


def validate_text(text: str, item: dict):
    upper = text.upper()
    company_ok = any(
        token.replace(" ", "").upper() in upper
        for token in (
            COMPANY_FULL,
            COMPANY_SHORT,
            OLD_CODE,
            CURRENT_CODE,
            "INNER MONGOLIA KNIGHT DAIRY",
        )
    )
    if not company_ok:
        raise RuntimeError(f"Company identity validation failed: {item['filename']}")
    if item["year"] not in text:
        raise RuntimeError(f"Report-year validation failed: {item['filename']}")
    if item["kind"] == "annual":
        type_ok = "年度报告" in text or "ANNUALREPORT" in upper
    elif item["kind"] == "prospectus":
        type_ok = "招股说明书" in text or "PROSPECTUS" in upper
    else:
        type_ok = (
            "第一季度报告" in text
            or "一季度报告" in text
            or "FIRSTQUARTERLYREPORT" in upper
        )
    if not type_ok:
        raise RuntimeError(f"Document-type validation failed: {item['filename']}")


manifest = []
render_paths = []
for item in DOCUMENTS:
    response = None
    source_url = None
    errors = []
    for url in candidate_urls(item["art_code"]):
        try:
            response = fetch_pdf(url)
            source_url = url
            break
        except Exception as exc:
            errors.append({"url": url, "error": repr(exc)})
            print("CANDIDATE_FAILED", json.dumps(errors[-1], ensure_ascii=False))
    if response is None:
        raise RuntimeError(
            f"All PDF candidates failed for {item['title']}: {json.dumps(errors, ensure_ascii=False)}"
        )

    data = response.content
    if len(data) < 50_000:
        raise RuntimeError(f"PDF too small: {item['filename']} ({len(data)} bytes)")
    path = REPORTS / item["filename"]
    path.write_bytes(data)

    reader = PdfReader(str(path))
    encrypted = reader.is_encrypted
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    page_count = len(reader.pages)
    if page_count < item["min_pages"]:
        raise RuntimeError(
            f"Unexpected page count for {path.name}: {page_count} < {item['min_pages']}"
        )

    text = extract_first_pages_text(path)
    validate_text(text, item)

    doc = pymupdf.open(str(path))
    for label, index in (("first", 0), ("last", doc.page_count - 1)):
        pixmap = doc[index].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        render_path = RENDERS / f"{path.stem}_{label}.png"
        pixmap.save(str(render_path))
        render_paths.append(render_path)
    doc.close()

    record = {
        "filename": path.name,
        "category": item["category"],
        "announcement_title": item["title"],
        "art_code": item["art_code"],
        "announcement_page": f"https://data.eastmoney.com/notices/detail/{CURRENT_CODE}/{item['art_code']}.html",
        "source_url": source_url,
        "final_url": response.url,
        "pages": page_count,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
    }
    manifest.append(record)
    print("VERIFIED", json.dumps(record, ensure_ascii=False))

# Make a contact sheet for visual verification of all first and last pages.
thumbs = []
for image_path in render_paths:
    image = Image.open(image_path).convert("RGB")
    image.thumbnail((390, 540))
    canvas = Image.new("RGB", (410, 590), "white")
    canvas.paste(image, ((410 - image.width) // 2, 8))
    ImageDraw.Draw(canvas).text((10, 565), image_path.stem[:56], fill="black")
    thumbs.append(canvas)
    image.close()
cols = 2
rows = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 410, rows * 590), "white")
for index, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((index % cols) * 410, (index // cols) * 590))
    canvas.close()
contact_sheet = RENDERS / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=90)
sheet.close()

readme = f"""内蒙古骑士乳业集团股份有限公司披露文件资料包

证券代码口径：
- 全国股转系统挂牌期间证券代码：{OLD_CODE}
- 北京证券交易所当前证券代码：{CURRENT_CODE}

文件范围：
1. 公司挂牌后发布的全部完整年度报告：2015年至2025年，共11份。
2. 对存在后续更正版本的年度报告，优先收录最新有效的“更正后”全文：2016年、2019年、2020年和2021年。
3. 2023年9月22日最终发行版《向不特定合格投资者公开发行股票并在北京证券交易所上市招股说明书》；未使用申报稿、上会稿或注册稿替代。
4. 2026年第一季度报告，为截至{AS_OF_DATE}最新一份标题明确为季度报告的正式定期报告。

口径说明：
- 公司已披露2026年半年度报告，但半年度报告不属于标题明确的季度报告，故本资料包将2026年第一季度报告作为“最新季报”。
- 2014年度财务数据曾用于挂牌申报材料，但公司于2015年挂牌后发布的首份独立年度报告为2015年年度报告，因此本包的“所有年报”范围为2015年至2025年。
- PDF取自东方财富公开公告附件服务器，公告编号和标题按公开披露记录核对。

完整性检查：
- 已逐份检查PDF文件头、实际页数、加密状态、公司名称、报告年份和文件类型。
- 已渲染并检查每份PDF的首页和末页。
- 压缩包附来源、文件大小和SHA-256校验值，并执行ZIP CRC完整性测试。
- 文件仅供个人研究与学习使用，请遵守原始文件版权与免责声明。
"""
(REPORTS / "00_资料说明.txt").write_text(readme, encoding="utf-8")
(REPORTS / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
(REPORTS / "00_SHA256SUMS.txt").write_text(
    "\n".join(f"{record['sha256']}  {record['filename']}" for record in manifest) + "\n",
    encoding="utf-8",
)

zip_path = OUT / "knight_dairy_all_annual_reports_prospectus_latest_quarter.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(REPORTS.iterdir(), key=lambda p: p.name):
        archive.write(path, arcname=path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad_file = archive.testzip()
    if bad_file:
        raise RuntimeError(f"ZIP CRC validation failed at {bad_file}")

summary = {
    "zip_filename": zip_path.name,
    "zip_bytes": zip_path.stat().st_size,
    "zip_sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    "report_count": len(manifest),
    "total_pages": sum(record["pages"] for record in manifest),
    "reports": manifest,
}
(OUT / "BUILD_SUMMARY.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    "FINAL_SUMMARY",
    json.dumps(
        {key: summary[key] for key in ("zip_filename", "zip_bytes", "zip_sha256", "report_count", "total_pages")},
        ensure_ascii=False,
    ),
)
