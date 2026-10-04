import json
import re
from pathlib import Path

import requests

OUT = Path('output/baolidi_eastmoney_date_probe')
OUT.mkdir(parents=True, exist_ok=True)

API = 'https://reportapi.eastmoney.com/report/list'
DATE_WINDOWS = [
    ('2025-05-27', '2025-05-31'),
    ('2024-05-30', '2024-06-05'),
    ('2020-10-01', '2020-12-31'),
]

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
    'Referer': 'https://data.eastmoney.com/',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
})

all_rows = []
matches = []
for begin, end in DATE_WINDOWS:
    window_rows = []
    for qtype in ('0', '1', '2'):
        for page in range(1, 31):
            params = {
                'industryCode': '*',
                'pageSize': '500',
                'industry': '*',
                'rating': '*',
                'ratingChange': '*',
                'beginTime': begin,
                'endTime': end,
                'pageNo': str(page),
                'fields': '',
                'qType': qtype,
                'orgCode': '',
                'code': '',
                'rcode': '',
                'p': str(page),
                'pageNum': str(page),
                'pageNumber': str(page),
            }
            response = session.get(API, params=params, timeout=90, allow_redirects=True)
            print('API', begin, end, qtype, page, response.status_code, len(response.content), response.url)
            response.raise_for_status()
            text = response.text.strip()
            try:
                obj = response.json()
            except Exception:
                match = re.search(r'^[^(]+\((.*)\)\s*;?$', text, re.S)
                if not match:
                    raise
                obj = json.loads(match.group(1))
            rows = obj.get('data') or obj.get('Data') or obj.get('result') or []
            if isinstance(rows, dict):
                rows = rows.get('data') or rows.get('list') or rows.get('records') or []
            print('ROWS', len(rows), 'HITS', obj.get('hits'), 'TOTALPAGE', obj.get('TotalPage') or obj.get('totalPage'))
            if not rows:
                break
            for row in rows:
                row['_query_window'] = [begin, end]
                row['_query_qtype'] = qtype
                blob = json.dumps(row, ensure_ascii=False)
                if '宝丽迪' in blob or '300905' in blob or 'COFS' in blob.upper() or 'COFs' in blob:
                    matches.append(row)
                    print('MATCH', json.dumps(row, ensure_ascii=False))
            window_rows.extend(rows)
            total_page = obj.get('TotalPage') or obj.get('totalPage') or 1
            try:
                if page >= int(total_page):
                    break
            except Exception:
                pass
        if window_rows:
            break
    all_rows.extend(window_rows)

# De-duplicate matches.
dedup = {}
for row in matches:
    info = str(row.get('infoCode') or row.get('info_code') or row.get('reportId') or row.get('id') or '')
    key = info or json.dumps(row, ensure_ascii=False, sort_keys=True)
    dedup[key] = row
matches = list(dedup.values())

(OUT / 'matches.json').write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT / 'summary.json').write_text(json.dumps({
    'match_count': len(matches),
    'matches': matches,
    'total_rows_examined': len(all_rows),
}, ensure_ascii=False, indent=2), encoding='utf-8')
print('FINAL_MATCH_COUNT', len(matches), 'TOTAL_ROWS', len(all_rows))
