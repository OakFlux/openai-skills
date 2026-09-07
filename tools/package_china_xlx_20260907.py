from __future__ import annotations

import hashlib, html, json, re, time, zipfile
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pymupdf
from bs4 import BeautifulSoup
from pypdf import PdfReader

HOME = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
API = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
STOCK_ID, STOCK_CODE = "41122", "01866"
OUT, VERIFY = Path("package_files"), Path("verification")
OUT.mkdir(exist_ok=True); VERIFY.mkdir(exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
client = httpx.Client(http2=True, follow_redirects=True, timeout=httpx.Timeout(600, connect=30), headers=HEADERS)


def records(value):
    if isinstance(value, str): value = json.loads(value)
    if isinstance(value, list): return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for k in ("result", "data", "rows", "items", "records"):
            if k in value:
                try:
                    r = records(value[k])
                    if r: return r
                except Exception: pass
    return []


def search(start, end, lang="zh", t1="-2", t2="-2", title=""):
    p = {"sortDir":"1","sortByOptions":"DateTime","category":"0","market":"SEHK","stockId":STOCK_ID,
         "documentType":"-1","fromDate":start,"toDate":end,"title":title,"searchType":"1",
         "t1code":t1,"t2Gcode":"-2","t2code":t2,"rowRange":"2000","lang":lang}
    client.get(HOME, params={"lang":lang,"market":"SEHK","stockId":STOCK_ID,"category":"0"})
    r = client.get(API, params=p, headers={**HEADERS,"Accept":"application/json,text/plain,*/*","Referer":HOME})
    print("SEARCH", r.status_code, lang, t1, t2, title, len(r.content), flush=True); r.raise_for_status()
    out=[]
    for x in records(r.json()):
        code=re.sub(r"\D","",str(x.get("STOCK_CODE",""))).zfill(5)
        if code and code != STOCK_CODE: continue
        y=dict(x); y["title"]=re.sub(r"\s+"," ",BeautifulSoup(html.unescape(str(x.get("TITLE") or "")),"html.parser").get_text(" ",strip=True)); y["lang"]=lang
        link=str(x.get("FILE_LINK") or x.get("FILEPATH") or x.get("URL") or "").strip(); y["url"]=urljoin("https://www1.hkexnews.hk/",link) if link else None
        out.append(y)
    for y in out: print(y.get("DATE_TIME"), y["title"], y["url"], flush=True)
    return out


def norm(s): return re.sub(r"\s+","",s.replace("〇","零").replace("○","零")).upper()
cn={"0":"零","1":"一","2":"二","3":"三","4":"四","5":"五","6":"六","7":"七","8":"八","9":"九"}
def cny(y): return "".join(cn[c] for c in str(y))

annual = search("20210101","20260907","zh","40000","40100") + search("20210101","20260907","EN","40000","40100")
recent = search("20260401","20260907","zh") + search("20260401","20260907","EN")

chosen=[]
for year in range(2020,2026):
    cand=[]
    for x in annual:
        t=norm(x["title"])
        if (str(year) in t or cny(year) in t) and any(k in t for k in ("年度報告","年報","ANNUALREPORT")) and not any(k in t for k in ("中期","INTERIM","摘要","SUMMARY","ESG","SUSTAINABILITY")):
            score=(100 if x["lang"]=="zh" else 0)+(50 if "年度報告" in t else 0)+(40 if "年報" in t else 0)
            cand.append((score,x))
    if not cand: raise RuntimeError(f"Annual report not found: {year}")
    x=max(cand,key=lambda z:z[0])[1]
    chosen.append({"filename":f"{len(chosen)+1:02d}_中国心连心化肥_{year}年年度报告.pdf","period":f"{year}年年度报告","row":x,"min_pages":100})

q1=[]
for x in recent:
    t=norm(x["title"])
    if ("2026" in t or cny(2026) in t) and any(k in t for k in ("三個月","三个月","THREEMONTHS")) and any(k in t for k in ("業務更新","业务更新","BUSINESSUPDATE")) and not any(k in t for k in ("預告","预告","ESTIMATED","PROFITALERT")):
        q1.append(((100 if x["lang"]=="zh" else 0),x))
if not q1: raise RuntimeError("2026 Q1 business update not found")
x=max(q1,key=lambda z:z[0])[1]
chosen.append({"filename":"07_中国心连心化肥_2026年第一季度业务更新.pdf","period":"2026年第一季度业务更新","row":x,"min_pages":3})

full=[]; results=[]
for x in recent:
    t=norm(x["title"])
    if not ("2026" in t or cny(2026) in t): continue
    if any(k in t for k in ("中期報告","中期报告","INTERIMREPORT")) and not any(k in t for k in ("通知","NOTIFICATION","業績公告","业绩公告","RESULTSANNOUNCEMENT")):
        full.append(((100 if x["lang"]=="zh" else 0),x))
    if any(k in t for k in ("中期業績","中期业绩","INTERIMRESULTS","UNAUDITEDINTERIMRESULTS")) or (any(k in t for k in ("六個月","六个月","SIXMONTHS")) and any(k in t for k in ("業績","业绩","RESULTS"))):
        if not any(k in t for k in ("股息","DIVIDEND","盈利預告","盈利预告","PROFITALERT","BOARDMEETING")):
            results.append(((100 if x["lang"]=="zh" else 0),x))
if full:
    x=max(full,key=lambda z:z[0])[1]; label="2026年中期报告"; minp=20
elif results:
    x=max(results,key=lambda z:z[0])[1]; label="2026年中期业绩公告"; minp=8
else: raise RuntimeError("2026 interim disclosure not found")
chosen.append({"filename":f"08_中国心连心化肥_{label}.pdf","period":label,"row":x,"min_pages":minp})

manifest=[]
for i,item in enumerate(chosen,1):
    dest=OUT/item["filename"]; part=Path(str(dest)+".part"); errors=[]
    for attempt in range(1,4):
        try:
            with client.stream("GET",item["row"]["url"],headers={**HEADERS,"Accept":"application/pdf,*/*","Referer":"https://www1.hkexnews.hk/"}) as r:
                r.raise_for_status()
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1024*1024):
                        if chunk: f.write(chunk)
                final_url=str(r.url)
            if part.read_bytes()[:5] != b"%PDF-": raise RuntimeError("not PDF")
            pages=len(PdfReader(str(part),strict=False).pages)
            if pages < item["min_pages"]: raise RuntimeError(f"pages {pages} < {item['min_pages']}")
            doc=pymupdf.open(part); cover=VERIFY/f"cover_{i:02d}.png"; doc[0].get_pixmap(matrix=pymupdf.Matrix(1.2,1.2),alpha=False).save(cover); doc.close()
            if cover.stat().st_size < 1500: raise RuntimeError("cover render failed")
            part.replace(dest); data=dest.read_bytes()
            manifest.append({"filename":dest.name,"period":item["period"],"document_title":item["row"]["title"],"release_time":item["row"].get("DATE_TIME"),"pages":pages,"size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"source_url":final_url})
            print("OK",dest.name,pages,len(data),flush=True); break
        except Exception as e:
            errors.append(str(e)); part.unlink(missing_ok=True); dest.unlink(missing_ok=True); time.sleep(attempt*2)
    else: raise RuntimeError(f"Failed {dest.name}: {errors}")

note=["中国心连心化肥有限公司（01866.HK）定期报告资料包","","收录2020—2025年完整年度报告、2026年第一季度未经审核业务更新，以及截至2026年9月7日最新中期财务披露。","港交所截至打包时尚未列示完整2026年中期报告，故收入2026年中期业绩公告。","全部PDF来自港交所公开披露页面，未删页或重排。",""]
for x in manifest: note += [f"- {x['filename']}",f"  页数：{x['pages']}",f"  披露时间：{x['release_time']}",f"  来源：{x['source_url']}",f"  SHA-256：{x['sha256']}"]
(OUT/"00_文件清单与来源说明.txt").write_text("\n".join(note)+"\n",encoding="utf-8")
(OUT/"00_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
(OUT/"SHA256SUMS.txt").write_text("".join(f"{x['sha256']}  {x['filename']}\n" for x in manifest),encoding="utf-8")
check=Path("integrity.zip")
with zipfile.ZipFile(check,"w",zipfile.ZIP_STORED,allowZip64=True) as z:
    for p in sorted(OUT.iterdir()): z.write(p,p.name)
with zipfile.ZipFile(check) as z:
    if z.testzip() is not None or len([n for n in z.namelist() if n.lower().endswith('.pdf')]) != 8: raise RuntimeError("ZIP validation failed")
check.unlink()
summary={"pdf_count":len(manifest),"total_pages":sum(x["pages"] for x in manifest),"total_pdf_bytes":sum(x["size_bytes"] for x in manifest),"documents":manifest}
(VERIFY/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
print("PACKAGE READY "+json.dumps(summary,ensure_ascii=False),flush=True)
