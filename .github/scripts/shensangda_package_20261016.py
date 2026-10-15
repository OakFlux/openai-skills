from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader

CODE = "000032"
AS_OF = "2026-10-16"
PACKAGE = "深桑达A_000032_上市以来年报_招股说明书及最新季报_截至20261016"
ROOT = Path(PACKAGE)
ANNUAL_DIR = ROOT / "01_年度报告"
PROSPECTUS_DIR = ROOT / "00_招股说明书"
QUARTER_DIR = ROOT / "02_最新季度报告"
PREVIEW_DIR = ROOT / "首页预览"
for d in [ROOT, ANNUAL_DIR, PROSPECTUS_DIR, QUARTER_DIR, PREVIEW_DIR]:
    d.mkdir(parents=True, exist_ok=True)

s = requests.Session()
s.trust_env = False
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})

def compact(x: Any) -> str:
    return re.sub(r"\s+", "", str(x or ""))

def art_code(row: dict[str, Any]) -> str:
    return str(row.get("art_code") or row.get("artCode") or "")

# Query the complete public announcement index.
rows: list[dict[str, Any]] = []
api = "https://np-anotice-stock.eastmoney.com/api/security/ann"
for page in range(1, 80):
    params = {
        "sr": "-1", "page_size": "100", "page_index": str(page),
        "ann_type": "A", "client_source": "web", "stock_list": CODE,
        "f_node": "0", "s_node": "0",
    }
    r = s.get(api, params=params, timeout=(20, 120))
    r.raise_for_status()
    batch = ((r.json().get("data") or {}).get("list") or [])
    if not batch:
        break
    rows.extend(x for x in batch if isinstance(x, dict))

specs: list[dict[str, Any]] = []

# 1993-2000: the indexed official archive exposes annual-report summaries rather than full PDFs.
early = [
    (1993, "AN201202240003357048", "深圳桑达实业股份有限公司一九九三年年度报告(要)"),
    (1994, "AN201202240003351332", "深圳桑达实业股份有限公司1994年年度报告(摘要)"),
    (1995, "AN201202240003353725", "深圳桑达实业股份有限公司1995年年度报告摘要"),
    (1996, "AN201202240003338480", "深圳桑达实业股份有限公司1996年年度报告摘要"),
    (1997, "AN201202240003329187", "深圳市桑达实业股份有限公司1997年年度报告摘要"),
    (1998, "AN201202240003297625", "深圳市桑达实业股份有限公司1998年年度报告摘要"),
    (1999, "AN201202250003416712", "深圳市桑达实业股份有限公司1999年年度报告摘要"),
    (2000, "AN201202250003495326", "深圳市桑达实业股份有限公司2000年年度报告摘要"),
]
for year, art, title in early:
    specs.append({
        "kind": "annual_summary", "year": year, "title": title,
        "art_code": art, "folder": ANNUAL_DIR,
        "filename": f"{year}_深桑达A_{year}年年度报告摘要_官方存档.pdf",
        "min_pages": 1,
        "note": "该年度在可检索的官方电子档案中仅提供年度报告摘要。",
    })

# 2001-2006 full annual reports from CNINFO's official archive.
cninfo = [
    (2001, "finalpage/2002-03-01/546049.PDF"),
    (2002, "finalpage/2003-04-12/10491136.PDF"),
    (2003, "finalpage/2004-03-27/13834590.PDF"),
    (2004, "finalpage/2005-04-02/15176435.PDF"),
    (2005, "finalpage/2006-04-01/16679255.PDF"),
    (2006, "finalpage/2007-04-07/22518260.PDF"),
]
for year, path in cninfo:
    specs.append({
        "kind": "annual_full", "year": year,
        "title": f"深桑达A:{year}年年度报告",
        "urls": [f"https://static.cninfo.com.cn/{path}", f"http://static.cninfo.com.cn/{path}"],
        "source_page": f"https://static.cninfo.com.cn/{path}",
        "folder": ANNUAL_DIR,
        "filename": f"{year}_深桑达A_{year}年年度报告_完整版.pdf",
        "min_pages": 10,
    })

# 2007-2025 full annual reports selected from the public announcement index.
bad_annual = ["摘要", "半年度", "英文版", "社会责任", "可持续发展", "内部控制", "审计报告", "工作规程", "说明会", "独立意见"]
for year in range(2007, 2026):
    candidates = []
    for row in rows:
        title = str(row.get("title") or "")
        tc = compact(title)
        if (f"{year}年年度报告" not in tc and f"{year}年度报告" not in tc) or any(x in tc for x in bad_annual):
            continue
        score = 100
        if tc.endswith(f"{year}年年度报告") or tc.endswith(f"{year}年度报告"):
            score += 50
        if any(x in tc for x in ["修订版", "更新后", "更正后"]):
            score += 80
        candidates.append((score, title, row))
    if not candidates:
        raise RuntimeError(f"未找到{year}年完整年度报告")
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, title, row = candidates[0]
    specs.append({
        "kind": "annual_full", "year": year, "title": title,
        "art_code": art_code(row), "folder": ANNUAL_DIR,
        "filename": f"{year}_深桑达A_{year}年年度报告_完整版.pdf",
        "min_pages": 10,
        "announcement_date": row.get("display_time"),
    })

# Original prospectus.
prospectus_candidates = []
for row in rows:
    title = str(row.get("title") or "")
    tc = compact(title)
    if "招股说明书" in tc and not any(x in tc for x in ["摘要", "提示", "公告"]):
        prospectus_candidates.append((len(tc), title, row))
if not prospectus_candidates:
    raise RuntimeError("未找到原始招股说明书")
prospectus_candidates.sort(key=lambda x: x[0])
_, prospectus_title, prospectus_row = prospectus_candidates[0]
specs.insert(0, {
    "kind": "prospectus", "title": prospectus_title,
    "art_code": art_code(prospectus_row), "folder": PROSPECTUS_DIR,
    "filename": "深桑达A_原始招股说明书.pdf", "min_pages": 5,
    "announcement_date": prospectus_row.get("display_time"),
})

# Latest independent quarterly report available as of the package date.
quarter_patterns = [
    ("2026Q3", ["2026年第三季度报告", "2026年三季度报告"]),
    ("2026Q1", ["2026年第一季度报告", "2026年一季度报告"]),
    ("2025Q3", ["2025年第三季度报告", "2025年三季度报告"]),
]
quarter_spec = None
for period, patterns in quarter_patterns:
    qrows = []
    for row in rows:
        title = str(row.get("title") or "")
        tc = compact(title)
        if any(p in tc for p in patterns) and not any(x in tc for x in ["正文", "摘要", "董事会", "独立意见"]):
            qrows.append(row)
    if qrows:
        row = qrows[0]
        title = str(row.get("title") or "")
        quarter_spec = {
            "kind": "quarter", "period": period, "title": title,
            "art_code": art_code(row), "folder": QUARTER_DIR,
            "filename": f"深桑达A_{period}_最新季度报告.pdf", "min_pages": 3,
            "announcement_date": row.get("display_time"),
        }
        break
if quarter_spec is None:
    raise RuntimeError("未找到最新独立季度报告")
specs.append(quarter_spec)


def eastmoney_urls(art: str) -> list[str]:
    return [f"https://pdf.dfcfw.com/pdf/H{x}_{art}_1.pdf" for x in (3, 2, 1, 4)]

def fetch_pdf(spec: dict[str, Any]) -> tuple[bytes, str, dict[str, Any]]:
    urls = list(spec.get("urls") or [])
    art = str(spec.get("art_code") or "")
    if art:
        urls.extend(eastmoney_urls(art))
    last: dict[str, Any] = {}
    for url in urls:
        for attempt in range(5):
            try:
                referer = "https://www.cninfo.com.cn/" if "cninfo" in url else f"https://data.eastmoney.com/notices/detail/{CODE}/{art}.html"
                r = s.get(url, timeout=(30, 420), allow_redirects=True, headers={
                    "User-Agent": s.headers["User-Agent"], "Referer": referer,
                    "Accept": "application/pdf,*/*;q=0.8",
                })
                last = {"url": url, "status": r.status_code, "bytes": len(r.content), "content_type": r.headers.get("content-type", ""), "final_url": r.url}
                if r.status_code == 200 and r.content[:5] == b"%PDF-" and len(r.content) > 5000:
                    return r.content, url, last
            except Exception as exc:
                last = {"url": url, "error": repr(exc)}
            time.sleep(1 + attempt)
    raise RuntimeError(f"PDF下载失败: {spec['title']} | {last}")

manifest: list[dict[str, Any]] = []
seen_hashes: dict[str, str] = {}
for i, spec in enumerate(specs, 1):
    data, source_url, dlmeta = fetch_pdf(spec)
    dest = Path(spec["folder"]) / spec["filename"]
    dest.write_bytes(data)
    reader = PdfReader(str(dest), strict=False)
    pages = len(reader.pages)
    if pages < int(spec["min_pages"]):
        raise RuntimeError(f"页数异常: {dest.name} = {pages}")
    subprocess.run(["qpdf", "--warning-exit-0", "--check", str(dest)], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    text = ""
    for page in reader.pages[: min(12, pages)]:
        try:
            text += "\n" + (page.extract_text() or "")
        except Exception:
            pass
    tc = compact(text)
    identity_hits = [x for x in ["深桑达", "桑达实业", "深圳市桑达", "000032"] if compact(x) in tc]
    if len(tc) >= 150 and not identity_hits:
        raise RuntimeError(f"公司身份核验失败: {dest.name}")

    sha = hashlib.sha256(data).hexdigest()
    if sha in seen_hashes:
        raise RuntimeError(f"发现重复PDF: {dest.name} 与 {seen_hashes[sha]}")
    seen_hashes[sha] = dest.name

    preview_prefix = PREVIEW_DIR / f"{i:02d}_{dest.stem}"
    subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-png", "-r", "105", "-singlefile", str(dest), str(preview_prefix)], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    preview = Path(str(preview_prefix) + ".png")
    if not preview.exists() or preview.stat().st_size < 3000:
        raise RuntimeError(f"首页预览失败: {dest.name}")

    source_page = spec.get("source_page") or (f"https://data.eastmoney.com/notices/detail/{CODE}/{spec.get('art_code')}.html" if spec.get("art_code") else source_url)
    manifest.append({
        "kind": spec["kind"], "year": spec.get("year"), "period": spec.get("period"),
        "title": spec["title"], "filename": str(dest.relative_to(ROOT)),
        "pages": pages, "bytes": len(data), "sha256": sha,
        "source_url": source_url, "source_page": source_page,
        "art_code": spec.get("art_code"), "announcement_date": spec.get("announcement_date"),
        "identity_hits": identity_hits, "note": spec.get("note", ""),
        "validation": ["PDF header", "page count", "qpdf structure", "first-page render", "issuer text when extractable"],
        "download_meta": dlmeta,
    })

annual_full = sum(x["kind"] == "annual_full" for x in manifest)
annual_summary = sum(x["kind"] == "annual_summary" for x in manifest)
quarter = next(x for x in manifest if x["kind"] == "quarter")
readme = [
    "深圳市桑达实业股份有限公司（深桑达A，000032.SZ）上市以来年报、招股说明书及最新季报资料包",
    f"整理日期：{AS_OF}", "",
    f"文件合计：{len(manifest)}份PDF；其中完整年度报告{annual_full}份、早期年度报告摘要{annual_summary}份、原始招股说明书1份、最新独立季度报告1份。",
    f"最新独立季度报告：{quarter['title']}。", "",
    "档案说明：2001—2025年收录完整年度报告；1993—2000年在可检索的官方电子档案中仅提供年度报告摘要，因此收录对应官方摘要，并在文件名与清单中明确标识。",
    "未将半年度报告、年度报告摘要的重复版本、英文版、社会责任报告、内控报告、董事会/监事会文件混入。", "",
]
for i, x in enumerate(manifest, 1):
    readme += [
        f"{i}. {x['filename']}",
        f"   标题：{x['title']}",
        f"   页数：{x['pages']}页；SHA-256：{x['sha256']}",
        f"   来源页面：{x['source_page']}",
        (f"   说明：{x['note']}" if x["note"] else ""), "",
    ]
readme += ["全部文件已检查PDF文件头、页数、qpdf结构、首页渲染、ZIP CRC及SHA-256。"]
(ROOT / "00_文件清单与档案说明.txt").write_text("\n".join(readme), encoding="utf-8")
(ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
(ROOT / "SHA256SUMS.txt").write_text("".join(f"{x['sha256']}  {x['filename']}\n" for x in manifest), encoding="utf-8")

zip_path = Path(PACKAGE + ".zip")
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            z.write(p, arcname=f"{PACKAGE}/{p.relative_to(ROOT)}")
with zipfile.ZipFile(zip_path) as z:
    bad = z.testzip()
    pdfs = [n for n in z.namelist() if n.lower().endswith(".pdf")]
    if bad or len(pdfs) != len(manifest):
        raise RuntimeError(f"ZIP核验失败 bad={bad} pdfs={len(pdfs)} expected={len(manifest)}")

alias = Path("shensangda_000032_all_annual_reports_prospectus_latest_quarter_20261016.zip")
alias.write_bytes(zip_path.read_bytes())
zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
print("FINAL_ZIP", zip_path, zip_path.stat().st_size, "SHA256", zip_sha)
print("COUNTS", json.dumps({"pdfs": len(manifest), "annual_full": annual_full, "annual_summary": annual_summary, "quarter": quarter["title"]}, ensure_ascii=False))
print("MANIFEST", json.dumps(manifest, ensure_ascii=False, indent=2))
