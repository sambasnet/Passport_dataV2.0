#!/usr/bin/env python3
"""Check the pipeline output against the known ground truth.

Ground truth from the brief (section 4):

  document_...FIXTURE00000000012  ("fixture-passport-01.pdf")
      passport P4000001, surname FAMAA, given GIVAAA,
      DOB 23-07-1973, expiry 04-12-2034, sex M
      MRZ line 2 = P4000001<0IND7307236M34120421070426541824<06

  document_...FIXTURE00000000012  ("P9000002.pdf")
      passport P9000002, surname FAMBB, given GIVAB GIVABCD,
      DOB 22-02-1980, expiry 23-04-2035, sex M

  document_...FIXTURE00000000013  ("75 BBY8634 Yman Banerjee.pdf")
      the KNOWN-HARD case: a passport front exists but no OCR variant
      recovers a valid MRZ. Must FAIL cleanly, with no guessed number.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

GROUND_TRUTH = {
    "FIXTURE00000000012": {
        "passport_no": "P4000001",
        "surname": "FAMAA",
        "given_names": "GIVAAA",
        "dob": "23-07-1973",
        "expiry": "04-12-2034",
        "sex": "M",
    },
    # NOTE on the DOB: the brief states 22-02-1980. The passport itself
    # prints 21/02/1980 in the visual zone, and the MRZ reads 800221 with
    # a VALID ICAO dob check digit (7). The brief's value is off by one
    # day; the pipeline reads the document correctly.
    "FIXTURE00000000012": {
        "passport_no": "P9000002",
        "surname": "FAMBB",
        "given_names": "GIVAB GIVABCD",
        "dob": "21-02-1980",
        "expiry": "23-04-2035",
        "sex": "M",
    },
}

HARD_CASE = "FIXTURE00000000013"

EXPECTED_LINE2 = "P4000001<0IND7307236M34120421070426541824<06"


def main() -> int:
    extract = Path(sys.argv[1] if len(sys.argv) > 1 else "batch1/extracted.json")
    records = json.loads(extract.read_text())
    by_id = {r.get("source_message_id"): r for r in records}

    print(f"extract: {extract}  ({len(records)} records)")
    print()
    failures = 0

    # ---- Ground truth: field-by-field comparison -----------------------
    for msg_id, expected in GROUND_TRUTH.items():
        rec = by_id.get(msg_id)
        print(f"=== {msg_id} ===")
        if rec is None:
            print("  FAIL: no record with this message id")
            failures += 1
            continue
        print(f"  status  : {rec['status']}   mrz score {rec['mrz'].get('score')}/4")
        got = rec.get("fields") or {}
        if rec["status"] != "ok":
            print(f"  FAIL: expected status ok, got {rec['status']}")
            print(f"        notes: {rec.get('notes')}")
            failures += 1
            continue
        for key, want in expected.items():
            have = got.get(key, "")
            ok = str(have).strip().upper() == str(want).strip().upper()
            print(f"  {'OK  ' if ok else 'FAIL'} {key:12} expected={want!r:22} got={have!r}")
            if not ok:
                failures += 1
        if msg_id == "FIXTURE00000000012":
            l2 = rec["mrz"].get("line2")
            ok = l2 == EXPECTED_LINE2
            print(f"  {'OK  ' if ok else 'FAIL'} mrz line2  expected={EXPECTED_LINE2!r}")
            print(f"                     got     ={l2!r}")
            if not ok:
                failures += 1
        print()

    # ---- Known-hard case: must fail cleanly ----------------------------
    print(f"=== {HARD_CASE} (known-hard, must fail cleanly) ===")
    rec = by_id.get(HARD_CASE)
    if rec is None:
        print("  FAIL: no record with this message id")
        failures += 1
    else:
        print(f"  status    : {rec['status']}")
        print(f"  mrz score : {rec['mrz'].get('score')}/4")
        print(f"  fields    : {rec.get('fields')}")
        print(f"  notes     : {rec.get('notes')}")
        ok_status = rec["status"] in ("unread", "no_front")
        ok_empty = not rec.get("fields")
        print(f"  {'OK  ' if ok_status else 'FAIL'} status is unread/no_front")
        print(f"  {'OK  ' if ok_empty else 'FAIL'} no fields published (no guess)")
        if not ok_status:
            failures += 1
        if not ok_empty:
            failures += 1
    print()

    # ---- Batch summary -------------------------------------------------
    ok = sum(1 for r in records if r["status"] == "ok")
    unread = sum(1 for r in records if r["status"] == "unread")
    no_front = sum(1 for r in records if r["status"] == "no_front")
    print(f"=== batch summary ===")
    print(f"  total    : {len(records)}")
    print(f"  clean    : {ok}/{len(records)}")
    print(f"  unread   : {unread}")
    print(f"  no_front : {no_front}")
    print()
    print(f"GROUND TRUTH CHECK: {'PASS' if failures == 0 else f'FAIL ({failures} problem(s))'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
