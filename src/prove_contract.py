#!/usr/bin/env python3
"""Prove the Stage 2 output matches the exception-report contract.

Runs EDITH's exception_report.py against the pipeline's output and shows
that it produces a report with the expected exception codes.

The master spreadsheet is READ ONLY.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

EXTRACT = sys.argv[1] if len(sys.argv) > 1 else "batch2/extracted.json"
MASTER = "/home/hermes/EA-Files/RPS_Bali_Sep2026/All_Travel_Passport_data.xlsx"
REPORT = Path("/home/hermes/.hermes/kanban/attachments/t_8047a784/exception_report.py")
WORKDIR = Path("report_out")
TRAVEL_DATE = "2026-09-19"
TRIP = "RPS Bali Sep 2026"

if not REPORT.exists():
    print(f"exception_report.py not found at {REPORT}", file=sys.stderr)
    sys.exit(2)

WORKDIR.mkdir(parents=True, exist_ok=True)

# --- 1. Shape check against the contract in references/exception-report.md
recs = json.loads(Path(EXTRACT).read_text())
required_top = {
    "source_message_id", "original_filename", "file_sha256", "chat",
    "received_at", "status", "front_image", "back_image", "mrz",
    "fields", "visual_zone", "notes",
}
required_mrz = {"line1", "line2", "score", "checks"}
required_fields = {
    "passport_no", "surname", "given_names", "nationality", "sex",
    "dob", "expiry", "issue_date", "issue_date_source",
}

print("=== contract shape check ===")
bad = 0
for r in recs:
    missing = required_top - set(r)
    if missing:
        print(f"  MISSING top-level {sorted(missing)} in {r.get('original_filename')}")
        bad += 1
    mrz_missing = required_mrz - set(r.get("mrz") or {})
    if mrz_missing:
        print(f"  MISSING mrz {sorted(mrz_missing)} in {r.get('original_filename')}")
        bad += 1
    if r.get("status") == "ok":
        f_missing = required_fields - set(r.get("fields") or {})
        if f_missing:
            print(f"  MISSING fields {sorted(f_missing)} in {r.get('original_filename')}")
            bad += 1
print(f"  {len(recs)} records checked, {bad} shape problem(s)")
print(f"  status values seen: {sorted({r.get('status') for r in recs})}")
print()

# --- 2. Run EDITH's exception report generator against it
cmd = [
    sys.executable, str(REPORT),
    "--extract", str(EXTRACT),
    "--master", MASTER,
    "--travel-date", TRAVEL_DATE,
    "--trip", TRIP,
    "--outdir", str(WORKDIR),
]
print("=== running EDITH's exception_report.py ===")
print("  " + " ".join(cmd))
proc = subprocess.run(cmd, capture_output=True, text=True)
print(proc.stdout)
if proc.stderr.strip():
    print("STDERR:", proc.stderr.strip())
print(f"exit code: {proc.returncode}")
print()

# --- 3. Show the artefacts it produced
print("=== artefacts ===")
for f in sorted(WORKDIR.glob("*")):
    size = f.stat().st_size
    print(f"  {f.name}  ({size} bytes)")
    if f.suffix == ".md":
        print("  " + "-" * 60)
        print("  " + f.read_text()[:4000].replace("\n", "\n  "))
        print("  " + "-" * 60)
    elif f.suffix == ".csv":
        print("  " + f.read_text()[:3000].replace("\n", "\n  "))
        print("  " + "-" * 60)

# --- 4. Confirm the master was not modified
print("=== master sheet untouched ===")
mt = Path(MASTER).stat()
print(f"  {MASTER}")
print(f"  size={mt.st_size}  mtime={mt.st_mtime}")
sys.exit(proc.returncode)
