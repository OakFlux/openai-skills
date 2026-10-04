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
REPORTS.mkdir(parents=True, exist_ok=True)
RENDERS.mkdir(parents=True, exist_ok=True)

AS_OF_DATE = "2026-10-04"
COMPANY_FULL = "江西宁新新材料股份有限公司"
COMPANY_SHORT = "宁新新材"
CURRENT_CODE = "920719"
FORMER_CODE = "839719"

DOCUMENTS = [
    {
        "filename": "01_宁新新材_2016年年度报告.pdf",
        "category": "2016年年度报告",
        "year": "2016",
        "doc_type": "年度报告",
        "art_code": "AN201703170415426307",
        "announcement_title": "宁新新材:2016年年度报告",
    },
    {
        "filename": "02_宁新新材_2017年年度报告_更正后.pdf",
        "category": "2017年年度报告（更正后）",
        "year": "2017",
        "doc_type": "年度报告",
        "art_code": "AN202007131391428212",
        "announcement_title": "宁新新材:2017年年度报告(更正后)",
    },
    {
        "filename": "03_宁新新材_2018年年度报告_更正后.pdf",
        "category": "2018年年度报告（更正后）",
        "year": "2018",
        "doc_type": "年度报告",
        "art_code": "AN202009221416153639",
        "announcement_title": "宁新新材:2018年年度报告(更正后)",
    },
    {
        "filename": "04_宁新新材_2019年年度报告_更正后.pdf",
        "category": "2019年年度报告（更正后）",
        "year": "2019",
        "doc_type": "年度报告",
        "art_code": "AN202209141578396035",
        "announcement_title": "宁新新材:2019年年度报告(更正后)",
    },
    {
        "filename": "05_宁新新材_2020年年度报告_更正后.pdf",
        "category": "2020年年度报告（更正后）",
        "year": "2020",
        "doc_type": "年度报告",
        "art_code": "AN202209141578396183",
        "announcement_title": "宁新新材:2020年年度报告(更正后)",
    },
    {
        "filename": "06_宁新新材_2021年年度报告_更正后.pdf",
        "category": "2021年年度报告（更正后）",
        "year": "2021",
        "doc_type": "年度报告",
        "art_code": "AN202209141578396300",
        "announcement_title": "宁新新材:2021年年度报告(更正后)",
    },
    {
        "filename": "07_宁新新材_2022年年度报告.pdf",
        "category": "2022年年度报告",
        "year": "2022",
        "doc_type": "年度报告",
        "art_code": "AN202304111585329147",
        "announcement_title": "宁新新材:2022年年度报告",
    },
    {
        "filename": "08_宁新新材_2023年年度报告_更正后.pdf",
        "category": "2023年年度报告（更正后）",
        "year": "2023",
        "doc_type": "年度报告",
        "art_code": "AN202407021637474494",
        "announcement_title": "宁新新材:2023年年度报告(更正后)",
    },
    {
        "filename": "09_宁新新材_2024年年度报告.pdf",
        "category": "2024年年度报告",
        "year": "2024",
        "doc_type": "年度报告",
        "art_code": "AN202504281664051236",
        "announcement_title": "宁新新材:2024年年度报告",
    },
    {
        "filename": "10_宁新新材_2025年年度报告.pdf",
        "category": "2025年年度报告",
        "year": "2025",
        "doc_type": "年度报告",
        "art_code": "AN202604291821765791",
        "announcement_title": "宁新新材:2025年年度报告",
    },
    {
        "filename": "11_宁新新材_向不特定合格投资者公开发行股票并在北京证券交易所上市招股说明书_最终版_2023-05-08.pdf",
        "category": "北交所公开发行上市招股说明书（最终发行版）",
        "year": "2023",
        "doc_type": "招股说明书",
        "art_code": "AN202305081586364294",
        "announcement_title": "宁新新材:向不特定合格投资者公开发行股票并在北京证券交易所上市招股说明书",
    },
    {
        "filename": "12_宁新新材_2026年第一季度报告_最新季报.pdf",
        "category": f"2026年第一季度报告（截至{AS_OF_DATE}最新正式季报）",
        "year": "2026",
        "doc_type": "第一季度报告",
        "art_code": "AN202604291821765793",
        "announcement_title": "宁新新材:2026年一季度报告",
    },
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Referer": "https://data.eastmoney.com/notices/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)


def fetch_pdf(art_code: str):
    candidates = [
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H2_{art_code}.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{art_code}_1.pdf",
        f"https://pdf.dfcfw.com/pdf/H3_{art_code}.pdf",
    ]
    errors = []
    for url in candidates:
        for attempt in range(4):
            try:
                response = SESSION.get(url, timeout=180, allow_redirects=True)
                print("FETCH", response.status_code, len(response.content), response.url, response.headers.get("content-type"))
                if response.status_code == 200 and response.content.startswith(b"%PDF-") and len(response.content) > 50_000:
                    return response, url
                errors.append({"url": url, "status": response.status_code, "head": repr(response.content[:32])})
                break
            except Exception as exc:
                errors.append({"url": url, "attempt": attempt + 1, "error": repr(exc)})
                time.sleep(min(8, 2**attempt))
    raise RuntimeError(f"All PDF URLs failed for {art_code}: {errors}")


def validate_identity(item: dict, path: Path, page_count: int):
    doc = pymupdf.open(str(path))
    sample_pages = min(10, doc.page_count)
    text = "".join(doc[i].get_text("text") for i in range(sample_pages))
    normalized = re.sub(r"\s+", "", text).upper()

    company_tokens = (
        COMPANY_FULL,
        COMPANY_SHORT,
        CURRENT_CODE,
        FORMER_CODE,
        "JIANGXININGXINNEWMATERIAL",
        "NINGXINNEWMATERIAL",
    )
    if not any(token.replace(" ", "").upper() in normalized for token in company_tokens):
        doc.close()
        raise RuntimeError(f"Company identity validation failed: {path.name}")
    if item["year"] not in normalized:
        doc.close()
        raise RuntimeError(f"Report year validation failed: {path.name}")

    if item["doc_type"] == "年度报告":
        type_ok = "年度报告" in normalized or "ANNUALREPORT" in normalized
        if page_count < 50:
            type_ok = False
    elif item["doc_type"] == "招股说明书":
        type_ok = "招股说明书" in normalized or "PROSPECTUS" in normalized
        if page_count < 200:
            type_ok = False
    else:
        type_ok = ("第一季度报告" in normalized or "一季度报告" in normalized or "FIRSTQUARTER" in normalized)
        if page_count < 5:
            type_ok = False
    if not type_ok:
        doc.close()
        raise RuntimeError(f"Document type validation failed: {path.name}")

    render_paths = []
    for label, index in (("first", 0), ("last", doc.page_count - 1)):
        pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        render_path = RENDERS / f"{path.stem}_{label}.png"
        pix.save(str(render_path))
        render_paths.append(render_path)
    doc.close()
    return render_paths


manifest = []
all_render_paths = []
for item in DOCUMENTS:
    response, source_url = fetch_pdf(item["art_code"])
    data = response.content
    path = REPORTS / item["filename"]
    path.write_bytes(data)

    reader = PdfReader(str(path))
    encrypted = reader.is_encrypted
    if encrypted and not reader.decrypt(""):
        raise RuntimeError(f"Password-protected PDF: {path.name}")
    pages = len(reader.pages)
    all_render_paths.extend(validate_identity(item, path, pages))

    record = {
        "filename": path.name,
        "category": item["category"],
        "announcement_title": item["announcement_title"],
        "art_code": item["art_code"],
        "announcement_page": f"https://data.eastmoney.com/notices/detail/{CURRENT_CODE}/{item['art_code']}.html",
        "source_url": source_url,
        "final_url": response.url,
        "pages": pages,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encrypted": encrypted,
    }
    manifest.append(record)
    print("VERIFIED", json.dumps(record, ensure_ascii=False))

# Visual contact sheet of every first and last page.
thumbs = []
for image_path in all_render_paths:
    image = Image.open(image_path).convert("RGB")
    image.thumbnail((420, 580))
    canvas = Image.new("RGB", (440, 630), "white")
    canvas.paste(image, ((440 - image.width) // 2, 8))
    ImageDraw.Draw(canvas).text((10, 605), image_path.stem[:58], fill="black")
    thumbs.append(canvas)
    image.close()
cols = 2
rows = (len(thumbs) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 440, rows * 630), "white")
for index, canvas in enumerate(thumbs):
    sheet.paste(canvas, ((index % cols) * 440, (index // cols) * 630))
    canvas.close()
contact_sheet = RENDERS / "contact_sheet.jpg"
sheet.save(contact_sheet, "JPEG", quality=90)
sheet.close()

readme = [
    f"{COMPANY_FULL}（证券简称：宁新新材，现证券代码：{CURRENT_CODE}，原证券代码：{FORMER_CODE}）披露文件资料包",
    "",
    "文件范围：",
    "1. 公司挂牌后公开披露的全部正式年度报告：2016年至2025年，共10份。",
    "2. 2023年5月8日北交所公开发行上市招股说明书最终发行版；未使用申报稿、上会稿或注册稿替代。",
    f"3. 2026年第一季度报告，为截至{AS_OF_DATE}最新一份标题明确为季度报告的正式定期报告。",
    "",
    "版本选择：",
    "- 2017、2018、2019、2020、2021及2023年年报采用后续披露的最新有效更正后全文。",
    "- 2016、2022、2024及2025年年报采用正式年度报告全文。",
    "- 公司已披露2026年半年度报告，但半年度报告不属于标题明确的季度报告；截至资料整理日，2026年第三季度报告尚未披露。",
    "",
    "核验：",
    "- 已检查PDF文件头、实际页数、加密状态、公司名称、报告年份及文件类型。",
    "- 已渲染并检查每份PDF的首页和末页。",
    "- ZIP已执行CRC完整性测试；包内附公告来源、文件大小及SHA-256校验值。",
    "- 文件仅供个人研究与学习使用，请遵守原始文件版权与免责声明。",
]
(REPORTS / "00_资料说明.txt").write_text("\n".join(readme) + "\n", encoding="utf-8")
(REPORTS / "00_文件清单及校验值.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
(REPORTS / "00_SHA256SUMS.txt").write_text(
    "\n".join(f"{record['sha256']}  {record['filename']}" for record in manifest) + "\n",
    encoding="utf-8",
)

zip_path = OUT / "ningxin_materials_all_annual_reports_prospectus_latest_quarter.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(REPORTS.iterdir(), key=lambda p: p.name):
        archive.write(path, arcname=path.name)
with zipfile.ZipFile(zip_path) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f"ZIP CRC validation failed: {bad}")

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
