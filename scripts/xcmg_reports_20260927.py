from __future__ import annotations

import hashlib, json, re, subprocess, time, zipfile
from datetime import datetime
from pathlib import Path

import requests
from pypdf import PdfReader

CODE, COMPANY = "000425", "徐工机械"
PACKAGE = "徐工机械_000425_2020-2025年报_2026最新中期报告_官方原件"
OUT, ART = Path(PACKAGE), Path("artifact_out")
OUT.mkdir(exist_ok=True); ART.mkdir(exist_ok=True)

s = requests.Session(); s.trust_env = False
s.headers.update({
    "User-Agent": "Mozilla/5.0 Chrome/151 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/disclosure/stock?stockCode=000425&orgId=gssz0000425",
    "X-Requested-With": "XMLHttpRequest",
})


def query(page: int) -> dict:
    data = {
        "pageNum": str(page), "pageSize": "30", "column": "szse", "tabName": "fulltext",
        "plate": "sz", "stock": "000425,gssz0000425", "searchkey": "", "secid": "",
        "category": "category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh",
        "trade": "", "seDate": "2020-01-01~2026-09-27", "sortName": "", "sortType": "",
        "isHLtitle": "true",
    }
    last = None
    for attempt in range(1, 5):
        try:
            r = s.post("https://www.cninfo.com.cn/new/hisAnnouncement/query", data=data, timeout=(30, 180))
            print("QUERY", page, attempt, r.status_code, len(r.content), flush=True)
            r.raise_for_status(); return r.json()
        except Exception as exc:
            last = exc; time.sleep(attempt * 2)
    raise RuntimeError(f"query failed page {page}: {last!r}")


def clean_title(a: dict) -> str:
    t = str(a.get("announcementTitle") or a.get("title") or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t).replace("&nbsp;", " ")).strip()


def pub_date(a: dict) -> str:
    v = a.get("announcementTime") or a.get("publishTime") or a.get("announcementDate")
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000).strftime("%Y-%m-%d")
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", str(v or ""))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else str(v or "")[:10]


def pdf_url(a: dict) -> str:
    u = str(a.get("adjunctUrl") or a.get("url") or "").strip()
    return u if u.startswith("http") else "https://static.cninfo.com.cn/" + u.lstrip("/")


ann = []
seen_pages = set()
for p in range(1, 16):
    payload = query(p); batch = payload.get("announcements") or []
    print("BATCH", p, len(batch), flush=True)
    if not batch: break
    sig = tuple(str(x.get("announcementId") or x.get("adjunctUrl")) for x in batch)
    if sig in seen_pages: break
    seen_pages.add(sig); ann.extend(batch)
    if len(batch) < 30: break
uniq = {str(a.get("announcementId") or a.get("adjunctUrl")): a for a in ann}
ann = list(uniq.values())
Path("cninfo_announcements.json").write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
print("ANNOUNCEMENTS", len(ann), flush=True)

selected = []
for year in range(2020, 2026):
    cands = []
    for a in ann:
        t = clean_title(a)
        if f"{year}年年度报告" not in t: continue
        if any(x in t for x in ["摘要", "英文", "审计报告", "社会责任", "ESG", "问询函", "更正公告", "披露提示"]): continue
        exact = int(bool(re.fullmatch(rf"(徐工机械[:：]?)?{year}年年度报告(?:（修订版）|\(修订版\)|（更新后）|\(更新后\))?", t)))
        revised = int("修订" in t or "更新" in t)
        cands.append(((exact, revised, pub_date(a)), a))
    if not cands:
        raise RuntimeError(f"missing annual report {year}: {[clean_title(a) for a in ann if str(year) in clean_title(a)][:30]}")
    cands.sort(key=lambda x: x[0], reverse=True); a = cands[0][1]
    selected.append({"filename": f"{year-2019:02d}_徐工机械_000425_{year}年年度报告.pdf", "category": "年度报告",
                     "report_year": year, "title": clean_title(a), "disclosure_date": pub_date(a),
                     "source_url": pdf_url(a), "min_pages": 90})

latest = []
for a in ann:
    t = clean_title(a)
    if any(x in t for x in ["摘要", "英文", "提示性公告", "披露提示", "审计报告"]): continue
    for pat, q, kind, minp in [
        (r"(20\d{2})年第一季度报告", 1, "第一季度报告", 15),
        (r"(20\d{2})年半年度报告", 2, "半年度报告", 45),
        (r"(20\d{2})年第三季度报告", 3, "第三季度报告", 15),
    ]:
        m = re.search(pat, t)
        if m:
            latest.append((int(m.group(1)) * 10 + q, pub_date(a), kind, minp, a)); break
if not latest: raise RuntimeError("no interim or quarterly report")
latest.sort(key=lambda x: (x[0], x[1]), reverse=True)
period, _, kind, minp, a = latest[0]; year = period // 10
selected.append({"filename": f"07_徐工机械_000425_{year}年{kind}_最新定期报告.pdf", "category": f"最新{kind}",
                 "report_year": year, "title": clean_title(a), "disclosure_date": pub_date(a),
                 "source_url": pdf_url(a), "min_pages": minp})
print("SELECTED", json.dumps(selected, ensure_ascii=False, indent=2), flush=True)


def download(doc: dict, dest: Path) -> str:
    headers = {"User-Agent": s.headers["User-Agent"], "Referer": "https://www.cninfo.com.cn/", "Accept": "application/pdf,*/*"}
    last = None
    for attempt in range(1, 5):
        tmp = dest.with_suffix(".pdf.part")
        try:
            with requests.get(doc["source_url"], headers=headers, timeout=(30, 300), stream=True) as r:
                print("DOWNLOAD", dest.name, attempt, r.status_code, r.url, flush=True)
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk: f.write(chunk)
                final_url = r.url
            if tmp.stat().st_size < 120000 or not tmp.open("rb").read(8).startswith(b"%PDF-"):
                raise RuntimeError("invalid PDF")
            tmp.replace(dest); return str(final_url)
        except Exception as exc:
            last = exc; tmp.unlink(missing_ok=True); time.sleep(attempt * 2)
    raise RuntimeError(f"download failed {dest.name}: {last!r}")


def sample(reader: PdfReader) -> str:
    n = len(reader.pages); idx = list(range(min(35, n)))
    if n > 40: idx += list(range(max(0, n - 5), n))
    parts = []
    for i in dict.fromkeys(idx):
        try: parts.append(reader.pages[i].extract_text() or "")
        except Exception: pass
    return "\n".join(parts)

records, hashes = [], set()
for doc in selected:
    dest = OUT / doc["filename"]; final = download(doc, dest)
    reader = PdfReader(str(dest), strict=False); pages = len(reader.pages)
    if pages < doc["min_pages"]: raise RuntimeError(f"short PDF {dest.name}: {pages}")
    txt = sample(reader); low = txt.lower()
    if not (COMPANY in txt or CODE in txt or "xcmg machinery" in low or "徐工集团工程机械股份有限公司" in txt):
        raise RuntimeError(f"identity not verified: {dest.name}")
    q = subprocess.run(["qpdf", "--check", str(dest)], capture_output=True, text=True)
    if q.returncode not in (0, 3): raise RuntimeError(f"qpdf failed: {dest.name}")
    data = dest.read_bytes(); sha = hashlib.sha256(data).hexdigest()
    if sha in hashes: raise RuntimeError(f"duplicate: {dest.name}")
    hashes.add(sha)
    rec = {k: doc[k] for k in ("filename", "category", "report_year", "title", "disclosure_date")}
    rec.update(source="巨潮资讯网", source_url=final, pages=pages, bytes=len(data), sha256=sha)
    records.append(rec); print("VALIDATED", json.dumps(rec, ensure_ascii=False), flush=True)
if len(records) != 7: raise RuntimeError(f"expected 7 PDFs, got {len(records)}")

latest_rec = records[-1]
lines = ["徐工机械（000425.SZ）2020—2025年度报告及最新一期定期财务报告", "整理日期：2026-09-27", "",
         "一、收录范围", "1. 2020—2025年完整年度报告，共6份。", f"2. 最新一期完整定期报告：{latest_rec['title']}。", "",
         "二、关于“最新季报”", "优先收录截至整理日最新的完整半年度报告；如尚未发布，则收录最新季度报告。",
         f"最新文件披露日期：{latest_rec['disclosure_date']}。", "", "三、文件清单"]
for i, r in enumerate(records, 1):
    lines += [f"{i}. {r['filename']}", f"   类别：{r['category']}；报告年度：{r['report_year']}；披露日期：{r['disclosure_date']}",
              f"   官方标题：{r['title']}", f"   页数：{r['pages']}；大小：{r['bytes']/1048576:.2f} MB",
              f"   来源：{r['source_url']}", f"   SHA-256：{r['sha256']}", ""]
lines += ["四、完整性核验", "全部PDF均已检查文件头、公司身份、实际页数、qpdf结构、重复文件及SHA-256；最终ZIP已通过CRC测试。"]
(OUT / "00_文件清单与范围说明.txt").write_text("\n".join(lines), encoding="utf-8")
(OUT / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
(OUT / "SHA256SUMS.txt").write_text("".join(f"{r['sha256']}  {r['filename']}\n" for r in records), encoding="utf-8")
(OUT / "CNINFO公告索引.json").write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")

zpath = ART / f"{PACKAGE}.zip"
with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
    for p in sorted(OUT.iterdir()): z.write(p, arcname=f"{PACKAGE}/{p.name}")
with zipfile.ZipFile(zpath) as z:
    bad = z.testzip(); pdfs = [n for n in z.namelist() if n.lower().endswith(".pdf")]
    if bad or len(pdfs) != 7: raise RuntimeError(f"ZIP validation failed: {bad}, {len(pdfs)}")
print("FINAL_ZIP", zpath.name, zpath.stat().st_size, "SHA256", hashlib.sha256(zpath.read_bytes()).hexdigest(), flush=True)
print("FINAL_MANIFEST", json.dumps(records, ensure_ascii=False, indent=2), flush=True)
