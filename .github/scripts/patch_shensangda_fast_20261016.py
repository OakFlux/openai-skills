from pathlib import Path

p = Path('.github/scripts/shensangda_package_20261016.py')
t = p.read_text(encoding='utf-8')

old_fn = '''def eastmoney_urls(art: str) -> list[str]:
    return [f"https://pdf.dfcfw.com/pdf/H{x}_{art}_1.pdf" for x in (3, 2, 1, 4)]
'''
new_fn = '''def eastmoney_urls(art: str) -> list[str]:
    versions = (22, 3, 2, 1, 4) if art.startswith("AN201202") else (3, 2, 1, 4)
    return [f"https://pdf.dfcfw.com/pdf/H{x}_{art}_1.pdf" for x in versions]
'''
if old_fn not in t:
    raise SystemExit('eastmoney_urls patch target not found')
t = t.replace(old_fn, new_fn, 1)

old_spec = '"art_code": art_code(prospectus_row), "folder": PROSPECTUS_DIR,'
new_spec = '"art_code": art_code(prospectus_row), "urls": ["https://pdf.dfcfw.com/pdf/H22_AN201202240003353422_1.pdf?1671023552000.pdf"], "folder": PROSPECTUS_DIR,'
if old_spec not in t:
    raise SystemExit('prospectus patch target not found')
t = t.replace(old_spec, new_spec, 1)

old_block = '''    art = str(spec.get("art_code") or "")
    if art:
        urls.extend(eastmoney_urls(art))
    last: dict[str, Any] = {}
    for url in urls:
        for attempt in range(5):
'''
new_block = '''    art = str(spec.get("art_code") or "")
    if art:
        try:
            meta_url = f"https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={art}&client_source=web&page_index=1"
            meta_resp = s.get(meta_url, timeout=(20, 90), headers={"User-Agent": s.headers["User-Agent"], "Referer": f"https://data.eastmoney.com/notices/detail/{CODE}/{art}.html"})
            if meta_resp.status_code == 200:
                meta = (meta_resp.json().get("data") or {})
                for item in (meta.get("attach_list") or []):
                    if item.get("attach_url"):
                        urls.append(str(item["attach_url"]))
                for key in ("attach_url_web", "attach_url"):
                    if meta.get(key):
                        urls.append(str(meta[key]))
        except Exception:
            pass
        urls.extend(eastmoney_urls(art))
    urls = list(dict.fromkeys(urls))
    last: dict[str, Any] = {}
    for url in urls:
        for attempt in range(1):
'''
if old_block not in t:
    raise SystemExit('fetch_pdf patch target not found')
t = t.replace(old_block, new_block, 1)

p.write_text(t, encoding='utf-8')
print('Patched exact attachment discovery and fast fallback routing.')
