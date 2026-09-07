#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

BASE_PATH = Path(__file__).with_name("package_sinopec_oilfield_service_reports_20260907.py")
spec = importlib.util.spec_from_file_location("sinopec_oilfield_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load base packager: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

_original_record_score = base.record_score


def record_score(record):
    score, timestamp = _original_record_score(record)
    # SSE metadata is authoritative, but its document CDN may return an anti-bot
    # interstitial to automated clients. CNINFO carries the same legally filed
    # document and currently serves the PDF bytes directly.
    if str(record.get("source", "")).startswith("巨潮资讯网"):
        score += 500
    return score, timestamp


def command_ok(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    output = (result.stdout or "") + (result.stderr or "")
    executable = Path(command[0]).name
    # qpdf exit code 3 means warnings without structural errors.
    ok = result.returncode in (0, 3) if executable == "qpdf" else result.returncode == 0
    return ok, output[-4000:]


base.record_score = record_score
base.command_ok = command_ok

if __name__ == "__main__":
    base.main()
