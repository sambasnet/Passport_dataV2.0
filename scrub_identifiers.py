#!/usr/bin/env python3
"""Replace the real passport identifiers in the test vectors with synthetic
ones that still carry VALID ICAO check digits.

Why: the test vectors were built from real passports, so they contain real
passport numbers, names and MRZ lines. Those must not live in a repository.

How: every MRZ line is rebuilt from its PARSED FIELDS and the check digits
are recomputed with the same arithmetic the engine uses. A synthetic line
that failed its own check digits would prove nothing, so the rebuild is
mandatory — swapping the passport number in place leaves the old check
digits behind and the vector no longer validates.

Every ASSERTION that hard-codes a derived value (a check digit, a
composite digit) is recomputed too, otherwise the scrub would silently
turn the test suite red for the wrong reason.

Details that matter:

* Synthetic names are the SAME LENGTH as the real ones, so a 44-character
  line 1 stays 44 characters and keeps validating.
* The OCR-corrupted vector keeps its STALE check digits, because that is
  what makes it fail to validate — exactly as the real one did.
* The ICAO Appendix A example is left untouched: it is a published
  specification example, not anyone's passport.

Usage:  python3 scrub_identifiers.py <file> [<file> ...]
"""

from __future__ import annotations

import json
import os
import re
import sys

W10 = [7, 3, 1, 7, 3, 1, 7, 3, 1, 7]
W7 = [7, 3, 1, 7, 3, 1]
W3 = [7, 3, 1]

CHAR_VALUES = {
    **{str(d): d for d in range(10)},
    **{chr(c): c - 55 for c in range(ord("A"), ord("Z") + 1)},
    "<": 0,
}

ICAO_EXAMPLE = "L898902C36UT07408122F2504155184226<<<<<<<<18"

# Real passport number -> synthetic. Same length (a letter plus seven
# digits); the MRZ field is 9 characters, padded with the '<' filler.
#
# These literals are the REAL identifiers and must never be committed.
# The table is loaded from `scrub_map.json` (which holds them) at run
# time, and `scrub_map.json` is excluded from the repository. Run
# `python3 scrub_identifiers.py --init-map <path>` to regenerate it from
# a private copy of the vectors.
NUMBERS: dict[str, str] = {}

# Real names -> synthetic names of the SAME LENGTH.
NAMES: dict[str, str] = {}

# The canonical (valid) line 2s, keyed by real passport number.
TRUTH: dict[str, str] = {}

# The OCR-corrupted vector: a mis-read passport-number field with
# everything else (including the check digits) read from the same image.
OCR_TWIN: dict[str, tuple[str, str]] = {}

# The deliberately-corrupted vector whose nationality and sex are mis-read.
BAD_TWIN = ""


def load_map(path: str) -> bool:
    """Load the real->synthetic mapping from a JSON file.

    The mapping holds REAL passport identifiers, so it lives OUTSIDE the
    repository. It is git-ignored and must never be committed.
    """
    global NUMBERS, NAMES, TRUTH, OCR_TWIN, BAD_TWIN
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    NUMBERS = data.get("numbers", {})
    NAMES = data.get("names", {})
    TRUTH = data.get("truth", {})
    OCR_TWIN = {k: tuple(v) for k, v in data.get("ocr_twin", {}).items()}
    BAD_TWIN = data.get("bad_twin", "")
    return bool(NUMBERS and NAMES and TRUTH)


def _first_key_for(template: str) -> str:
    """Find the real passport number whose canonical line 2 this is.

    Used to look up the synthetic replacement without hard-coding a real
    identifier in the source.
    """
    for real, canonical in TRUTH.items():
        if canonical[:9] == template[:9]:
            return real
    for real in TRUTH:
        if real in template:
            return real
    return ""


def check_digit(text: str, weights: list[int]) -> str:
    total = 0
    for i, ch in enumerate(text):
        total += CHAR_VALUES.get(ch, 0) * weights[i % len(weights)]
    return str(total % 10)


def composite_of(line2: str) -> str:
    """The ICAO composite check digit over line 2 (positions 1-10, 14-20, 22-43)."""
    return check_digit(line2[0:10] + line2[13:20] + line2[21:43], W3)


def rebuild_with_field(template: str, field: str) -> str:
    """Replace the passport-number field and recompute its check digit and
    the composite digit. Everything else is carried over verbatim."""
    new_field = field.ljust(9, "<")
    line = new_field + check_digit(new_field, W10) + template[10:43]
    return line + composite_of(line)


def map_names(text: str) -> str:
    for real, syn in NAMES.items():
        text = text.replace(real, syn)
    return text


def rebuild_line1(token: str) -> str:
    """Rebuild a line 1 with synthetic names, preserving its exact length.

    The structure (`P<` or a deliberately-wrong `X<`, the nationality, the
    surname, the `<<` separator, the given names and the trailing filler)
    is preserved so the vector still exercises the same parsing.
    """
    length = len(token)
    if token.startswith("X<"):
        prefix, body = "X<", token[2:]
    elif token.startswith("P<"):
        prefix, body = "P<", token[2:]
    elif token.startswith("<"):
        prefix, body = "<", token[1:]
    else:
        return token
    if "<<" not in body:
        return token
    head, rest = body.split("<<", 1)
    if not head.startswith("IND"):
        return token
    surname, given = head[3:], rest.rstrip("<")
    rebuilt = prefix + "IND" + map_names(surname) + "<<" + map_names(given)
    return rebuilt.ljust(length, "<")


def scrub(text: str) -> str:
    # ------------------------------------------------------------------
    # 1. The 44-character line 2 vectors, handled explicitly because each
    #    one plays a different role in the test suite.
    # ------------------------------------------------------------------
    for real_number, template in TRUTH.items():
        synthetic = rebuild_with_field(template, NUMBERS[real_number])
        text = text.replace(template, synthetic)

    # The OCR-corrupted vector: swap in the synthetic field but keep the
    # stale check digits, so it still fails exactly as it did before.
    for ocr_field, (truth_number, syn_field) in OCR_TWIN.items():
        for template in list(TRUTH.values()):
            ocr_template = ocr_field + template[8:]
            if ocr_template in text:
                synthetic_truth = rebuild_with_field(
                    TRUTH[truth_number], NUMBERS[truth_number])
                text = text.replace(ocr_template,
                                    syn_field + synthetic_truth[8:])

    # The corrupted-nationality/sex vector: the composite digit does not
    # cover those positions, so it carries over unchanged.
    if BAD_TWIN and BAD_TWIN in text:
        bad = rebuild_with_field(BAD_TWIN, NUMBERS.get(_first_key_for(BAD_TWIN), ""))
        text = text.replace(BAD_TWIN, bad)

    # ------------------------------------------------------------------
    # 2. The clipped 43-character line 2: the canonical line with its
    #    leading character removed.
    # ------------------------------------------------------------------
    for real_number, template in TRUTH.items():
        clipped = template[1:]
        if clipped in text:
            synthetic = rebuild_with_field(template, NUMBERS[real_number])
            text = text.replace(clipped, synthetic[1:])

    # ------------------------------------------------------------------
    # 3. Line 1 tokens: same length, synthetic names, same structure.
    # ------------------------------------------------------------------
    def rebuild_l1_token(m: re.Match) -> str:
        return rebuild_line1(m.group(0))

    text = re.sub(r"(?<![A-Z0-9<])[PX]?<IND[A-Z]+<<[A-Z<]+(?![A-Z0-9<])",
                  rebuild_l1_token, text)

    # ------------------------------------------------------------------
    # 4. Bare passport numbers (in dict keys, comparisons and prose).
    # ------------------------------------------------------------------
    for real, syn in NUMBERS.items():
        text = text.replace(real, syn)

    # ------------------------------------------------------------------
    # 5. Real names in assertions and prose (whole words).
    # ------------------------------------------------------------------
    for real, syn in NAMES.items():
        text = re.sub(rf"\b{real}\b", syn, text)

    # ------------------------------------------------------------------
    # 6. RECOMPUTE every hard-coded derived value. The check-digit and
    #    composite-digit assertions quote a literal that was correct for
    #    the real passport; after the rebuild it must be recomputed or the
    #    test goes red for the wrong reason.
    # ------------------------------------------------------------------
    text = re.sub(
        r'(pe\.check_digit\(\s*"([^"]+)"\s*,\s*pe\.(W10|W7)\s*\)\s*==\s*")(\d)(")',
        lambda m: (m.group(1)
                   + check_digit(m.group(2), W10 if m.group(3) == "W10" else W7)
                   + m.group(5)),
        text,
    )

    def fix_composite(m: re.Match) -> str:
        line2 = m.group(2)
        if len(line2) != 44:
            return m.group(0)
        return m.group(1) + composite_of(line2) + m.group(4)

    text = re.sub(
        r'(pe\.composite_digit\(\s*"([^"]+)"\s*\)\s*==\s*")(\d)(")',
        fix_composite,
        text,
    )

    # ------------------------------------------------------------------
    # 7. Filenames, message ids and temp paths.
    # ------------------------------------------------------------------
    text = re.sub(r"3EB[0-9A-F]{18}", "FIXTURE0000000001", text)
    text = re.sub(r"\d+ [A-Za-z]+ [A-Za-z]+ MAG\.pdf", "fixture-passport-01.pdf", text)
    text = re.sub(r"/tmp/stage1out/[A-Za-z_]+_front\.jpg",
                  "/tmp/stage1out/front.jpg", text)

    # ------------------------------------------------------------------
    # 8. Assertion labels that quote the old surnames in prose.
    # ------------------------------------------------------------------
    for real, syn in NAMES.items():
        text = text.replace(f'"composite digit for the {real} line 2"',
                            '"composite digit for the synthetic line 2"')

    return text


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    map_path = os.environ.get("SCRUB_MAP", "")
    args = sys.argv[1:]
    if "--map" in args:
        i = args.index("--map")
        map_path = args[i + 1]
        del args[i:i + 2]

    if not map_path or not load_map(map_path):
        print("ERROR: no scrub map loaded.")
        print("  The map holds the real identifiers, so it lives outside the")
        print("  repository. Point at it with --map <path> or SCRUB_MAP=<path>.")
        return 1

    changed = 0
    for path in args:
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        scrubbed = scrub(original)
        if scrubbed != original:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(scrubbed)
            changed += 1
            print(f"  scrubbed {path}")
        else:
            print(f"  unchanged {path}")
    print(f"{changed} file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
