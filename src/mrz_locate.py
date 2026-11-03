#!/usr/bin/env python3
"""Locate the MRZ region on a passport page using Tesseract's TSV output.

Tesseract's layout analysis splits an MRZ line into several "text lines"
when a character at the edge is isolated (a leading `C` sitting apart from
the rest of the line 2). Harvesting OCR line by line then loses those
characters.

So: ask Tesseract where the text lines are, find the band that holds the
MRZ (the dense run of A-Z0-9<> characters at the bottom of the data page),
and crop to it. The crop is then OCR'd as a single block, which keeps the
line intact.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

W10 = [7, 3, 1, 7, 3, 1, 7, 3, 1, 7]


def check_digit(chars: str, weights: list[int]) -> str:
    total = 0
    for ch, wt in zip(chars, weights):
        if ch.isdigit():
            val = int(ch)
        elif ch.isalpha():
            val = ord(ch) - 55
        else:
            val = 0
        total += val * wt
    return str(total % 10)


def tsv_lines(binary: str, image_path: str, env: dict, psm: int = 3, lang: str = "ocrb"):
    """Return the OCR text lines with their boxes, from the TSV output.

    The TSV is requested with an explicit `-c tessedit_create_tsv=1` rather
    than the `tsv` config name, because the config file is not always
    present in a minimal tessdata directory — and without it Tesseract
    silently emits HTML instead, which parses as zero text lines.
    """
    try:
        proc = subprocess.run(
            [
                binary, image_path, "stdout",
                "-l", lang, "--psm", str(psm),
                "-c", "tessedit_create_tsv=1",
            ],
            capture_output=True, text=True, env=env, timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = proc.stdout or ""
    if not out.startswith("level\t"):
        return []
    rows = []
    for ln in out.splitlines()[1:]:
        parts = ln.split("\t")
        if len(parts) < 12:
            continue
        try:
            rows.append({
                "block": int(parts[2]), "par": int(parts[3]), "line": int(parts[4]),
                "left": int(parts[6]), "top": int(parts[7]),
                "width": int(parts[8]), "height": int(parts[9]),
                "conf": float(parts[10]), "text": parts[11],
            })
        except ValueError:
            continue

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if not r["text"].strip():
            continue
        groups.setdefault((r["block"], r["par"], r["line"]), []).append(r)

    out_lines = []
    for words in groups.values():
        words = sorted(words, key=lambda w: w["left"])
        text = " ".join(w["text"] for w in words)
        out_lines.append({
            "text": text,
            "left": min(w["left"] for w in words),
            "top": min(w["top"] for w in words),
            "right": max(w["left"] + w["width"] for w in words),
            "bottom": max(w["top"] + w["height"] for w in words),
        })
    return out_lines


def find_mrz_band(lines, width, height):
    """Find the MRZ band: the group of dense, wide text lines at the
    bottom of the page.

    An MRZ line is wide (spanning most of the page), made only of
    A-Z0-9<> and spaces, and sits below the visual zone. Returns
    (top, bottom) in pixels, or None.
    """
    if not lines:
        return None

    scored = []
    for ln in lines:
        clean = re.sub(r"[^A-Z0-9< ]", "", ln["text"].upper())
        clean = clean.replace(" ", "")
        if len(clean) < 20:
            continue
        span = (ln["right"] - ln["left"]) / max(1, width)
        density = len(clean) / max(1, (ln["right"] - ln["left"]))
        scored.append((span, density, ln))

    if not scored:
        return None

    # The MRZ band is the cluster of wide lines in the lower part of the
    # page. Take every wide line whose top is within the lower third,
    # and use the lowest cluster.
    lower = [s for s in scored if s[2]["top"] > height * 0.55]
    if not lower:
        lower = scored

    # Group by proximity so two separate blocks are not merged.
    lower.sort(key=lambda s: s[2]["top"])
    clusters: list[list] = []
    for item in lower:
        placed = False
        for cl in clusters:
            if abs(cl[-1][2]["top"] - item[2]["top"]) < height * 0.06:
                cl.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])

    # Pick the cluster with the most wide lines.
    best = max(clusters, key=lambda cl: (len(cl), max(s[2]["bottom"] for s in cl)))
    top = min(s[2]["top"] for s in best)
    bottom = max(s[2]["bottom"] for s in best)
    return top, bottom, best


def main() -> int:
    binary = sys.argv[1]
    image_path = sys.argv[2]
    env = {
        "PATH": "/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/home/hermes/.hermes/cache/scratch/tstest/prefix/usr/lib/x86_64-linux-gnu",
        "TESSDATA_PREFIX": "/home/hermes/.hermes/cache/scratch/tstest/tessdata",
    }
    img = Image.open(image_path)
    W, H = img.size
    print(f"{image_path}  {W}x{H}")
    lines = tsv_lines(binary, image_path, env)
    print(f"{len(lines)} text lines")
    band = find_mrz_band(lines, W, H)
    if band is None:
        print("no MRZ band found")
        return 1
    top, bottom, members = band
    print(f"MRZ band: y={top}..{bottom}  ({top/H:.3f}..{bottom/H:.3f})")
    for span, density, ln in members:
        print(f"   y={ln['top']}..{ln['bottom']} x={ln['left']}..{ln['right']} "
              f"span={span:.2f} {ln['text'][:60]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
