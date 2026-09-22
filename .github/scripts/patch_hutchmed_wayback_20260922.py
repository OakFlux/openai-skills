from pathlib import Path

path = Path('.github/scripts/hutchmed_package_20260922.py')
text = path.read_text(encoding='utf-8')

if 'from io import BytesIO' not in text:
    text = text.replace('import hashlib\n', 'import hashlib\nfrom io import BytesIO\n', 1)

marker = '\n\ndef pdf_info(path: Path) -> tuple[int, str]:\n'
helper = r'''


def fetch_wayback(url: str) -> bytes:
    """Download archived binaries while ignoring incorrect Content-Length headers."""
    temp_path = TEMP / ("wayback_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20] + ".bin")
    cmd = [
        "curl",
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--retry", "4",
        "--retry-all-errors",
        "--connect-timeout", "30",
        "--max-time", "300",
        "--http1.1",
        "--ignore-content-length",
        "--output", str(temp_path),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed ({proc.returncode}): {proc.stderr[-1200:]}")
    data = temp_path.read_bytes()
    if len(data) < 100:
        raise RuntimeError(f"archived response too small: {len(data)} bytes")
    return data
'''

if 'def fetch_wayback(' not in text:
    if marker not in text:
        raise SystemExit('Insertion marker not found')
    text = text.replace(marker, helper + marker, 1)

old = '''        for candidate in (current, archived):
            try:
                candidate_data = fetch(candidate)
'''
new = '''        for candidate in (archived,):
            try:
                candidate_data = fetch_wayback(candidate)
'''
if old not in text:
    raise SystemExit('Legacy loop marker not found')
text = text.replace(old, new, 1)

old_reader = '                candidate_reader = PdfReader(candidate_data)\n'
new_reader = '                candidate_reader = PdfReader(BytesIO(candidate_data))\n'
if old_reader not in text:
    raise SystemExit('Archived PDF reader marker not found')
text = text.replace(old_reader, new_reader, 1)

path.write_text(text, encoding='utf-8')
print('Patched legacy Wayback download and in-memory PDF parsing')
