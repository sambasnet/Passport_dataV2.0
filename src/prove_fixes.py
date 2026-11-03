#!/usr/bin/env python3
"""Prove fix 1 (psm 6 -> psm 3 for the classification pass) and fix 2
(the removed "assume the first image is the front" fallback).

Usage:
    python3 prove_fixes.py --pdf <passport-pdf>
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pymupdf

import passport_extract as pe

TESS_BIN = "/home/hermes/.hermes/cache/scratch/tstest/prefix/usr/bin/tesseract"
TESSDATA = "/home/hermes/.hermes/cache/scratch/tstest/tessdata"


def scores(text):
    f = sum(1 for kw in pe.FRONT_KEYWORDS if kw in text)
    b = sum(1 for kw in pe.BACK_KEYWORDS if kw in text)
    return f, b


def verdict(f, b):
    if f >= 2 and f > b:
        return "front"
    if b >= 2 and b > f:
        return "back"
    return "NOT A PASSPORT -> no front found"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, help="the passport PDF to probe")
    args = ap.parse_args()

    tess = pe.Tesseract(TESS_BIN, TESSDATA)
    print(f"tesseract: {tess.binary}")
    print(f"tessdata : {tess.tessdata}")
    print()

    # ------------------------------------------------------------------
    # FIX 1: the classification pass psm
    # ------------------------------------------------------------------
    print("=== FIX 1: classification keyword scores by psm ===")
    print("The upstream code OCR'd the EMBEDDED IMAGE, so that is what is")
    print("measured here — the same artifact the upstream classifier saw.")
    print()
    with pymupdf.open(args.pdf) as doc:
        for pno in range(doc.page_count):
            page = doc.load_page(pno)
            for img in page.get_images(full=True):
                xref, w, h = img[0], img[2], img[3]
                if w * h < 100_000:
                    continue
                pix = pe.load_pixmap(doc, xref)
                if pix is None:
                    continue
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t:
                    tp = t.name
                pix.save(tp)
                rows = []
                for psm in (3, 6):
                    text = tess.run(tp, "eng", psm)
                    f, b = scores(text)
                    rows.append((psm, f, b, verdict(f, b)))
                if any(r[1] or r[2] for r in rows):
                    print(f"page {pno} xref {xref} ({w}x{h}):")
                    print(f"  {'psm':>4}  {'front':>5}  {'back':>5}   verdict")
                    for psm, f, b, v in rows:
                        print(f"  {psm:>4}  {f:>5}  {b:>5}   {v}")
                    print()
                Path(tp).unlink()

    # ------------------------------------------------------------------
    # FIX 2: no-guess behaviour when classification yields nothing
    # ------------------------------------------------------------------
    print("=== FIX 2: no-guess behaviour when classification yields nothing ===")
    outdir = Path("prove_fixes_probe")
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in outdir.glob("*"):
        stale.unlink()

    with pymupdf.open(args.pdf) as doc:
        cands = pe.ordered_candidate_xrefs(doc)
    print(f"candidate images in the PDF: {len(cands)}")

    print()
    print("upstream (broken) behaviour with tesseract unavailable:")
    print(f"  found.setdefault('front', candidates[0])  -> xref {cands[0] if cands else 'n/a'}")
    print(f"  back = candidates[1]                      -> xref {cands[1] if len(cands) > 1 else 'n/a'}")
    print("  and it would write <name>_front.jpg / <name>_back.jpg with NO warning.")

    # Point TESSDATA_PREFIX at a bogus dir so the OCR returns nothing.
    tess_bogus = pe.Tesseract(TESS_BIN, "/nonexistent/tessdata")
    s1 = pe.stage1(Path(args.pdf), outdir, tess_bogus)
    print()
    print("this pipeline (fail-closed) behaviour with tesseract unavailable:")
    print(f"  status          : {s1['status']}")
    print(f"  front_image     : {s1['front_image']}")
    print(f"  back_image      : {s1['back_image']}")
    print(f"  reason          : {s1['detail'].get('reason')}")
    written = sorted(outdir.glob("*"))
    print(f"  files written   : {[w.name for w in written]}")
    if not written:
        print("  -> no _front.jpg / _back.jpg was written. The file is marked failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
