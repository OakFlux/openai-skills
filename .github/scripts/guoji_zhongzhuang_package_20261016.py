from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from pypdf import PdfReader

AS_OF = "2026-10-16"
ROOT_NAME = "国机重装_601399_上市以来全部年报_招股说明书及最新季报_截至20261016"
ROOT = Path(ROOT_NAME)
LISTING_DIR = ROOT / "00_上市文件"
ANNUAL_DIR = ROOT / "01_年度报告"
QUARTER_DIR = ROOT / "02_最新季度报告"
PREVIEW_DIR = ROOT / "03_首页预览"
for directory in (LISTING_DIR, ANNUAL_DIR, QUARTER_DIR, PREVIEW_DIR):
    directory.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
east = requests.Session()
east.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
neeq = requests.Session()
neeq.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", text).strip()


def normalize_url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin("https://pdf.dfcfw.com/", url)
    return url


def extract_pdf_text(path: Path, pages: int = 5) -> str:
    result = subprocess.run(
        ["pdftotext", "-f", "1", "-l", str(pages), "-layout", str(path), "-"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    return result.stdout


def verify_pdf(path: Path, identity_terms: list[str], preview_stem: str) -> dict[str, Any]:
    if path.stat().st_size < 20_000 or not path.read_bytes()[:4] == b"%PDF":
        raise RuntimeError(f"Invalid PDF header or size: {path}")
    subprocess.run(
        ["qpdf", "--check", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    reader = PdfReader(str(path))
    pages = len(reader.pages)
    if pages < 2:
        raise RuntimeError(f"Unexpected page count for {path}: {pages}")
    text = extract_pdf_text(path, min(6, pages)).replace(" ", "")
    identity_hits = [term for term in identity_terms if term.replace(" ", "") in text]
    if text.strip() and not identity_hits:
        # Some old annual reports place the legal name after the first pages. Retry more pages.
        text2 = extract_pdf_text(path, min(15, pages)).replace(" ", "")
        identity_hits = [term for term in identity_terms if term.replace(" ", "") in text2]
        if text2.strip() and not identity_hits:
            raise RuntimeError(f"Issuer identity not found in extractable text: {path}")
    preview_base = PREVIEW_DIR / preview_stem
    subprocess.run(
        ["pdftoppm", "-f", "1", "-singlefile", "-png", "-r", "110", str(path), str(preview_base)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    preview = preview_base.with_suffix(".png")
    if not preview.exists() or preview.stat().st_size < 1_000:
        raise RuntimeError(f"First-page render failed: {path}")
    return {"pages": pages, "bytes": path.stat().st_size, "sha256": sha256(path), "identity_hits": identity_hits}


def eastmoney_candidate_urls(art: str, code: str) -> list[str]:
    urls: list[str] = []
    detail_url = f"https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={art}&client_source=web&page_index=1"
    try:
        response = east.get(
            detail_url,
            timeout=(20, 90),
            headers={
                "User-Agent": UA,
                "Referer": f"https://data.eastmoney.com/notices/detail/{code}/{art}.html",
            },
        )
        if response.ok:
            data = response.json().get("data") or {}
            for item in data.get("attach_list") or []:
                for key in ("attach_url", "attach_url_web"):
                    if item.get(key):
                        urls.append(normalize_url(item[key]))
            for key in ("attach_url_web", "attach_url"):
                if data.get(key):
                    urls.append(normalize_url(data[key]))
    except Exception as exc:
        print("DETAIL_API_WARNING", art, repr(exc))
    urls.extend(
        f"https://pdf.dfcfw.com/pdf/H{host}_{art}_1.pdf"
        for host in (2, 3, 1, 22, 4)
    )
    return list(dict.fromkeys(url for url in urls if url))


def download_eastmoney(art: str, code: str) -> tuple[bytes, str]:
    errors: list[str] = []
    for url in eastmoney_candidate_urls(art, code):
        try:
            response = east.get(
                url,
                timeout=(20, 180),
                allow_redirects=True,
                headers={
                    "User-Agent": UA,
                    "Referer": f"https://data.eastmoney.com/notices/detail/{code}/{art}.html",
                },
            )
            content = response.content
            if response.status_code == 200 and content.startswith(b"%PDF") and len(content) > 20_000:
                return content, response.url
            errors.append(f"{url}: status={response.status_code}, bytes={len(content)}")
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError(f"Failed to download Eastmoney filing {art}: " + " | ".join(errors[-10:]))


def parse_jsonp(raw: str, callback: str) -> Any:
    text = raw.strip().rstrip(";")
    for prefix in (callback + "(", "null(", "callback("):
        if text.startswith(prefix) and text.endswith(")"):
            text = text[len(prefix) : -1]
            break
    if not text.startswith(("{", "[")) and "(" in text and text.endswith(")"):
        text = text[text.find("(") + 1 : -1]
    return json.loads(text)


def crawl_neeq_annual_reports() -> dict[int, dict[str, Any]]:
    base = "https://www.neeq.com.cn"
    page_url = base + "/disclosure/snadc_announcement.html"
    endpoint = base + "/disclosureInfoController/infoResult_en.do"
    warm = neeq.get(page_url, timeout=40)
    warm.raise_for_status()
    callback = "jQuery183_guoji_1770000000000"
    need = ["companyCd", "companyName", "disclosureTitle", "disclosurePostTitle", "destFilePath", "publishDate", "fileExt"]
    all_rows: list[dict[str, Any]] = []
    total_pages = 1
    page = 0
    while page < total_pages:
        form: list[tuple[str, str]] = [
            ("flag", "1"),
            ("disclosureType", "5"),
            ("page", str(page)),
            ("companyCd", "400062"),
            ("isNewThree", "0"),
            ("startTime", "2015-01-01"),
            ("endTime", "2020-06-30"),
            ("keyword", ""),
        ]
        for field in need:
            form.append(("needFields", field))
        response = neeq.post(
            endpoint,
            params={"callback": callback, "_": "1770000000000"},
            data=form,
            timeout=(20, 90),
            headers={
                "User-Agent": UA,
                "Origin": base,
                "Referer": page_url,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        )
        response.raise_for_status()
        payload = parse_jsonp(response.text, callback)
        blocks = payload if isinstance(payload, list) else [payload]
        if not blocks or not isinstance(blocks[0], dict):
            raise RuntimeError(f"Unexpected NEEQ response on page {page}")
        list_info = blocks[0].get("listInfo") or {}
        rows = list_info.get("content") or []
        all_rows.extend(rows)
        total_pages = int(list_info.get("totalPages") or 0)
        print("NEEQ_PAGE", page, "ROWS", len(rows), "TOTAL_PAGES", total_pages)
        page += 1

    selected: dict[int, dict[str, Any]] = {}
    for year in range(2015, 2020):
        candidates: list[dict[str, Any]] = []
        for row in all_rows:
            title = str(row.get("disclosureTitle") or "").strip()
            body = re.split(r"[:：]", title, maxsplit=1)[-1]
            normalized = re.sub(r"\s+", "", body)
            pattern = rf"{year}年(?:度)?报告(?:（(?:修订版|更正版|更正后|修订稿)）|\((?:修订版|更正版|更正后|修订稿)\)|(?:修订版|更正版|更正后|修订稿))?"
            if re.fullmatch(pattern, normalized):
                candidates.append(row)
        if not candidates:
            near = [r for r in all_rows if str(year) in str(r.get("disclosureTitle")) and "年度报告" in re.sub(r"\s+", "", str(r.get("disclosureTitle")))]
            raise RuntimeError(f"No exact NEEQ annual report for {year}; near={json.dumps(near, ensure_ascii=False)[:3000]}")
        def score(row: dict[str, Any]) -> tuple[int, str, str]:
            title = str(row.get("disclosureTitle") or "")
            return (int(any(k in title for k in ("修订", "更正"))), str(row.get("publishDate") or ""), str(row.get("destFilePath") or ""))
        selected[year] = max(candidates, key=score)
        print("NEEQ_SELECTED", year, json.dumps(selected[year], ensure_ascii=False, sort_keys=True))
    return selected


def download_neeq(path: str) -> tuple[bytes, str]:
    base = "https://www.neeq.com.cn"
    page_url = base + "/disclosure/snadc_announcement.html"
    urls = [urljoin(base, path)]
    if urls[0].startswith("https://"):
        urls.append("http://" + urls[0][8:])
    errors: list[str] = []
    for url in urls:
        try:
            response = neeq.get(
                url,
                timeout=(20, 180),
                allow_redirects=True,
                headers={"User-Agent": UA, "Referer": page_url},
            )
            content = response.content
            if response.status_code == 200 and content.startswith(b"%PDF") and len(content) > 20_000:
                return content, response.url
            errors.append(f"{url}: status={response.status_code}, bytes={len(content)}, ctype={response.headers.get('content-type')}")
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError("Failed to download NEEQ filing: " + " | ".join(errors))


# Art codes identified from the public announcement archives.
eastmoney_items: list[dict[str, Any]] = [
    {"kind": "annual", "year": 2009, "art": "AN201203010004563948", "code": "601268", "issuer": "二重重装"},
    {"kind": "annual", "year": 2010, "art": "AN201203020004750260", "code": "601268", "issuer": "二重重装"},
    {"kind": "annual", "year": 2011, "art": "AN201205070005148665", "code": "601268", "issuer": "二重重装"},
    {"kind": "annual", "year": 2012, "art": "AN201304220003196350", "code": "601268", "issuer": "二重重装"},
    {"kind": "annual", "year": 2013, "art": "AN201404280005551779", "code": "601268", "issuer": "二重重装"},
    {"kind": "annual", "year": 2014, "art": "AN201504290009434613", "code": "601268", "issuer": "二重重装"},
    {"kind": "annual", "year": 2020, "art": "AN202104201486654508", "code": "601399", "issuer": "国机重装"},
    {"kind": "annual", "year": 2021, "art": "AN202204221560869620", "code": "601399", "issuer": "国机重装"},
    {"kind": "annual", "year": 2022, "art": "AN202304271585991752", "code": "601399", "issuer": "国机重装"},
    {"kind": "annual", "year": 2023, "art": "AN202404231631059755", "code": "601399", "issuer": "国机重装"},
    {"kind": "annual", "year": 2024, "art": "AN202504221660547121", "code": "601399", "issuer": "国机重装"},
    {"kind": "annual", "year": 2025, "art": "AN202604131821156525", "code": "601399", "issuer": "国机重装"},
    {"kind": "prospectus", "year": 2010, "art": "AN201202290004527563", "code": "601268", "issuer": "二重重装"},
    {"kind": "relisting_report", "year": 2020, "art": "AN202005291380804804", "code": "601399", "issuer": "国机重装"},
    {"kind": "relisting_application", "year": 2020, "art": "AN202005291380804803", "code": "601399", "issuer": "国机重装"},
    {"kind": "quarter", "period": "2026Q1", "art": "AN202604271821613912", "code": "601399", "issuer": "国机重装"},
]

aliases = [
    "国机重装",
    "国机重型装备集团股份有限公司",
    "二重重装",
    "二重集团（德阳）重型装备股份有限公司",
    "二重集团(德阳)重型装备股份有限公司",
    "国重装5",
    "二重5",
    "601399",
    "601268",
    "400062",
]

manifest: list[dict[str, Any]] = []

# Download Eastmoney-backed filings.
for item in eastmoney_items:
    content, source_url = download_eastmoney(item["art"], item["code"])
    kind = item["kind"]
    if kind == "annual":
        filename = safe_name(f"{item['year']}_{item['issuer']}_{item['year']}年年度报告.pdf")
        target = ANNUAL_DIR / filename
        preview_stem = f"年报_{item['year']}"
        title = f"{item['issuer']} {item['year']}年年度报告"
        period = str(item["year"])
    elif kind == "prospectus":
        filename = "2010_二重重装_首次公开发行股票招股说明书.pdf"
        target = LISTING_DIR / filename
        preview_stem = "上市文件_2010_招股说明书"
        title = "二重集团（德阳）重型装备股份有限公司首次公开发行股票招股说明书"
        period = "2010 IPO"
    elif kind == "relisting_report":
        filename = "2020_国机重装_重新上市报告书.pdf"
        target = LISTING_DIR / filename
        preview_stem = "上市文件_2020_重新上市报告书"
        title = "国机重型装备集团股份有限公司重新上市报告书"
        period = "2020 relisting"
    elif kind == "relisting_application":
        filename = "2020_国机重装_重新上市申请书.pdf"
        target = LISTING_DIR / filename
        preview_stem = "上市文件_2020_重新上市申请书"
        title = "国机重型装备集团股份有限公司重新上市申请书"
        period = "2020 relisting application"
    elif kind == "quarter":
        filename = "2026Q1_国机重装_2026年第一季度报告.pdf"
        target = QUARTER_DIR / filename
        preview_stem = "季报_2026Q1"
        title = "国机重装2026年第一季度报告"
        period = item["period"]
    else:
        raise AssertionError(kind)
    target.write_bytes(content)
    verified = verify_pdf(target, aliases, preview_stem)
    row = {
        "kind": kind,
        "period": period,
        "title": title,
        "filename": str(target.relative_to(ROOT)),
        "art_code": item["art"],
        "source_url": source_url,
        "source_page": f"https://data.eastmoney.com/notices/detail/{item['code']}/{item['art']}.html",
        **verified,
    }
    manifest.append(row)
    print("VERIFIED_EASTMONEY", json.dumps(row, ensure_ascii=False, sort_keys=True))

# Download the five annual reports disclosed during the delisted-board phase.
neeq_selected = crawl_neeq_annual_reports()
for year in range(2015, 2020):
    rec = neeq_selected[year]
    content, source_url = download_neeq(str(rec.get("destFilePath") or ""))
    issuer = str(rec.get("companyName") or ("二重5" if year <= 2017 else "国重装5"))
    filename = safe_name(f"{year}_{issuer}_{year}年年度报告.pdf")
    target = ANNUAL_DIR / filename
    target.write_bytes(content)
    verified = verify_pdf(target, aliases, f"年报_{year}")
    row = {
        "kind": "annual",
        "period": str(year),
        "title": str(rec.get("disclosureTitle") or ""),
        "filename": str(target.relative_to(ROOT)),
        "company_code": str(rec.get("companyCd") or "400062"),
        "company_name": issuer,
        "announcement_date": str(rec.get("publishDate") or ""),
        "disclosure_code": str(rec.get("disclosureCode") or ""),
        "source_url": source_url,
        "source_page": "https://www.neeq.com.cn/disclosure/snadc_announcement.html?companyCode=400062",
        "archive_path": str(rec.get("destFilePath") or ""),
        **verified,
    }
    manifest.append(row)
    print("VERIFIED_NEEQ", json.dumps(row, ensure_ascii=False, sort_keys=True))

# Sort manifest for readable inventories: listing docs, annual reports, then latest quarter.
kind_order = {"prospectus": 0, "relisting_application": 1, "relisting_report": 2, "annual": 3, "quarter": 4}
manifest.sort(key=lambda row: (kind_order.get(row["kind"], 99), str(row["period"])))
annual_rows = [row for row in manifest if row["kind"] == "annual"]
if [int(row["period"]) for row in annual_rows] != list(range(2009, 2026)):
    raise RuntimeError("Annual report coverage is not continuous from 2009 through 2025")

readme = [
    "国机重装（601399）上市以来年度报告、上市文件及最新季度报告",
    f"整理日期：{AS_OF}",
    "",
    "一、文件范围",
    "1. 年度报告：2009—2025年完整年度报告，共17份。",
    "2. 上市文件：2010年首次公开发行股票招股说明书、2020年重新上市申请书、2020年重新上市报告书，共3份。",
    "3. 最新独立季度报告：2026年第一季度报告，共1份。",
    "",
    "二、历史代码和资料来源",
    "1. 2009—2014年：原A股主体‘二重重装’，证券代码601268。",
    "2. 2015—2019年：退市板块阶段，证券代码400062，历史简称包括‘二重5’和‘国重装5’；年报取自全国股转系统‘两网及退市公司公告’原始PDF。",
    "3. 2020—2025年：重新上市后‘国机重装’，证券代码601399。",
    "",
    "三、核验",
    "每份PDF均检查了PDF文件头、qpdf结构、实际页数、可提取文本中的公司身份（如存在文本层）以及首页渲染；ZIP另做CRC完整性检查。",
    "PDF及研究资料版权归披露主体和原发布平台所有，本压缩包仅供个人研究与学习。",
    "",
    "四、文件清单",
]
for index, row in enumerate(manifest, 1):
    readme.extend(
        [
            f"{index}. [{row['kind']}] {row['period']}｜{row['title']}",
            f"   文件：{row['filename']}",
            f"   页数：{row['pages']}页；大小：{row['bytes']}字节",
            f"   来源：{row['source_url']}",
            f"   SHA-256：{row['sha256']}",
            "",
        ]
    )

(ROOT / "文件清单与说明.txt").write_text("\n".join(readme), encoding="utf-8")
(ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
(ROOT / "SHA256SUMS.txt").write_text(
    "\n".join(f"{row['sha256']}  {row['filename']}" for row in manifest) + "\n",
    encoding="utf-8",
)

zip_cn = Path(ROOT_NAME + ".zip")
with zipfile.ZipFile(zip_cn, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(ROOT.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(ROOT.parent))
with zipfile.ZipFile(zip_cn, "r") as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f"ZIP CRC failure: {bad}")

zip_en = Path("guoji_zhongzhuang_601399_all_annual_reports_prospectus_latest_quarter_20261016.zip")
shutil.copy2(zip_cn, zip_en)
print("FINAL_ZIP", zip_cn.name, zip_cn.stat().st_size, "SHA256", sha256(zip_cn))
print("COUNTS", json.dumps({"annual": len(annual_rows), "listing": 3, "quarter": 1, "pdfs": len(manifest)}, ensure_ascii=False))
print("ANNUAL_YEARS", [row["period"] for row in annual_rows])
