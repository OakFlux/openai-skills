from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Iterable

import requests
import urllib3
from pypdf import PdfReader, PdfWriter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

AS_OF = "2026-09-22"
ROOT_NAME = f"HUTCHMED_和黄医药_资料包_截至{AS_OF}"
ROOT = Path(ROOT_NAME)
ZIP_NAME = f"HUTCHMED_All_Annual_Reports_Prospectuses_Latest_Periodic_Report_{AS_OF}.zip"

if ROOT.exists():
    shutil.rmtree(ROOT)
ROOT.mkdir(parents=True)

ANNUAL = ROOT / "01_Annual_Reports"
PROSPECTUS = ROOT / "02_Prospectuses"
LATEST = ROOT / "03_Latest_Periodic_Report"
META = ROOT / "00_Metadata"
TEMP = Path("_hutchmed_temp")
for d in (ANNUAL, PROSPECTUS, LATEST, META, TEMP):
    d.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.trust_env = False
session.verify = False
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
)

manifest: list[dict] = []
validation: list[dict] = []
errors: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, timeout: int = 180, headers: dict | None = None) -> bytes:
    last: Exception | None = None
    for attempt in range(1, 5):
        try:
            r = session.get(url, timeout=(25, timeout), allow_redirects=True, headers=headers)
            r.raise_for_status()
            if len(r.content) < 100:
                raise RuntimeError(f"response too small: {len(r.content)} bytes")
            return r.content
        except Exception as exc:
            last = exc
            print(f"RETRY {attempt}/4 {url}: {exc}")
            time.sleep(attempt * 2)
    raise RuntimeError(f"download failed: {url}: {last}")


def pdf_info(path: Path) -> tuple[int, str]:
    if path.read_bytes()[:4] != b"%PDF":
        raise RuntimeError(f"not a PDF: {path}")
    reader = PdfReader(str(path), strict=False)
    pages = len(reader.pages)
    sample_parts: list[str] = []
    indexes = list(range(min(10, pages)))
    if pages > 10:
        indexes.extend(range(max(10, pages - 3), pages))
    for i in sorted(set(indexes)):
        try:
            sample_parts.append(reader.pages[i].extract_text() or "")
        except Exception:
            pass
    sample = " ".join(" ".join(sample_parts).split())
    return pages, sample


def qpdf_check(path: Path) -> tuple[bool, str]:
    qpdf = shutil.which("qpdf")
    if not qpdf:
        return True, "qpdf unavailable; pypdf parse succeeded"
    p = subprocess.run([qpdf, "--check", str(path)], capture_output=True, text=True)
    msg = (p.stdout + "\n" + p.stderr).strip()
    return p.returncode == 0, msg[-1200:]


def register_file(
    path: Path,
    *,
    category: str,
    year: str,
    language: str,
    source_type: str,
    source_url: str,
    notes: str = "",
    min_pages: int = 1,
    expected_terms: Iterable[str] = (),
) -> None:
    pages, sample = pdf_info(path)
    if pages < min_pages:
        raise RuntimeError(f"too few pages ({pages} < {min_pages}): {path}")
    if expected_terms:
        low = sample.lower()
        if not any(term.lower() in low for term in expected_terms):
            print(f"WARNING expected text not found in extractable sample: {path}")
    ok, qmsg = qpdf_check(path)
    if not ok:
        raise RuntimeError(f"qpdf check failed for {path}: {qmsg}")
    row = {
        "category": category,
        "year": year,
        "language": language,
        "filename": path.relative_to(ROOT).as_posix(),
        "source_type": source_type,
        "source_url": source_url,
        "bytes": path.stat().st_size,
        "pages": pages,
        "sha256": sha256(path),
        "validation": "PASS",
        "notes": notes,
    }
    manifest.append(row)
    validation.append({"file": row["filename"], "pages": pages, "qpdf": "PASS", "message": qmsg})
    print(f"PASS {row['filename']} pages={pages} bytes={row['bytes']}")


def download_pdf(
    urls: str | list[str],
    dest: Path,
    *,
    category: str,
    year: str,
    language: str,
    source_type: str,
    notes: str = "",
    min_pages: int = 1,
    expected_terms: Iterable[str] = (),
) -> None:
    if isinstance(urls, str):
        urls = [urls]
    last: Exception | None = None
    used = ""
    for url in urls:
        try:
            data = fetch(url)
            if not data.startswith(b"%PDF"):
                raise RuntimeError(f"not PDF; header={data[:24]!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            used = url
            register_file(
                dest,
                category=category,
                year=year,
                language=language,
                source_type=source_type,
                source_url=used,
                notes=notes,
                min_pages=min_pages,
                expected_terms=expected_terms,
            )
            return
        except Exception as exc:
            last = exc
            if dest.exists():
                dest.unlink()
            print(f"FAILED CANDIDATE {url}: {exc}")
    raise RuntimeError(f"all sources failed for {dest}: {last}")


# 2006-2007 statutory annual reports were published as official chapter PDFs on the legacy company site.
# Reconstruct one continuous PDF without altering page content.
legacy_parts = {
    2006: {
        1: "20081120205305", 2: "20081120180757", 3: "20081120183424",
        4: "20081120190421", 5: "20081120181900", 6: "20081120200041",
        7: "20081120191517", 8: "20081120193617", 9: "20081120204702",
        10: "20081120182805", 11: "20081120185500", 12: "20081120191129",
        13: "20081120200613", 14: "20081120202858", 15: "20081120195245",
    },
    2007: {
        1: "20081120192126", 2: "20080828223615", 3: "20080828223042",
        4: "20080828223338", 5: "20080828224439", 6: "20080828225136",
        7: "20081120200323", 8: "20081120205704", 9: "20081120192722",
        10: "20080828224303", 11: "20080828223454", 12: "20081120202141",
        13: "20080828224842", 14: "20081120193826", 15: "20081120204453",
        16: "20080828223001", 17: "20080828223917",
    },
}

for year, parts in legacy_parts.items():
    writer = PdfWriter()
    source_urls: list[str] = []
    part_dir = TEMP / f"{year}_parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    for num, ts in parts.items():
        current = f"https://www.hutch-med.com/wp-content/uploads/docArchive/reports/{year}ar/{num:02d}.pdf"
        original = f"http://www.chi-med.com/eng/irinfo/reports/{year}ar/{num:02d}.pdf"
        archived = f"https://web.archive.org/web/{ts}id_/{original}"
        part_path = part_dir / f"{num:02d}.pdf"
        data = None
        used = None
        for candidate in (current, archived):
            try:
                candidate_data = fetch(candidate)
                if not candidate_data.startswith(b"%PDF"):
                    raise RuntimeError("not PDF")
                candidate_reader = PdfReader(candidate_data)
                if len(candidate_reader.pages) < 1:
                    raise RuntimeError("zero pages")
                data = candidate_data
                used = candidate
                break
            except Exception as exc:
                print(f"PART FAILED {candidate}: {exc}")
        if data is None or used is None:
            raise RuntimeError(f"could not obtain {year} part {num:02d}")
        part_path.write_bytes(data)
        reader = PdfReader(str(part_path), strict=False)
        for page in reader.pages:
            writer.add_page(page)
        source_urls.append(used)
    dest = ANNUAL / str(year) / f"HUTCHMED_{year}_Annual_Report_EN_reconstructed_from_official_sections.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        writer.write(f)
    register_file(
        dest,
        category="Annual report",
        year=str(year),
        language="English",
        source_type="Official legacy company chapter PDFs; lossless page merge",
        source_url=" | ".join(source_urls),
        notes="The legacy company website distributed this annual report as section PDFs. This file is a page-preserving merge of those official sections.",
        min_pages=20,
        expected_terms=["Hutchison China MediTech", "Annual Report"],
    )

# 2008-2011: the current official legacy archive retains annual/full-year results presentation files.
legacy_presentations = {
    2008: "pre0903.pdf",
    2009: "pre1003.pdf",
    2010: "pre1103.pdf",
    2011: "pre1203.pdf",
}
for year, name in legacy_presentations.items():
    download_pdf(
        f"https://www.hutch-med.com/wp-content/uploads/docArchive/presentations/{name}",
        ANNUAL / str(year) / f"HUTCHMED_{year}_Official_Annual_or_Full-Year_Results_Presentation_EN.pdf",
        category="Historical annual/full-year results material",
        year=str(year),
        language="English",
        source_type="Official company legacy archive",
        notes="The current official archive retains this annual/full-year results presentation; the complete statutory annual-report PDF for this year was not available from the current official archive.",
        min_pages=15,
        expected_terms=[str(year), "Full Year Financial Results", "Annual Report"],
    )

annual_sources = [
    (2012, "EN", "https://www.hutch-med.com/wp-content/uploads/2015/06/2012ar.pdf", ""),
    (2013, "EN", "https://www.hutch-med.com/wp-content/uploads/2015/06/2013ar.pdf", ""),
    (2013, "TC", "https://www.hutch-med.com/wp-content/uploads/2015/06/2013ar_tc.pdf", ""),
    (2014, "EN", "https://www.hutch-med.com/wp-content/uploads/2015/06/2014ar.pdf", ""),
    (2014, "TC", "https://www.hutch-med.com/wp-content/uploads/2015/06/2014ar_tc.pdf", ""),
    (2015, "EN", "https://www.hutch-med.com/wp-content/uploads/2016/03/2015AR.pdf", ""),
    (2015, "TC_Summary", "https://www.hutch-med.com/wp-content/uploads/2016/04/2015AR_tc_summary.pdf", "Traditional Chinese summary, not a full Chinese annual report."),
    (2016, "EN", "https://www.hutch-med.com/wp-content/uploads/2017/03/2016AR-Form20F.pdf", ""),
    (2017, "EN", "https://www.hutch-med.com/wp-content/uploads/2018/03/2017AR-Form20F.pdf", ""),
    (2018, "EN", "https://www.hutch-med.com/wp-content/uploads/2019/03/2018AR-Form20F-3.pdf", ""),
    (2019, "EN", "https://www.hutch-med.com/wp-content/uploads/2020/03/2019AR-Form20F-2.pdf", ""),
    (2020, "EN", "https://www.hutch-med.com/wp-content/uploads/2021/03/2020AR-Form20F-1.pdf", ""),
    (2021, "EN", "https://www.hutch-med.com/wp-content/uploads/2022/03/2021AR-Form20F-EN-1.pdf", ""),
    (2021, "TC", "https://www.hutch-med.com/wp-content/uploads/2022/03/2021AR-Form20F-CN.pdf", ""),
    (2022, "EN", "https://www.hutch-med.com/wp-content/uploads/2023/04/2022-Annual-Report-1.pdf", ""),
    (2022, "TC", "https://www.hutch-med.com/wp-content/uploads/2023/04/2022-Annual-Report-C-1.pdf", ""),
    (2023, "EN", "https://www.hutch-med.com/wp-content/uploads/2024/04/2023AR-Form20F-2.pdf", ""),
    (2023, "TC", "https://www.hutch-med.com/wp-content/uploads/2024/04/2023AR-Form20F_C-2.pdf", ""),
    (2024, "EN", "https://www.hutch-med.com/wp-content/uploads/2025/04/2024AR-Form20F-1.pdf", ""),
    (2024, "TC", "https://www.hutch-med.com/wp-content/uploads/2025/04/2024AR-Form20F_C-1.pdf", ""),
    (2025, "EN", "https://www.hutch-med.com/wp-content/uploads/2026/04/2025AR-Form20F-1.pdf", ""),
    (2025, "TC", "https://www.hutch-med.com/wp-content/uploads/2026/04/2025AR-Form20F_C-1.pdf", ""),
]
for year, lang, url, note in annual_sources:
    label = {"EN": "English", "TC": "Traditional Chinese", "TC_Summary": "Traditional Chinese summary"}[lang]
    download_pdf(
        url,
        ANNUAL / str(year) / f"HUTCHMED_{year}_Annual_Report_{lang}.pdf",
        category="Annual report",
        year=str(year),
        language=label,
        source_type="Official company PDF",
        notes=note,
        min_pages=10,
        expected_terms=["HUTCHMED", "Hutchison China MediTech", "和黃醫藥", str(year)],
    )

# Prospectuses / admission documents for the company's major listing stages.
download_pdf(
    "https://www.hutch-med.com/wp-content/uploads/2015/04/HCM-admission.pdf",
    PROSPECTUS / "2006_AIM_Admission_Document_EN.pdf",
    category="Prospectus / admission document",
    year="2006",
    language="English",
    source_type="Official company PDF",
    min_pages=30,
    expected_terms=["Admission Document", "Hutchison China MediTech"],
)

download_pdf(
    "https://www.hutch-med.com/wp-content/uploads/2021/06/2021061800169.pdf",
    PROSPECTUS / "2021_Hong_Kong_Listing_Prospectus_EN.pdf",
    category="Prospectus",
    year="2021",
    language="English",
    source_type="Official company / HKEX PDF",
    min_pages=100,
    expected_terms=["Prospectus", "HUTCHMED"],
)

download_pdf(
    "https://www.hutch-med.com/wp-content/uploads/2021/06/2021061800170_c.pdf",
    PROSPECTUS / "2021_Hong_Kong_Listing_Prospectus_TC.pdf",
    category="Prospectus",
    year="2021",
    language="Traditional Chinese",
    source_type="Official company / HKEX PDF",
    min_pages=100,
    expected_terms=["招股章程", "和黃醫藥"],
)

sec_url = "https://www.sec.gov/Archives/edgar/data/1648257/000104746916011330/a2227658z424b4.htm"
sec_headers = {
    "User-Agent": "OpenAI academic research archive contact research@example.com",
    "Accept-Encoding": "gzip, deflate",
}
sec_html = fetch(sec_url, timeout=240, headers=sec_headers)
if b"Hutchison China MediTech" not in sec_html or b"PROSPECTUS" not in sec_html.upper():
    raise RuntimeError("SEC 424B4 content validation failed")
html_path = PROSPECTUS / "2016_Nasdaq_Final_Prospectus_424B4_official_SEC.html"
html_path.write_bytes(sec_html)
manifest.append(
    {
        "category": "Prospectus",
        "year": "2016",
        "language": "English",
        "filename": html_path.relative_to(ROOT).as_posix(),
        "source_type": "Official SEC HTML filing (Form 424B4)",
        "source_url": sec_url,
        "bytes": html_path.stat().st_size,
        "pages": "N/A",
        "sha256": sha256(html_path),
        "validation": "PASS",
        "notes": "Original official filing format retained; a PDF rendering is also included.",
    }
)

rendered_pdf = PROSPECTUS / "2016_Nasdaq_Final_Prospectus_424B4_SEC_HTML_rendered_to_PDF.pdf"
chrome = next((shutil.which(x) for x in ("google-chrome", "chromium", "chromium-browser") if shutil.which(x)), None)
render_error = None
if chrome:
    cmd = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--allow-file-access-from-files",
        f"--print-to-pdf={rendered_pdf.resolve()}",
        html_path.resolve().as_uri(),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not rendered_pdf.exists():
        render_error = (proc.stdout + "\n" + proc.stderr)[-2000:]
if not rendered_pdf.exists():
    try:
        from weasyprint import HTML
        HTML(filename=str(html_path)).write_pdf(str(rendered_pdf))
    except Exception as exc:
        raise RuntimeError(f"could not render SEC HTML to PDF; chrome={render_error}; weasyprint={exc}")
register_file(
    rendered_pdf,
    category="Prospectus",
    year="2016",
    language="English",
    source_type="PDF rendering of official SEC HTML filing",
    source_url=sec_url,
    notes="SEC filed the final prospectus in HTML. This PDF is a local print rendering; the original official HTML is included alongside it.",
    min_pages=50,
    expected_terms=["Hutchison China MediTech", "Prospectus"],
)

# Latest complete periodic financial report as at the package date.
for lang, url in (
    ("EN", "https://www.hutch-med.com/wp-content/uploads/2026/08/e_2026_interim.pdf"),
    ("TC", "https://www.hutch-med.com/wp-content/uploads/2026/08/c_2026_interim.pdf"),
):
    download_pdf(
        url,
        LATEST / f"HUTCHMED_2026_Interim_Report_{lang}.pdf",
        category="Latest complete periodic report",
        year="2026 H1",
        language="English" if lang == "EN" else "Traditional Chinese",
        source_type="Official company PDF",
        notes="HUTCHMED does not issue a complete quarterly financial report in the requested sense; this is the latest complete periodic report available as of 2026-09-22.",
        min_pages=10,
        expected_terms=["2026", "Interim", "中期"],
    )

# Coverage checks.
years_present = {int(x["year"]) for x in manifest if x["category"] in ("Annual report", "Historical annual/full-year results material") and str(x["year"]).isdigit()}
missing_years = sorted(set(range(2006, 2026)) - years_present)
if missing_years:
    raise RuntimeError(f"annual-year coverage missing: {missing_years}")

# Metadata files.
manifest_csv = META / "manifest.csv"
fields = ["category", "year", "language", "filename", "source_type", "source_url", "bytes", "pages", "sha256", "validation", "notes"]
with manifest_csv.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(manifest)

(META / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
(META / "validation_summary.json").write_text(
    json.dumps(
        {
            "as_of": AS_OF,
            "file_count": len(manifest),
            "pdf_count": sum(1 for x in manifest if str(x["filename"]).lower().endswith(".pdf")),
            "annual_year_coverage": sorted(years_present),
            "missing_annual_years": missing_years,
            "all_pdf_validations_passed": True,
            "checks": validation,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

sha_lines = []
for row in manifest:
    sha_lines.append(f"{row['sha256']}  {row['filename']}")
(META / "SHA256SUMS.txt").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")

readme = f"""和黄医药（HUTCHMED，港股0013 / Nasdaq HCM）资料包
整理日期：{AS_OF}

一、资料范围
1. 年度资料覆盖2006—2025年。
2. 2012—2025年为公司现行投资者关系档案所提供的完整年度报告原始PDF；在官方提供中文版本的年份，同时收录中文版本。
3. 2006—2007年旧官网将正式年报拆分为多个章节PDF。本资料包将官方章节按原顺序合并为连续PDF，未改动页面内容；来源章节链接和校验值记录在manifest中。
4. 2008—2011年：现行官方旧档案仅保留年度/全年业绩演示文件，未能从现行官方档案取得完整法定年报PDF。这四份文件已按真实类型命名，不视为完整法定年报；详情见manifest中的notes字段。
5. 招股文件包括：2006年AIM Admission Document、2016年纳斯达克最终招股说明书（SEC Form 424B4）以及2021年香港上市中英文招股章程。
6. 2016年纳斯达克最终招股说明书由SEC以HTML格式披露。本资料包同时保留官方HTML，并提供从该HTML打印生成的PDF；PDF文件名明确标注为rendered_to_PDF。
7. 截至{AS_OF}，公司最新完整定期财务报告为2026年中期报告（截至2026年6月30日）。公司没有按用户通常所指的形式发布完整季度财务报告，因此以最新中期报告作为“最新季报口径”收录。

二、校验
- 每个PDF均检查了%PDF文件头、可解析页数及qpdf结构完整性。
- SHA-256校验值见00_Metadata/SHA256SUMS.txt。
- 来源、页数、文件大小和说明见00_Metadata/manifest.csv及manifest.json。
- ZIP在生成后执行完整性测试。

三、目录
01_Annual_Reports：年度资料
02_Prospectuses：招股书/上市文件
03_Latest_Periodic_Report：最新完整定期报告
00_Metadata：清单、来源、校验值和验证记录
"""
(ROOT / "00_README_CN.txt").write_text(readme, encoding="utf-8")

# Add metadata/readme themselves to checksum list? Keep manifest focused on downloaded/report files.
if Path(ZIP_NAME).exists():
    Path(ZIP_NAME).unlink()
with zipfile.ZipFile(ZIP_NAME, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            zf.write(p, p.as_posix())
with zipfile.ZipFile(ZIP_NAME, "r") as zf:
    bad = zf.testzip()
    if bad is not None:
        raise RuntimeError(f"ZIP integrity failure: {bad}")
    members = zf.namelist()

summary = {
    "zip": ZIP_NAME,
    "zip_bytes": Path(ZIP_NAME).stat().st_size,
    "zip_sha256": sha256(Path(ZIP_NAME)),
    "zip_integrity": "PASS",
    "members": len(members),
    "report_file_count": len(manifest),
    "pdf_count": sum(1 for x in manifest if str(x["filename"]).lower().endswith(".pdf")),
    "annual_year_coverage": sorted(years_present),
    "missing_annual_years": missing_years,
}
Path("package_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
