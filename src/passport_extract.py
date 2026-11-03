#!/usr/bin/env python3
"""Passport extraction pipeline — Linux-native port of PDF-JPG_V2.0 +
Passport_dataV2.0.

Takes a folder of traveller PDFs and emits:

  1. per-file pass/fail verdicts (JSON, the Stage 2 contract), and
  2. a clean-data CSV (the format Passport_dataV2.0 produced), and
  3. the extracted front/back page images.

The pipeline is FAIL-CLOSED: a document that cannot be validated produces
a verdict with no fields, never a guess.

Six fixes over the upstream Windows scripts
--------------------------------------------

1. The keyword classification pass uses `--psm 3`, not `--psm 6`.
   Measured on a real passport page: psm 6 scores front=0 back=0,
   psm 3 scores front=6 back=8. With psm 6 the page is classified as
   "not a passport", which (before fix 2) silently became a WRONG file.
   The MRZ pass is a different pass and still uses psm 6/11.

2. The "assume the first image is the front" fallback is REMOVED.
   When classification yields nothing, no image is written and the file
   is marked failed. A confidently wrong passport number is worse than
   a missing row: a missing row is visible, a wrong row is not.

3. Images are written with the codec the PDF actually stores.
   `extract_image()` reports the codec; the file extension follows it.

4. The MRZ parse is joint ICAO 9303 validation, not `passporteye`'s
   `valid_score >= 60`. All four ICAO check digits are computed: the
   passport number, the DOB, the expiry, and the composite digit. The
   composite covers positions 1-10, 14-20 and 22-43 of the LOWER line
   and lives at position 44 (ICAO Doc 9303 Part 4, section 4.2.4).

5. The MRZ is cropped from the BOTTOM of the page, where it actually is.
   The upstream sweep cropped 0.60-0.80 of the page height; on the real
   documents the MRZ band starts at ~0.85, so the sweep was reading the
   visual zone and picking up fragments of the printed fields instead.

6. The MRZ repair is JOINT over both lines, and the result is confirmed
   against the printed passport number in the visual zone.
   On the ground-truth document the MRZ's leading character is clipped off
   the scan, so both lines come back one character short. Insertion
   repair then yields 37 candidate pairs that all satisfy the three
   per-field check digits — including wrong passport numbers such as
   `W4000001` and `4000001A`. The check digits CANNOT tell them apart.
   The printed number in the visual zone is an independent read of the
   same page, and it does. So the visual zone is used both to SELECT the
   candidate and to CROSS-CHECK the result. If the visual zone and the
   MRZ disagree, nothing is published.

Usage:
    python3 passport_extract.py --input <pdf-dir> --outdir <work-dir>

Exit codes: 0 = batch ran (check the verdict counts), 1 = bad usage,
2 = a fatal environment problem (no Tesseract, no PDFs).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mrz_locate  # noqa: E402 - needs the path set first

# --------------------------------------------------------------------------
# ICAO 9303 TD3 MRZ constants and check-digit arithmetic
# --------------------------------------------------------------------------

W10 = [7, 3, 1, 7, 3, 1, 7, 3, 1, 7]
W7 = [7, 3, 1, 7, 3, 1]
# ICAO 9303 weights cycle with PERIOD 3. W10 and W7 above are correct
# only for fields of exactly 10 and 7 characters.
W3 = [7, 3, 1]
ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"

# Fix 5: the MRZ band is NOT at a fixed fraction of the image.
# A passport scanned flat fills the frame, so the MRZ sits at ~0.85-1.0 of
# the height. But a passport PHOTOGRAPHED ON A BED occupies only the lower
# half of the frame, and the MRZ sits at ~0.62-0.68. Measured across the
# live corpus the MRZ band moves by more than 20% of the image height.
#
# So the sweep walks overlapping windows up the page and keeps any window
# that yields an MRZ-looking candidate. The window is tall (28% of the
# image) so both MRZ lines are always inside it together.
#
# The sweep starts at the BOTTOM, because a flat scan is the common case
# and the early exit then costs only a couple of windows.
MRZ_WINDOW_HEIGHT = 0.28
MRZ_BAND_STARTS = tuple(
    sorted((x / 100 for x in range(40, 92, 4)), reverse=True)
)
MRZ_SCALES = (3, 2)
MRZ_PSMS = (6, 11)
MRZ_INVERTS = (False, True)

# The acceptance threshold.
#
# With the composite check digit computed correctly (ICAO Doc 9303 Part 4
# section 4.2.4: positions 1-10, 14-20 and 22-43 of the LOWER line, the
# digit at position 44) a real TD3 MRZ scores 4/4, and a random 44-char
# string scores 4/4 about one time in ten thousand. So 4/4 is the bar.
#
# 3/4 is NOT accepted: a 3/4 pair means either the optional-data field or
# the composite is wrong, and publishing a passport number that may be
# wrong is worse than publishing nothing. A 3/4 pair is reported as
# unread and routed to a human.
MIN_MRZ_SCORE = 4
VZ_BAND_STARTS = tuple(x / 100 for x in range(25, 80, 5))
VZ_WINDOW_HEIGHT = 0.30
VZ_SCALES = (2,)
VZ_PSMS = (6, 11)
VZ_INVERTS = (False, True)

# Classification pass (fix 1).
CLASSIFY_PSM = 3

MIN_IMAGE_PIXELS = 100_000

FRONT_KEYWORDS = (
    "REPUBLIC OF INDIA",
    "GIVEN NAME",
    "SURNAME",
    "DATE OF BIRTH",
    "PLACE OF BIRTH",
    "DATE OF EXPIRY",
    "NATIONALITY",
    "PASSPORT NO",
    "P<IND",
    "<<<",
)

# A page is only a passport data page if it carries the country's header
# or the passport-number label. A CV or a form can easily contain
# "DATE OF BIRTH" and "NATIONALITY" — the same words a passport uses —
# so the keyword score alone is not enough to call a page a passport.
FRONT_REQUIRED = ("REPUBLIC OF INDIA", "PASSPORT NO", "P<IND", "<<<")

BACK_KEYWORDS = (
    "NAME OF FATHER",
    "LEGAL GUARDIAN",
    "NAME OF MOTHER",
    "NAME OF SPOUSE",
    "ADDRESS",
    "OLD PASSPORT",
    "FILE NO",
    "PIN:",
)

EXCLUDE_KEYWORDS = (
    "INCOME TAX DEPARTMENT",
    "INCOMETAX DEPARTMENT",
    "PERMANENT ACCOUNT NUMBER",
)

CSV_FIELDS = [
    "File",
    "Passport Number",
    "Given Name",
    "Surname",
    "Nationality",
    "Gender",
    "Date of Birth",
    "Issue Date",
    "Expiration Date",
]


def check_digit(chars: str, weights: list[int]) -> str:
    """ICAO 9303 check digit: (sum of value * weight) mod 10.

    The weights CYCLE: ICAO Doc 9303 Part 4 uses the repeating sequence
    7, 3, 1. So the weight for character i is `weights[i % len(weights)]`.

    Note `zip(chars, weights)` is WRONG here: zip stops at the shorter
    sequence, so a 39-character composite input paired with a 10-element
    weight list would score only the first 10 characters.
    """
    total = 0
    n = len(weights)
    for i, ch in enumerate(chars):
        if ch.isdigit():
            val = int(ch)
        elif ch.isalpha():
            val = ord(ch) - 55
        else:
            val = 0
        total += val * weights[i % n]
    return str(total % 10)


def line2_field_checks(line2: str) -> dict[str, bool]:
    """The three check digits that live entirely within line 2.

    passport_no (0-9), dob (13-19), expiry (21-27).
    """
    if len(line2) != 44:
        return {"passport_no": False, "dob": False, "expiry": False}
    return {
        "passport_no": check_digit(line2[0:9], W10) == line2[9],
        "dob": check_digit(line2[13:19], W7) == line2[19],
        "expiry": check_digit(line2[21:27], W7) == line2[27],
    }


def composite_input(line2: str) -> str:
    """The characters the composite check digit is computed over.

    ICAO Doc 9303 Part 4, section 4.2.4:

        "Composite check digit | 1-10, 14-20, 22-43 |
         Note.— Positions 11-13 and 21 are excluded when calculating the
         composite check digit. | Check digit position 44"

    In 0-indexed terms that is line 2 positions 0-9, 13-19 and 21-42 —
    the LOWER LINE ONLY. Line 1 does not take part.
    """
    return line2[0:10] + line2[13:20] + line2[21:43]


def composite_digit(line2: str) -> str:
    """The composite check digit of a line 2.

    It lives at position 44 of line 2 (index 43). Position 43 (index 42)
    is the OPTIONAL-DATA check digit, which is a different digit.
    """
    return check_digit(composite_input(line2), W3)


def score_pair(line1: str, line2: str) -> tuple[int, list[str]]:
    """Score an MRZ pair against all four ICAO check digits.

    Returns (score 0-4, per-check detail list).

    The four checks are the passport number, the date of birth, the
    expiry, and the composite digit. The composite is computed over
    positions 1-10, 14-20 and 22-43 of the LOWER line, and lives at
    position 44 of that line. It is the strongest of the four because it
    also covers the optional-data field, so a single wrong character
    anywhere in the data region breaks it.

    Line 2 is never scored in isolation from the pairing requirement:
    a 44-character string of digits can satisfy the per-field check
    digits by chance, so line 1 must also be a plausible TD3 header.
    """
    if len(line1) != 44 or len(line2) != 44:
        return 0, ["bad_length"]
    if not line1.startswith("P<"):
        return 0, ["bad_magic"]

    results = [
        "passport_no"
        if check_digit(line2[0:9], W10) == line2[9]
        else "passport_no_BAD",
        "dob"
        if check_digit(line2[13:19], W7) == line2[19]
        else "dob_BAD",
        "expiry"
        if check_digit(line2[21:27], W7) == line2[27]
        else "expiry_BAD",
    ]
    results.append(
        "composite"
        if composite_digit(line2) == line2[43]
        else "composite_BAD"
    )
    return sum(1 for r in results if not r.endswith("_BAD")), results


def normalise(text: str) -> str:
    """Strip everything outside the MRZ character set."""
    return re.sub(r"[^A-Z0-9<]", "", text.upper())


# --------------------------------------------------------------------------
# Tesseract plumbing
# --------------------------------------------------------------------------

DEFAULT_TESSDATA = "/home/hermes/.hermes/cache/scratch/tstest/tessdata"
DEFAULT_TESS_PREFIX = "/home/hermes/.hermes/cache/scratch/tstest/prefix"


class Tesseract:
    """Locates and drives a Tesseract binary.

    The rootless install used on this box needs LD_LIBRARY_PATH set or the
    binary cannot load libtesseract.so.5 / liblept.so.5.
    """

    def __init__(self, binary: str | None = None, tessdata: str | None = None):
        self.binary = binary or shutil.which("tesseract")
        self.tessdata = (
            tessdata or os.environ.get("TESSDATA_PREFIX") or DEFAULT_TESSDATA
        )
        self.env = dict(os.environ)
        if self.tessdata:
            self.env["TESSDATA_PREFIX"] = self.tessdata
        # Rootless prefix: put its shared libraries ahead of the system ones.
        libdir = os.path.join(DEFAULT_TESS_PREFIX, "usr/lib/x86_64-linux-gnu")
        if os.path.isdir(libdir):
            existing = self.env.get("LD_LIBRARY_PATH", "")
            self.env["LD_LIBRARY_PATH"] = (
                f"{libdir}:{existing}" if existing else libdir
            )

    @property
    def available(self) -> bool:
        return bool(self.binary) and os.path.exists(self.binary)

    def run(self, image_path: str, lang: str, psm: int) -> str:
        """Run Tesseract and return uppercase text. Never raises."""
        if not self.available:
            return ""
        try:
            proc = subprocess.run(
                [self.binary, image_path, "stdout", "-l", lang, "--psm", str(psm)],
                capture_output=True,
                text=True,
                env=self.env,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return (proc.stdout or "").upper()


# --------------------------------------------------------------------------
# Stage 1 — find the passport front/back pages in a PDF
# --------------------------------------------------------------------------


def rotate_pixmap(pix: pymupdf.Pixmap, k: int) -> pymupdf.Pixmap:
    """Rotate a PyMuPDF Pixmap by 90-degree increments using NumPy."""
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    rot = np.rot90(arr, k=k)
    return pymupdf.Pixmap(
        pix.colorspace, rot.shape[1], rot.shape[0], rot.tobytes(), False
    )


def load_pixmap(pdf_document, xref: int) -> pymupdf.Pixmap | None:
    """Load an embedded image as an RGB pixmap, shrunk to a sane size."""
    try:
        pix = pymupdf.Pixmap(pdf_document, xref)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        if pix.n > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        while max(pix.width, pix.height) > 1600:
            pix.shrink(1)
        return pix
    except (RuntimeError, ValueError):
        return None


def classify_pixmap(pix: pymupdf.Pixmap, tess: Tesseract) -> tuple[str | None, dict]:
    """Classify an image as 'front', 'back' or None.

    Checks upright orientation first, then 90-degree rotations. Uses psm 3
    (fix 1) — the default layout analysis, which finds the keyword blocks
    on a passport page. Returns (side, score_detail).
    """
    detail = {"front_score": 0, "back_score": 0, "psm": CLASSIFY_PSM}
    for rot_k in (0, -1, 1):
        test_pix = pix if rot_k == 0 else rotate_pixmap(pix, rot_k)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            test_pix.save(tmp_path)
            text = tess.run(tmp_path, "eng", CLASSIFY_PSM)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            return None, detail

        front_score = sum(1 for kw in FRONT_KEYWORDS if kw in text)
        back_score = sum(1 for kw in BACK_KEYWORDS if kw in text)
        detail["front_score"] = max(detail["front_score"], front_score)
        detail["back_score"] = max(detail["back_score"], back_score)

        if front_score >= 2 and front_score > back_score and any(
            kw in text for kw in FRONT_REQUIRED
        ):
            detail["rotation"] = rot_k
            return "front", detail
        if back_score >= 2 and back_score > front_score:
            detail["rotation"] = rot_k
            return "back", detail
    return None, detail


def classify_page(page: pymupdf.Page, tess: Tesseract) -> tuple[str | None, dict]:
    """Classify a PDF PAGE as 'front', 'back' or None.

    The page is rendered and OCR'd, rather than the embedded image. The
    render is what the PDF actually displays — it picks up vector
    overlays, and it scales the page to a resolution Tesseract reads
    well. Measured on a real passport PDF whose embedded image scored
    front=0/back=0, the page render scored front=3/back=0.
    """
    detail = {"front_score": 0, "back_score": 0, "psm": CLASSIFY_PSM}
    try:
        pix = page.get_pixmap(dpi=200)
    except (RuntimeError, ValueError):
        return None, detail
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pix.save(tmp_path)
        text = tess.run(tmp_path, "eng", CLASSIFY_PSM)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return None, detail

    front_score = sum(1 for kw in FRONT_KEYWORDS if kw in text)
    back_score = sum(1 for kw in BACK_KEYWORDS if kw in text)
    detail["front_score"] = front_score
    detail["back_score"] = back_score

    # A passport data page must carry one of the passport-specific
    # markers. Without this, a CV that lists "Date of Birth" and
    # "Nationality" scores like a passport and is then reported as an
    # unread passport instead of as "no passport here".
    has_marker = any(kw in text for kw in FRONT_REQUIRED)
    detail["has_marker"] = has_marker

    if front_score >= 2 and front_score > back_score and has_marker:
        return "front", detail
    if back_score >= 2 and back_score > front_score:
        return "back", detail
    return None, detail


def ordered_candidate_xrefs(pdf_document) -> list[int]:
    """Candidate image xrefs, page 1 and last page first, then the middle."""
    page_count = pdf_document.page_count
    if page_count == 0:
        return []

    page_order = [0]
    if page_count > 1:
        page_order.append(page_count - 1)
    page_order.extend(range(1, page_count - 1))

    candidates: list[int] = []
    seen: set[int] = set()
    for page_num in page_order:
        page = pdf_document.load_page(page_num)
        for img in page.get_images(full=True):
            if img[2] * img[3] < MIN_IMAGE_PIXELS:
                continue
            xref = img[0]
            if xref not in seen:
                seen.add(xref)
                candidates.append(xref)
    return candidates


def save_xref(pdf_document, xref: int, output_path: Path) -> bool:
    """Write an embedded image using the codec the PDF actually stores.

    Fix 3: `extract_image()` reports the codec. On Sam's own visa PDFs the
    stored codec is `png` for most xrefs — writing those bytes to a `.jpg`
    name produced files that were not JPEGs at all.
    """
    try:
        info = pdf_document.extract_image(xref)
        image_bytes = info["image"]
        codec = (info.get("ext") or "png").lower().lstrip(".")
        if codec in ("jpg", "jpeg"):
            codec = "jpg"
        elif codec in ("tif", "tiff"):
            codec = "tif"
        elif codec in ("jpx", "jpx2", "jp2"):
            codec = "jp2"
        else:
            codec = codec or "png"
        out = output_path.with_suffix(f".{codec}")
        with open(out, "wb") as fh:
            fh.write(image_bytes)
        return True
    except (OSError, RuntimeError, ValueError, KeyError):
        return False


def stage1(pdf_path: Path, image_dir: Path, tess: Tesseract) -> dict:
    """Locate and save the passport front/back images.

    Two-stage:
      - CLASSIFY on the rendered page. The render is what the PDF
        displays, and Tesseract reads it reliably. Measured: a real
        passport PDF whose embedded image scored front=0/back=0 scored
        front=3/back=0 as a page render.
      - SAVE the embedded image for that page, at the PDF's own
        resolution, because the MRZ pass needs every pixel it can get.
        If the page has no usable embedded image, the render is saved
        instead.

    Fail-closed (fix 2): if the classification pass cannot tell which
    page is the front, nothing is written. The upstream code guessed
    `candidates[0]` and `candidates[1]` and wrote `_front.jpg` /
    `_back.jpg` regardless — producing a confidently wrong row.
    """
    result = {
        "front_image": None,
        "back_image": None,
        "detail": {},
        "status": "no_front",
    }
    if not tess.available:
        result["detail"]["reason"] = "tesseract unavailable"
        return result

    base_name = pdf_path.stem
    try:
        with pymupdf.open(pdf_path) as doc:
            if doc.page_count == 0:
                result["detail"]["reason"] = "document has no pages"
                return result

            # --- classify each page ---
            sides: dict[str, tuple[int, dict]] = {}
            best_front_score = 0
            best_has_marker = False
            for pno in range(doc.page_count):
                page = doc.load_page(pno)
                side, detail = classify_page(page, tess)
                if detail.get("front_score", 0) > best_front_score:
                    best_front_score = detail.get("front_score", 0)
                    best_has_marker = bool(detail.get("has_marker"))
                if side and side not in sides:
                    sides[side] = (pno, detail)
                if "front" in sides and "back" in sides:
                    break

            result["detail"]["best_front_score"] = best_front_score
            result["detail"]["best_has_marker"] = best_has_marker

            if "front" not in sides:
                result["detail"]["reason"] = (
                    "no page in the document classified as a passport front "
                    f"(inspected {doc.page_count} page(s))"
                )
                result["detail"]["pages"] = doc.page_count
                # If a page DOES look like a passport but was not
                # classified as the front, keep its image. The exception
                # report then reports NO_PASSPORT_FRONT ("send the
                # passport pages") rather than MRZ_UNREADABLE ("send a
                # clearer scan") — a more accurate request.
                if best_front_score >= 3 and best_has_marker:
                    for pno in range(doc.page_count):
                        page = doc.load_page(pno)
                        out = image_dir / f"{base_name}_front"
                        saved = _save_best_image(doc, page, out)
                        if saved:
                            result["front_image"] = str(saved)
                            result["detail"]["front_page"] = pno
                            break
                return result

            # --- save the best image for each classified side ---
            for side in ("front", "back"):
                if side not in sides:
                    continue
                pno, detail = sides[side]
                page = doc.load_page(pno)
                out = image_dir / f"{base_name}_{side}"
                saved = _save_best_image(doc, page, out)
                if saved:
                    result[f"{side}_image"] = str(saved)
                    result["detail"][side] = detail
    except Exception as exc:  # noqa: BLE001 - a bad PDF must not kill the batch
        result["detail"]["reason"] = f"{type(exc).__name__}: {exc}"
        return result

    if result["front_image"]:
        result["status"] = "front_found"
    return result


def _save_best_image(doc, page, output_path: Path) -> Path | None:
    """Save the highest-resolution image for a page.

    Prefers an embedded image (the PDF's own pixels); falls back to the
    rendered page when the page has no embedded image above the floor.
    """
    best_xref = None
    best_pixels = 0
    for img in page.get_images(full=True):
        xref, w, h = img[0], img[2], img[3]
        if w * h < MIN_IMAGE_PIXELS:
            continue
        if w * h > best_pixels:
            best_pixels = w * h
            best_xref = xref

    if best_xref is not None and save_xref(doc, best_xref, output_path):
        written = sorted(output_path.parent.glob(f"{output_path.name}.*"))
        if written:
            return written[0]

    # Fall back to the rendered page.
    try:
        pix = page.get_pixmap(dpi=200)
    except (RuntimeError, ValueError):
        return None
    out = output_path.with_suffix(".png")
    try:
        pix.save(out)
    except (RuntimeError, ValueError, OSError):
        return None
    return out


# --------------------------------------------------------------------------
# Stage 2 — read the MRZ with joint check-digit validation
# --------------------------------------------------------------------------


def _ocr_lines(img: Image.Image, bands, scales, psms, inverts, lang, tess) -> set[str]:
    """Harvest normalised candidate lines from an image across OCR variants.

    Each crop is padded with white so a glyph sitting against the page
    edge is not clipped by the crop boundary.
    """
    width, height = img.size
    cands: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        out_png = os.path.join(tmp, "band.png")
        for band in bands:
            if isinstance(band, tuple):
                top, bottom = band
            else:
                top, bottom = band, 1.0
            for scale in scales:
                for psm in psms:
                    for invert in inverts:
                        crop = img.crop(
                            (0, int(height * top), width, int(height * bottom))
                        ).convert("L")
                        if scale > 1:
                            crop = crop.resize(
                                (width * scale, crop.height * scale), Image.LANCZOS
                            )
                        if invert:
                            crop = ImageOps.invert(crop)
                        crop = ImageOps.expand(crop, border=40, fill=255)
                        crop.save(out_png)
                        for raw in tess.run(out_png, lang, psm).splitlines():
                            cand = normalise(raw)
                            if len(cand) >= 30:
                                cands.add(cand)
    return cands


def locate_mrz_band(img: Image.Image, tess: Tesseract) -> tuple | None:
    """Locate the MRZ band on the page using Tesseract's TSV output.

    Returns (left, top, right, bottom) in pixels, or None.

    Why this exists: Tesseract's layout analysis SPLITS an MRZ line into
    several text lines when a character at the edge is isolated — a
    leading `C` sitting apart from the rest of line 2 is read as its own
    one-character line. Harvesting OCR output line by line then silently
    drops that character, which is exactly the character that decides the
    passport number.

    Asking Tesseract where the lines are, and cropping to the band that
    holds them, fixes that: the crop is then OCR'd as one block, so the
    line stays intact.
    """
    if not tess.available:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "page.png")
        img.convert("L").save(png)
        lines = mrz_locate.tsv_lines(tess.binary, png, tess.env)
        if not lines:
            return None
        width, height = img.size
        band = mrz_locate.find_mrz_band(lines, width, height)
        if band is None:
            return None
        top, bottom, members = band
        left = min(m[2]["left"] for m in members)
        right = max(m[2]["right"] for m in members)
        return (left, top, right, bottom)


def locate_visual_zone_band(img: Image.Image, tess: Tesseract) -> tuple | None:
    """Locate the visual-zone header row — the line that carries
    "REPUBLIC OF INDIA" and the printed passport number.

    Returns (left, top, right, bottom) in pixels, or None.

    The printed passport number sits on the same row as the country
    header, at the right-hand end of the page. Finding that row means
    the number can be read from one small crop instead of a sweep over
    the whole page.
    """
    if not tess.available:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "page.png")
        img.convert("L").save(png)
        lines = mrz_locate.tsv_lines(tess.binary, png, tess.env, psm=3, lang="eng")
        if not lines:
            return None
        for ln in lines:
            text = ln["text"].upper()
            if "REPUBLIC" in text and "INDIA" in text:
                return (ln["left"], ln["top"], ln["right"], ln["bottom"])
    return None


def ocr_mrz_candidates(image_path: str, tess: Tesseract) -> set[str]:
    """Harvest candidate MRZ lines from the page.

    Uses the OCR-B model: it is trained on the MRZ font and prevents the
    classic `<` -> `K`/`E`/`S`/`C` misreads that corrupt check digits.

    Two strategies, in order:

    1. LOCATE the MRZ band with the TSV output and crop to it. The crop
       is OCR'd as a single block, so a line stays one line even when
       Tesseract's layout analysis would otherwise split off an isolated
       edge character.
    2. FALL BACK to a sliding window over the page, because the MRZ
       band's position depends on how the passport was scanned: flat
       scans put it at the very bottom, photographs of a passport on a bed
       put it much higher up.

    The window sweep also tries the 90-degree rotations, for the scans
    where the MRZ runs vertically. It stops as soon as the candidates
    hold a VALIDATED pair, because more sweeping cannot improve on a
    pair that already passes the ICAO check digits.
    """
    try:
        img = Image.open(image_path)
        img.load()
    except (OSError, ValueError):
        return set()

    cands: set[str] = set()
    width, height = img.size

    # --- 1. Located band ---
    band = locate_mrz_band(img, tess)
    if band:
        left, top, right, bottom = band
        pad = max(8, int(height * 0.01))
        crop = img.crop(
            (
                max(0, left - pad),
                max(0, top - pad),
                min(width, right + pad),
                min(height, bottom + pad),
            )
        ).convert("L")
        with tempfile.TemporaryDirectory() as tmp:
            out_png = os.path.join(tmp, "band.png")
            for scale in MRZ_SCALES:
                c = crop.resize(
                    (crop.width * scale, crop.height * scale), Image.LANCZOS
                )
                c = ImageOps.expand(c, border=40, fill=255)
                for psm in MRZ_PSMS:
                    for invert in MRZ_INVERTS:
                        if invert:
                            ImageOps.invert(c).save(out_png)
                        else:
                            c.save(out_png)
                        for raw in tess.run(out_png, "ocrb", psm).splitlines():
                            cand = normalise(raw)
                            if len(cand) >= 30:
                                cands.add(cand)

    if _has_valid_pair(cands):
        return cands

    # --- 2. Sliding window, plus the 90-degree rotations ---
    rotations = [img]
    for k in (-1, 1):
        rotations.append(img.rotate(90 * k, expand=True))

    for source in rotations:
        if _has_valid_pair(cands):
            break
        sw, sh = source.size
        with tempfile.TemporaryDirectory() as tmp:
            out_png = os.path.join(tmp, "band.png")
            for start in MRZ_BAND_STARTS:
                end = min(1.0, start + MRZ_WINDOW_HEIGHT)
                for scale in MRZ_SCALES:
                    for psm in MRZ_PSMS:
                        for invert in MRZ_INVERTS:
                            crop = source.crop(
                                (0, int(sh * start), sw, int(sh * end))
                            ).convert("L")
                            if scale > 1:
                                crop = crop.resize(
                                    (sw * scale, crop.height * scale), Image.LANCZOS
                                )
                            if invert:
                                crop = ImageOps.invert(crop)
                            crop = ImageOps.expand(crop, border=40, fill=255)
                            crop.save(out_png)
                            for raw in tess.run(out_png, "ocrb", psm).splitlines():
                                cand = normalise(raw)
                                if len(cand) >= 30:
                                    cands.add(cand)
                            if _has_valid_pair(cands):
                                break
                        if _has_valid_pair(cands):
                            break
                    if _has_valid_pair(cands):
                        break
    return cands


def _has_valid_pair(cands: set[str]) -> bool:
    """True if the candidates already contain a pair that MEETS the bar.

    A clipped MRZ (the leading character of a line cut off by the page
    edge) has no directly-valid pair — the pair only exists after the
    repair. So the repair is considered too, otherwise the sweep runs to
    the end every time on those documents.
    """
    if not cands:
        return False
    line1s, line2s, _ = _split_windows(cands)
    if line1s and line2s:
        for l1 in line1s:
            for l2 in line2s:
                if score_pair(l1, l2)[0] >= MIN_MRZ_SCORE:
                    return True

    # The repaired space.
    l1_pool, l2_pool = _repair_space(cands)
    if l1_pool and l2_pool:
        # Sample rather than cross the whole product: the pools are large
        # and one confirmed pair is enough to stop sweeping.
        for l1 in l1_pool[:40]:
            for l2 in l2_pool[:400]:
                if score_pair(l1, l2)[0] >= MIN_MRZ_SCORE:
                    return True
    return False


def repair_insertions(cand: str) -> list[str]:
    """All 44-char strings reachable by inserting one glyph into cand."""
    out = []
    for pos in range(len(cand) + 1):
        for ch in ALPHA:
            trial = cand[:pos] + ch + cand[pos:]
            if len(trial) == 44:
                out.append(trial)
    return out


def repair_substitutions(cand: str, positions=None) -> list[str]:
    """All 44-char strings reachable by replacing one glyph in cand.

    `positions` limits the substitution to a subset of offsets. OCR
    commonly mis-reads a character rather than losing it — a leading `C`
    reads as `F`, `5` as `3` — and a substitution repair is what recovers
    those. Restricting the offsets keeps the search bounded: only the
    first field (the passport number) and the nationality/sex fields are
    worth trying, because the check digits already constrain the rest.
    """
    out = []
    n = len(cand)
    if positions is None:
        positions = range(n)
    for pos in positions:
        if pos >= n:
            continue
        for ch in ALPHA:
            trial = cand[:pos] + ch + cand[pos + 1:]
            if len(trial) == 44:
                out.append(trial)
    return out


def _split_windows(cands: set[str]) -> tuple[set[str], set[str], list[str]]:
    """Split candidates into 44-char line-1 windows, line-2 windows and
    damaged (non-44) candidates."""
    line1s: set[str] = set()
    line2s: set[str] = set()
    damaged: list[str] = []
    for cand in cands:
        for off in range(0, max(1, len(cand) - 43)):
            win = cand[off:off + 44]
            if len(win) != 44:
                continue
            if win[0:2] == "P<":
                line1s.add(win)
            else:
                line2s.add(win)
        if len(cand) != 44:
            damaged.append(cand)
    return line1s, line2s, damaged


def _repair_space(
    cands: set[str],
) -> tuple[list[str], list[str]]:
    """Build the joint repair space for line 1 and line 2.

    Fix 6: the repair must be able to lose the LEADING character of either
    line (the scan clips the MRZ's left edge). Line 1 is repaired by
    insertion of any glyph that yields a 'P<' header; line 2 by insertion
    of any glyph at any position. The two spaces are then crossed and
    scored together, so the composite check digit can arbitrate.
    """
    line1s, line2s, damaged = _split_windows(cands)

    l1_pool: set[str] = set(line1s)
    l2_pool: set[str] = set(line2s)

    for cand in damaged:
        # A line-1 candidate: the name block is `P<IND` followed by the
        # surname. OCR routinely mangles the leading `P<` into other
        # glyphs (`<` reads as `S`, `I` as `1`), so the trigger is
        # "contains IND", not "starts with IND".
        if "IND" in cand[:12]:
            for trial in repair_insertions(cand):
                if trial[0:2] == "P<":
                    l1_pool.add(trial)
            # A substitution repair of the leading glyphs recovers the
            # `P<` header when the character was mis-read rather than
            # lost.
            for trial in repair_substitutions(cand, [0, 1, 2]):
                if trial[0:2] == "P<":
                    l1_pool.add(trial)
        # A line-2 candidate: it holds a passport number, so it contains
        # a long digit run. The leading character may be OCR'd as a
        # letter (a leading `C` often reads as `F`), so do NOT require
        # the first character to be a digit — require digits anywhere in
        # the first field instead.
        head = cand[:9]
        if sum(ch.isdigit() for ch in head) >= 5:
            for trial in repair_insertions(cand):
                l2_pool.add(trial)
            # A mis-read character is as common as a lost one. Substitute
            # only inside the passport-number field and the
            # nationality/sex fields — the check digits constrain the
            # rest, so trying every offset would just be slow.
            subst_positions = list(range(0, 9)) + [10, 11, 12, 20]
            for trial in repair_substitutions(cand, subst_positions):
                l2_pool.add(trial)

    # If no line 1 could be found at all, allow repairing any damaged
    # candidate into a 'P<' header.
    if not l1_pool:
        for cand in damaged:
            for trial in repair_insertions(cand):
                if trial[0:2] == "P<":
                    l1_pool.add(trial)

    return sorted(l1_pool), sorted(l2_pool)


def best_pair(cands: set[str]) -> tuple:
    """Find the highest-scoring validated MRZ pair among candidates.

    Returns (line1, line2, score, checks, field_hits, all_valid) where
    `all_valid` is every pair scoring >= 3.
    """
    if not cands:
        return None, None, 0, [], 0, []

    line1s, line2s, damaged = _split_windows(cands)
    best = (None, None, 0, [], 0)
    all_valid: list[tuple[str, str, int, list[str], int]] = []

    if line1s and line2s:
        best, all_valid = _search_pairs(sorted(line1s), sorted(line2s))
        if best[2] >= 4:
            return (*best, all_valid)

    l1_pool, l2_pool = _repair_space(cands)
    if l1_pool and l2_pool:
        repaired, repaired_all = _search_pairs(l1_pool, l2_pool)
        if repaired[2] > best[2]:
            best = repaired
        all_valid = all_valid + repaired_all

    return (*best, all_valid)


def _pair_plausibility(line1: str, line2: str) -> tuple[int, int, int]:
    """Structural plausibility of an MRZ pair, as a tie-break score.

    The ICAO check digits cannot distinguish two readings of the same
    document that both happen to satisfy them: on the FAMBB document the
    true line 2 `P9000002<7IND8002217M35...` and an OCR-corrupted
    `P9000002<71ND80022171350...` (`I` misread as `1`, `M` as `1`) both
    score 3/4 with all three per-field digits valid.

    These structural signals break that tie:
      - the nationality is a plausible ISO alpha-3 code (`IND`, not `1ND`)
      - the sex field is `M`, `F` or `<`
      - the line-1 header is the canonical `P<`
    """
    if len(line2) < 44 or len(line1) < 2:
        return (0, 0, 0)
    nat_ok = 1 if line2[10:13].isalpha() else 0
    sex_ok = 1 if line2[20] in ("M", "F", "<") else 0
    magic_ok = 1 if line1[:2] == "P<" else 0
    return nat_ok, sex_ok, magic_ok


def build_line2_from_vz(passport_no: str, l2_template: str) -> str | None:
    """Rewrite a line-2 candidate's passport-number field from the number
    printed in the visual zone.

    When the scan clips or mis-reads the passport number, blind
    multi-substitution search over the MRZ alphabet cannot recover it —
    the FAMCCCCCCCCCCC document has FOUR mis-read characters in that
    field, so no single-glyph repair reaches the truth.

    But the visual zone gives the number directly and independently, so
    the field is simply replaced and the check digit recomputed. The
    rest of the line (nationality, DOB, expiry) is left as read, and
    the joint score then decides whether the result is credible.

    Returns the rebuilt 44-char line 2, or None if the template does not
    have the right shape.
    """
    if not passport_no or len(l2_template) != 44:
        return None
    field = passport_no[:9].ljust(9, "<")
    if len(field) != 9:
        return None
    rebuilt = field + l2_template[9:]
    # Recompute the passport-number check digit (offset 9).
    cd = check_digit(rebuilt[0:9], W10)
    return rebuilt[:9] + cd + rebuilt[10:]


def _rebuild_with_vz(cands: set[str], l1: str | None, vz_number: str):
    """Rebuild the MRZ pair using the passport number from the visual zone.

    Returns the same tuple shape as `best_pair`, or None if no candidate
    could be rebuilt into a valid pair.
    """
    if not l1 or len(l1) != 44:
        return None

    # Every 44-char window that could be a line 2.
    templates: set[str] = set()
    for cand in cands:
        for off in range(0, max(1, len(cand) - 43)):
            win = cand[off:off + 44]
            if len(win) == 44:
                templates.add(win)
    # Also the repaired forms.
    l1_pool, l2_pool = _repair_space(cands)
    templates.update(l2_pool)

    rebuilt_l2s: set[str] = set()
    for tpl in templates:
        new = build_line2_from_vz(vz_number, tpl)
        if new:
            rebuilt_l2s.add(new)
    if not rebuilt_l2s:
        return None

    best, all_valid = _search_pairs([l1], sorted(rebuilt_l2s))
    if not all_valid:
        return None
    return (*best, all_valid)


def _search_pairs(l1s: list[str], l2s: list[str]) -> tuple:
    """Score every (l1, l2) pair.

    Returns (best, all_valid) where `all_valid` is the list of EVERY pair
    that scores >= 3. The full list matters: the check digits alone
    cannot tell `P4000001` from `W4000001` — the visual zone picks which
    of them is the document's real number, and it can only do that if it
    is given all the candidates.

    Ranking order:
      1. total ICAO score (0-4)
      2. number of line-2 per-field digits that validate (0-3)
      3. structural plausibility: ISO nationality, M/F sex, `P<` header
      4. the line-2 that appeared VERBATIM in the OCR output (a repair is
         a guess; an un-repaired read is evidence)
    """
    best = (None, None, 0, [], 0)
    all_valid: list[tuple[str, str, int, list[str], int]] = []
    for l1 in l1s:
        for l2 in l2s:
            score, detail = score_pair(l1, l2)
            if score < 3:
                continue
            field_hits = sum(line2_field_checks(l2).values())
            all_valid.append((l1, l2, score, detail, field_hits))
            key = (
                score,
                field_hits,
                _pair_plausibility(l1, l2),
            )
            best_key = (
                best[2],
                best[4],
                _pair_plausibility(best[0] or "", best[1] or ""),
            )
            if key > best_key:
                best = (l1, l2, score, detail, field_hits)
    return best, all_valid


def parse_fields(line1: str, line2: str) -> dict[str, str]:
    """Extract the structured fields from a validated MRZ pair.

    Line 1 is `P<IND<SURNAME<<GIVEN<NAMES<<<<...`. The surname and the
    given names are separated by a DOUBLE `<`; a single `<` inside the
    given-names field separates its parts (`GIVAB<GIVABCD`).

    So: split on the double `<`, take the FIRST two segments. Taking the
    LAST segment (as some reference snippets do) returns the trailing
    filler run and loses the name entirely.

    The nationality is normalised: OCR reads `I` as `1` often enough
    (`1ND` for `IND`), and the composite check digit does NOT cover
    positions 11-13 — ICAO excludes them — so a wrong nationality can
    pass a 4/4 score. Any digit in an alpha-3 country code is therefore
    repaired to its letter.
    """
    parts = line1[5:].split("<<")
    surname = parts[0].replace("<", " ").strip() if parts else ""
    given = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""
    return {
        "passport_no": line2[0:9].replace("<", "").strip(),
        "nationality": clean_nationality(line2[10:13]),
        "dob": line2[13:19],
        "sex": line2[20],
        "expiry": line2[21:27],
        "surname": surname,
        "given_names": given,
    }


# OCR confusions in an ISO alpha-3 country code: digit -> letter.
_DIGIT_TO_LETTER = {
    "0": "O", "1": "I", "2": "Z", "3": "E", "4": "A",
    "5": "S", "6": "G", "7": "T", "8": "B", "9": "P",
}


def clean_nationality(raw: str) -> str:
    """Normalise an MRZ nationality field to an ISO alpha-3 code.

    The composite check digit excludes positions 11-13 (ICAO Doc 9303
    Part 4, section 4.2.4), so a mis-read nationality passes a 4/4
    score. `IND` read as `1ND` is the common case, so any digit in an
    alpha-3 code is mapped back to the letter OCR most likely meant.
    """
    if not raw:
        return ""
    out = "".join(_DIGIT_TO_LETTER.get(ch, ch) for ch in raw.upper())
    return out if len(out) == 3 and out.isalpha() else raw.upper()


# --------------------------------------------------------------------------
# Visual zone — the independent read of the printed passport number
# --------------------------------------------------------------------------


def visual_zone_numbers(front_image: str, tess: Tesseract) -> dict[str, int]:
    """Read the printed passport number from the visual zone.

    Returns {normalised_number: how_many_ocr_variants_produced_it}.

    The variant count is the confidence signal: the correct reading is
    produced by many more OCR variants than a mis-read single character.

    The printed number is found by asking Tesseract where the text lines
    are (TSV) and reading the upper part of the data page, which is where
    the "Passport No." field sits. The blind band sweep is the fallback.
    """
    try:
        img = Image.open(front_image)
        img.load()
    except (OSError, ValueError):
        return {}

    counts: dict[str, int] = {}
    width, height = img.size

    def harvest(crop: Image.Image, scales, psms, inverts) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_png = os.path.join(tmp, "vz.png")
            for scale in scales:
                c = crop
                if scale > 1:
                    c = crop.resize(
                        (crop.width * scale, crop.height * scale), Image.LANCZOS
                    )
                c = ImageOps.expand(c, border=30, fill=255)
                for psm in psms:
                    for invert in inverts:
                        if invert:
                            ImageOps.invert(c).save(out_png)
                        else:
                            c.save(out_png)
                        text = tess.run(out_png, "eng", psm)
                        for m in re.finditer(r"([A-Z])\s*([0-9]{7})\b", text):
                            key = m.group(0).replace(" ", "")
                            counts[key] = counts.get(key, 0) + 1

    # --- 1. The visual zone, found from the text layout ---
    # The printed number sits on the row that also carries "REPUBLIC OF
    # INDIA" — the header of the data page. Ask Tesseract where that
    # line is and crop around it, because the page may be a photograph
    # in which the passport occupies only part of the frame.
    band = locate_visual_zone_band(img, tess)
    if band:
        left, top, right, bottom = band
        harvest(
            img.crop((max(0, left - 40), max(0, top - 40),
                      min(width, right + 40), min(height, bottom + 40))).convert("L"),
            VZ_SCALES, VZ_PSMS, VZ_INVERTS,
        )
    if counts:
        return counts

    # --- 2. The upper part of the data page ---
    top = int(height * 0.40)
    harvest(
        img.crop((0, 0, width, top)).convert("L"),
        VZ_SCALES, VZ_PSMS, VZ_INVERTS,
    )
    if counts:
        return counts

    # --- 3. Fallback: sweep bands down the page ---
    for start in VZ_BAND_STARTS:
        end = min(1.0, start + VZ_WINDOW_HEIGHT)
        harvest(
            img.crop((0, int(height * start), width, int(height * end))).convert("L"),
            VZ_SCALES, VZ_PSMS, VZ_INVERTS,
        )
        if counts:
            break
    return counts


def select_candidate(
    candidates: list[tuple[str, str, int, list[str], int]],
    vz_counts: dict[str, int],
) -> tuple[tuple | None, dict]:
    """Choose the one MRZ candidate that the visual zone confirms.

    The check digits alone cannot disambiguate. On the ground-truth
    document, 37 different passport numbers all satisfy the three
    per-field check digits — including `W4000001` and `4000001A` — so
    picking by score alone publishes a wrong passport number. The
    printed number in the visual zone is an INDEPENDENT read of the same
    page, so it is what selects the candidate.

    Returns (chosen_candidate, decision_detail).
    """
    detail = {"candidates": len(candidates)}
    if not candidates:
        return None, detail

    # Best by check digits alone (the fallback when the visual zone gives
    # no usable reading).
    best = max(candidates, key=lambda c: (c[2], c[4]))

    if not vz_counts:
        detail["selection"] = "check_digits_only"
        detail["reason"] = "the printed passport number could not be read"
        return best, detail

    # Prefer the visual-zone reading with the strongest OCR support.
    top_vz = max(vz_counts.items(), key=lambda kv: kv[1])
    vz_number, vz_support = top_vz
    detail["visual_zone_top"] = vz_number
    detail["visual_zone_support"] = vz_support

    matching = [c for c in candidates if c[1][0:9].replace("<", "") == vz_number]
    if matching:
        # Several readings can share the passport number while differing
        # in the nationality or sex characters (OCR reads `I` as `1`).
        # Rank those by the same structural plausibility used elsewhere.
        chosen = max(
            matching,
            key=lambda c: (c[2], c[4], _pair_plausibility(c[0], c[1])),
        )
        detail["selection"] = "visual_zone_confirmed"
        detail["visual_zone_candidates"] = len(matching)
        return chosen, detail

    # The visual zone read something, but no MRZ candidate agrees with it.
    # That is a genuine disagreement: publish nothing.
    detail["selection"] = "mismatch"
    detail["reason"] = (
        f"the printed passport number {vz_number} does not match any MRZ "
        f"candidate ({', '.join(sorted({c[1][0:9].replace('<', '') for c in candidates}))})"
    )
    return None, detail


# --------------------------------------------------------------------------
# Date helpers
# --------------------------------------------------------------------------


def format_dob(yymmdd: str) -> str:
    """`YYMMDD` -> `DD-MM-YYYY`. A future two-digit year is 19xx."""
    if not yymmdd or len(yymmdd) != 6 or not yymmdd.isdigit():
        return ""
    try:
        dt = datetime.strptime(yymmdd, "%y%m%d")
        if dt.year > datetime.now().year:
            dt = dt.replace(year=dt.year - 100)
        return dt.strftime("%d-%m-%Y")
    except ValueError:
        return ""


def format_expiry(yymmdd: str) -> str:
    """`YYMMDD` -> `DD-MM-YYYY`. An expiry is in the current or next decade."""
    if not yymmdd or len(yymmdd) != 6 or not yymmdd.isdigit():
        return ""
    try:
        dt = datetime.strptime(yymmdd, "%y%m%d")
        # A passport expiry more than 20 years out is a mis-read century.
        if dt.year < datetime.now().year - 20:
            dt = dt.replace(year=dt.year + 100)
        return dt.strftime("%d-%m-%Y")
    except ValueError:
        return ""


def derive_issue_date(expiry: str) -> str:
    """Indian adult passports: issue date = expiry - 10 years + 1 day.

    This is an INFERENCE, not a reading. It is flagged `derived` so the
    report can raise ISSUE_DATE_UNCONFIRMED.
    """
    if not expiry:
        return ""
    try:
        exp = datetime.strptime(expiry, "%d-%m-%Y")
        try:
            issue = exp.replace(year=exp.year - 10)
        except ValueError:
            issue = exp.replace(year=exp.year - 10, day=28)
        return (issue + timedelta(days=1)).strftime("%d-%m-%Y")
    except ValueError:
        return ""


# --------------------------------------------------------------------------
# Batch driver
# --------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_message_id(path: Path) -> str:
    """WhatsApp store files are named document_<ts>_<message_id>."""
    parts = path.stem.split("_")
    if len(parts) >= 3 and parts[0] in ("document", "image", "video"):
        return parts[-1]
    return ""


def is_pdf(path: Path) -> bool:
    """True if the file is a PDF.

    WhatsApp's document store writes files with no extension at all
    (`document_20261028_133331_FIXTURE00000000012`), so the extension
    cannot be trusted — sniff the magic bytes instead.
    """
    if path.suffix.lower() == ".pdf":
        return True
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def process_one(pdf_path: Path, outdir: Path, image_dir: Path, tess: Tesseract) -> dict:
    """Process one PDF and return one Stage 2 contract record."""
    record = {
        "source_message_id": source_message_id(pdf_path),
        "original_filename": pdf_path.name,
        "file_sha256": sha256_of(pdf_path),
        "chat": "",
        "received_at": datetime.fromtimestamp(pdf_path.stat().st_mtime).isoformat(),
        "status": "unread",
        "front_image": None,
        "back_image": None,
        "mrz": {"line1": None, "line2": None, "score": 0, "checks": []},
        "fields": {},
        "visual_zone": {},
        "notes": [],
    }

    if not tess.available:
        record["status"] = "no_front"
        record["notes"].append("tesseract unavailable - nothing was classified or read")
        return record

    s1 = stage1(pdf_path, image_dir, tess)
    record["front_image"] = s1["front_image"]
    record["back_image"] = s1["back_image"]

    if not s1["front_image"]:
        record["status"] = "no_front"
        reason = s1["detail"].get("reason", "no passport front found")
        record["notes"].append(reason)
        # Distinguish "this document is not a passport" from "this is a
        # passport but the front page was not found". The first needs no
        # traveller request at all; the second does.
        #
        # `passport_likelihood` is the best front-keyword score seen on
        # any page. A document with a score of 0 (and no passport
        # marker) contains nothing passport-like — it is a brochure, a
        # form, a CV — and asking that traveller to resend a passport
        # would be a false request.
        record["passport_likelihood"] = s1["detail"].get("best_front_score", 0)
        record["contains_passport_page"] = bool(
            s1["detail"].get("best_front_score", 0) >= 3
            and s1["detail"].get("best_has_marker", False)
        )
        if not record["contains_passport_page"]:
            record["notes"].append(
                "the document contains no passport page - no traveller "
                "request is needed for this file"
            )
        return record

    # --- MRZ candidates ---
    cands = ocr_mrz_candidates(s1["front_image"], tess)

    # --- Visual zone: the independent read of the printed number ---
    # This is read BEFORE the MRZ is scored, because it is needed to
    # rebuild a line 2 whose passport-number field was clipped or
    # mis-read by the OCR.
    vz_counts = visual_zone_numbers(s1["front_image"], tess)
    vz_number = ""
    if vz_counts:
        vz_number = max(vz_counts.items(), key=lambda kv: kv[1])[0]

    l1, l2, score, checks, field_hits, all_valid = best_pair(cands)

    # --- Rebuild line 2 from the visual-zone number ---
    # A scan can clip or mis-read the passport-number field. Blind
    # multi-substitution search over the MRZ alphabet cannot recover it
    # (the FAMCCCCCCCCCCC document has four mis-reads in that field), but
    # the printed number is available directly, so the field is replaced
    # and its check digit recomputed.
    if vz_number and not all_valid:
        rebuilt = _rebuild_with_vz(cands, l1, vz_number)
        if rebuilt:
            l1, l2, score, checks, field_hits, all_valid = rebuilt

    record["mrz"] = {
        "line1": l1,
        "line2": l2,
        "score": score,
        "checks": checks,
        "candidates": len(cands),
    }

    if not all_valid:
        record["status"] = "unread"
        record["notes"].append(
            f"MRZ validated at only {score}/4 check digits - no fields published"
        )
        if vz_number:
            record["notes"].append(
                f"the printed passport number {vz_number} was read, but the MRZ "
                "could not be validated against it"
            )
        return record

    # --- Select the candidate the visual zone confirms ---
    chosen, decision = select_candidate(all_valid, vz_counts)

    record["visual_zone"] = {"passport_no": vz_number} if vz_number else {}
    record["mrz"]["candidate_pairs"] = len(all_valid)

    if chosen is None:
        record["status"] = "unread"
        record["notes"].append(decision.get("reason", "the MRZ could not be confirmed"))
        return record

    l1, l2, score, checks, field_hits = chosen
    record["mrz"]["line1"] = l1
    record["mrz"]["line2"] = l2
    record["mrz"]["score"] = score
    record["mrz"]["checks"] = checks

    if score < MIN_MRZ_SCORE:
        record["status"] = "unread"
        record["notes"].append(
            f"MRZ validated at only {score}/4 check digits - no fields published"
        )
        return record

    raw = parse_fields(l1, l2)
    dob = format_dob(raw["dob"])
    expiry = format_expiry(raw["expiry"])

    record["status"] = "ok"
    record["fields"] = {
        "passport_no": raw["passport_no"],
        "surname": raw["surname"],
        "given_names": raw["given_names"],
        "nationality": raw["nationality"],
        "sex": raw["sex"],
        "dob": dob,
        "expiry": expiry,
        "issue_date": derive_issue_date(expiry),
        "issue_date_source": "derived",
    }

    if decision.get("selection") == "visual_zone_confirmed":
        record["notes"].append(
            f"MRZ passport number confirmed against the printed number "
            f"({decision['visual_zone_top']})"
        )
    elif decision.get("selection") == "check_digits_only":
        record["notes"].append(
            "the printed passport number could not be read - the MRZ was "
            "accepted on its check digits alone"
        )

    vz_number = record["visual_zone"].get("passport_no")
    if vz_number and vz_number != raw["passport_no"]:
        record["notes"].append(
            f"printed passport number {vz_number} differs from the MRZ "
            f"{raw['passport_no']}"
        )
    if raw["sex"] not in ("M", "F"):
        record["notes"].append(f"sex field reads '{raw['sex']}', not M or F")
    return record


def write_csv(records: list[dict], outdir: Path) -> Path:
    """Write the clean-data CSV in Passport_dataV2.0's format."""
    path = outdir / "Cleaned_Passport_Data.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for rec in records:
            if rec["status"] != "ok":
                continue
            f = rec["fields"]
            writer.writerow(
                {
                    "File": rec.get("source_message_id")
                    or re.sub(r"[^A-Za-z0-9]", "", rec["original_filename"])[:8],
                    "Passport Number": f.get("passport_no", ""),
                    "Given Name": f.get("given_names", ""),
                    "Surname": f.get("surname", ""),
                    "Nationality": f.get("nationality", ""),
                    "Gender": f.get("sex", ""),
                    "Date of Birth": f.get("dob", ""),
                    "Issue Date": f.get("issue_date", ""),
                    "Expiration Date": f.get("expiry", ""),
                }
            )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="folder of traveller PDFs")
    parser.add_argument("--outdir", required=True, help="output working directory")
    parser.add_argument("--tesseract", help="path to the tesseract binary")
    parser.add_argument("--tessdata", help="tessdata directory (eng + ocrb)")
    parser.add_argument(
        "--extract-name",
        default="extracted.json",
        help="filename for the Stage 2 JSON contract output",
    )
    args = parser.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.outdir)
    if not in_dir.is_dir():
        print(f"input folder not found: {in_dir}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    image_dir = out_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    tess = Tesseract(args.tesseract, args.tessdata)
    if not tess.available:
        print(
            "tesseract is not available - no document can be classified or read. "
            "Refusing to guess. Install tesseract or pass --tesseract.",
            file=sys.stderr,
        )
        return 2

    pdfs = sorted(p for p in in_dir.iterdir() if p.is_file() and is_pdf(p))
    if not pdfs:
        print(f"no PDF files found in {in_dir}", file=sys.stderr)
        return 2

    records = []
    for pdf in pdfs:
        rec = process_one(pdf, out_dir, image_dir, tess)
        records.append(rec)
        status = rec["status"]
        score = rec["mrz"].get("score", 0)
        detail = ""
        if status == "ok":
            f = rec["fields"]
            detail = (
                f"  {f.get('passport_no','')}  {f.get('surname','')} "
                f"{f.get('given_names','')}  dob={f.get('dob','')} "
                f"exp={f.get('expiry','')}  sex={f.get('sex','')}"
            )
        elif status == "unread":
            detail = f"  MRZ score {score}/4 - no fields published"
        else:
            detail = "  " + "; ".join(rec["notes"])
        print(f"[{status:8}] {pdf.name}{detail}", flush=True)

    extract_path = out_dir / args.extract_name
    with open(extract_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)

    csv_path = write_csv(records, out_dir)

    ok = sum(1 for r in records if r["status"] == "ok")
    unread = sum(1 for r in records if r["status"] == "unread")
    no_front = sum(1 for r in records if r["status"] == "no_front")
    print()
    print(f"documents : {len(records)}")
    print(f"clean     : {ok}/{len(records)}")
    print(f"unread    : {unread}  (MRZ not validated)")
    print(f"no_front  : {no_front}  (no passport front page found)")
    print(f"extract   : {extract_path}")
    print(f"csv       : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
