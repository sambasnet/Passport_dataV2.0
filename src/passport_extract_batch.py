#!/usr/bin/env python3
"""Parallel batch driver for the passport extraction pipeline.

The OCR work is per-document and independent, so the batch fans out over
processes. Each worker gets its own output subdirectory and the driver
merges the per-document JSON records into one Stage 2 contract file.

Usage:
    python3 passport_extract_batch.py --input <pdf-dir> --outdir <work-dir> \
        --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRACTOR = HERE / "passport_extract.py"

sys.path.insert(0, str(HERE))
from passport_extract import is_pdf, write_csv  # noqa: E402

TESS_BIN = "/home/hermes/.hermes/cache/scratch/tstest/prefix/usr/bin/tesseract"
TESSDATA = "/home/hermes/.hermes/cache/scratch/tstest/tessdata"


def run_one(args):
    in_dir, outdir, tesseract, tessdata = args
    in_dir = Path(in_dir)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(EXTRACTOR),
        "--input", str(in_dir),
        "--outdir", str(outdir),
        "--extract-name", f"{in_dir.name}.json",
    ]
    if tesseract:
        cmd += ["--tesseract", tesseract]
    if tessdata:
        cmd += ["--tessdata", tessdata]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return in_dir.name, proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--tesseract", default=TESS_BIN)
    ap.add_argument("--tessdata", default=TESSDATA)
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    # Give each worker its own input dir holding one PDF, so the
    # extractor processes exactly one document.
    stage = Path(tempfile.mkdtemp(prefix="passport_batch_", dir=str(out_dir)))
    pdfs = sorted(p for p in in_dir.iterdir() if p.is_file() and is_pdf(p))
    skipped = sorted(
        p for p in in_dir.iterdir()
        if p.is_file() and not is_pdf(p)
    )
    if skipped:
        print(f"skipping {len(skipped)} non-PDF file(s):")
        for p in skipped:
            print(f"  {p.name}")

    jobs = []
    for pdf in pdfs:
        one = stage / pdf.stem
        one.mkdir(parents=True, exist_ok=True)
        link = one / pdf.name
        if not link.exists():
            link.symlink_to(pdf.resolve())
        jobs.append((one, str(out_dir / "_work" / pdf.stem), args.tesseract, args.tessdata))

    records = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, j): j[0] for j in jobs}
        for fut in as_completed(futures):
            name, code, out, err = fut.result()
            for line in out.splitlines():
                if line.startswith("["):
                    print(line, flush=True)
            if code != 0:
                print(f"  !! {name} exited {code}: {err.strip()[:200]}", flush=True)
            # Merge this worker's record.
            stem = Path(futures[fut]).name
            jf = out_dir / "_work" / stem / f"{stem}.json"
            if jf.exists():
                records.extend(json.loads(jf.read_text()))
    # Order the records to match the input order.
    order = {p.name: i for i, p in enumerate(pdfs)}
    records.sort(key=lambda r: order.get(r.get("original_filename", ""), 999))

    extract_path = out_dir / "extracted.json"
    with open(extract_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)

    # Merge the per-worker image directories into one.
    import shutil
    for sub in (out_dir / "_work").glob("*/images/*"):
        dest = img_dir / sub.name
        if dest.exists():
            continue
        shutil.copy2(sub, dest)

    # Rebuild the CSV from the merged records.
    csv_path = write_csv(records, out_dir)

    ok = sum(1 for r in records if r["status"] == "ok")
    unread = sum(1 for r in records if r["status"] == "unread")
    no_front = sum(1 for r in records if r["status"] == "no_front")
    print()
    print(f"documents : {len(records)}")
    print(f"clean     : {ok}/{len(records)}")
    print(f"unread    : {unread}")
    print(f"no_front  : {no_front}")
    print(f"extract   : {extract_path}")
    print(f"csv       : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
