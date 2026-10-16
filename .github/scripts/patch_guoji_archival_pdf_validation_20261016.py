from pathlib import Path

path = Path('.github/scripts/guoji_zhongzhuang_package_20261016.py')
text = path.read_text(encoding='utf-8')

old = '''    subprocess.run(
        ["qpdf", "--check", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
'''
new = '''    qcheck = subprocess.run(
        ["qpdf", "--check", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    qpdf_status = qcheck.returncode
    qpdf_output = qcheck.stdout.strip()
    if qpdf_status not in (0, 3):
        raise RuntimeError(f"qpdf structural error for {path}: {qpdf_status}\\n{qpdf_output}")
'''

old_return = '''    return {"pages": pages, "bytes": path.stat().st_size, "sha256": sha256(path), "identity_hits": identity_hits}
'''
new_return = '''    return {
        "pages": pages,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "identity_hits": identity_hits,
        "qpdf_status": qpdf_status,
        "qpdf_output": qpdf_output[:2000],
    }
'''

if old not in text:
    raise SystemExit('qpdf validation block not found')
if old_return not in text:
    raise SystemExit('verify return block not found')

text = text.replace(old, new).replace(old_return, new_return)
path.write_text(text, encoding='utf-8')
print('Patched archival PDF validation.')
