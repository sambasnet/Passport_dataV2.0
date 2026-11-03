#!/usr/bin/env python3
"""Unit tests for the MRZ repair, validation and selection logic.

These run without OCR, so they are fast and pin the exact behaviour the
live corpus depends on.

    python3 test_mrz_logic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import passport_extract as pe

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  OK   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILURES.append(name)


print("=== ICAO check digits ===")
L1 = "P<INDFAMAA<<GIVAAA<<<<<<<<<<<<<<<<<<<<<<<<<<"
L2 = "P4000001<0IND7307236M34120421070426541824<06"
check(
    "passport no check digit",
    pe.check_digit("P4000001<", pe.W10) == "0",
    pe.check_digit("P4000001<", pe.W10),
)
check(
    "dob check digit",
    pe.check_digit("730723", pe.W7) == "6",
    pe.check_digit("730723", pe.W7),
)
check(
    "expiry check digit",
    pe.check_digit("341204", pe.W7) == "2",
    pe.check_digit("341204", pe.W7),
)
check("filler is zero", pe.check_digit("<<<<<<<<<", pe.W10) == "0")

print()
print("=== the weights CYCLE (period 3), zip() would truncate ===")
# ICAO Doc 9303 Part 4: the repeating sequence is 7, 3, 1.
check(
    "check_digit cycles the weights over the whole string",
    pe.check_digit("ABCDEFGHIJ", pe.W3) == pe.check_digit("ABCDEFGHIJ", [7, 3, 1, 7, 3, 1, 7, 3, 1, 7]),
    f"{pe.check_digit('ABCDEFGHIJ', pe.W3)} vs {pe.check_digit('ABCDEFGHIJ', [7,3,1,7,3,1,7,3,1,7])}",
)
long_39 = "A" * 39
check(
    "a 39-char input is scored in full, not truncated to 10",
    pe.check_digit(long_39, pe.W3) == pe.check_digit(long_39, [7, 3, 1] * 13),
)

print()
print("=== composite check digit (ICAO Doc 9303 Part 4, section 4.2.4) ===")
# The composite covers positions 1-10, 14-20 and 22-43 of the LOWER line
# only, and lives at position 44.
check(
    "composite input is the lower line only",
    pe.composite_input(L2) == L2[0:10] + L2[13:20] + L2[21:43],
    pe.composite_input(L2),
)
check(
    "composite input excludes positions 11-13 and 21",
    L2[10:13] not in pe.composite_input(L2) and L2[20] not in pe.composite_input(L2),
)
check(
    "composite digit for the FAMAA line 2",
    pe.composite_digit(L2) == L2[43],
    f"computed {pe.composite_digit(L2)} expected {L2[43]}",
)
check(
    "composite digit for the FAMBB line 2",
    pe.composite_digit("P9000002<8IND8002217M35042332070912943625<02") == "2",
)
# The official ICAO Appendix A example.
ICAO_L1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
ICAO_L2 = "L898902C36UT07408122F2504155184226<<<<<<<<18"
check(
    "ICAO Appendix A example scores 4/4",
    pe.score_pair(ICAO_L1, ICAO_L2) == (4, ["passport_no", "dob", "expiry", "composite"]),
    str(pe.score_pair(ICAO_L1, ICAO_L2)),
)
check(
    "ICAO Appendix A composite digit",
    pe.composite_digit(ICAO_L2) == ICAO_L2[43],
    f"computed {pe.composite_digit(ICAO_L2)} expected {ICAO_L2[43]}",
)

print()
print("=== a random line 2 must not score 4/4 ===")
import random
import string
random.seed(20261103)
alphabet = string.ascii_uppercase + string.digits + "<"
false_pos = 0
for _ in range(5000):
    l2 = "".join(random.choice(alphabet) for _ in range(44))
    pn = pe.check_digit(l2[0:9], pe.W10) == l2[9]
    dob = pe.check_digit(l2[13:19], pe.W7) == l2[19]
    exp = pe.check_digit(l2[21:27], pe.W7) == l2[27]
    comp = pe.composite_digit(l2) == l2[43]
    if pn and dob and exp and comp:
        false_pos += 1
check("0 of 5000 random line-2 strings score 4/4", false_pos == 0, str(false_pos))

print()
print("=== joint scoring ===")
score, detail = pe.score_pair(L1, L2)
check("known-good pair scores 4/4", score == 4, f"{score} {detail}")
check("per-field digits all valid", all(pe.line2_field_checks(L2).values()))

score2, d2 = pe.score_pair("P<INDFAMBB<<GIVAB<GIVABCD<<<<<<<<<<<<<<<<<<<",
                           "P9000002<8IND8002217M35042332070912943625<02")
check("FAMBB pair scores 4/4", score2 == 4, f"{score2} {d2}")

print()
print("=== bad length / bad magic are rejected ===")
check("43-char line rejected", pe.score_pair(L1[:43], L2)[0] == 0)
check("wrong header rejected", pe.score_pair("X<INDFAMAA<<GIVAAA<<<<<<<<<<<<<<<<<<<<<<<<<<", L2)[0] == 0)

print()
print("=== field parsing ===")
f = pe.parse_fields(L1, L2)
check("surname FAMAA", f["surname"] == "FAMAA", f["surname"])
check("given names GIVAAA", f["given_names"] == "GIVAAA", f["given_names"])
check("passport no P4000001", f["passport_no"] == "P4000001", f["passport_no"])
check("nationality IND", f["nationality"] == "IND", f["nationality"])
check("sex M", f["sex"] == "M", f["sex"])

f2 = pe.parse_fields("P<INDFAMBB<<GIVAB<GIVABCD<<<<<<<<<<<<<<<<<<<",
                     "P9000002<8IND8002217M35042332070912943625<02")
check("multi-part given names kept whole", f2["given_names"] == "GIVAB GIVABCD", f2["given_names"])
check("surname FAMBB", f2["surname"] == "FAMBB", f2["surname"])

print()
print("=== the nationality is normalised (it is outside the composite) ===")
# ICAO excludes positions 11-13 from the composite, so a mis-read
# nationality passes a 4/4 score. `I` is routinely read as `1`.
check("1ND -> IND", pe.clean_nationality("1ND") == "IND", pe.clean_nationality("1ND"))
check("IND stays IND", pe.clean_nationality("IND") == "IND")
check("GBR stays GBR", pe.clean_nationality("GBR") == "GBR")
check("empty stays empty", pe.clean_nationality("") == "")
f3 = pe.parse_fields(
    "P<INDFAMCCCCCCCCCCC<<GIVXYZW<GIVQR<<<<<<<<<<",
    "P5000003<91ND7004225M35031112067440021925<84",
)
check("nationality from a mis-read line 2 is repaired", f3["nationality"] == "IND", f3["nationality"])

print()
print("=== dates ===")
check("dob 730723 -> 23-07-1973", pe.format_dob("730723") == "23-07-1973", pe.format_dob("730723"))
check("dob 800221 -> 21-02-1980", pe.format_dob("800221") == "21-02-1980", pe.format_dob("800221"))
check("expiry 341204 -> 04-12-2034", pe.format_expiry("341204") == "04-12-2034", pe.format_expiry("341204"))
check("expiry 350423 -> 23-04-2035", pe.format_expiry("350423") == "23-04-2035", pe.format_expiry("350423"))
check("issue date derived +1 day", pe.derive_issue_date("04-12-2034") == "05-12-2024", pe.derive_issue_date("04-12-2034"))
check("issue date from expiry 23-04-2035", pe.derive_issue_date("23-04-2035") == "24-04-2025", pe.derive_issue_date("23-04-2035"))

print()
print("=== joint repair recovers a clipped MRZ ===")
# The ground-truth document: the scan clips the leading character of both
# lines, so both come back 43 chars.
L1_43 = "<INDFAMAA<<GIVAAA<<<<<<<<<<<<<<<<<<<<<<<<<<"
L2_43 = "4000001<0IND7307236M34120421070426541824<06"
l1_pool, l2_pool = pe._repair_space({L1_43, L2_43})
check("line-1 repair reaches the canonical header", L1 in l1_pool)
check("line-2 repair reaches the true line 2", L2 in l2_pool)
best, all_valid = pe._search_pairs(l1_pool, l2_pool)
check("repaired pair validates at 4/4", best[2] == 4, str(best[2]))
check("many candidates tie on score (the ambiguity the VZ resolves)", len(all_valid) > 10, str(len(all_valid)))
nums = {c[1][0:9].replace("<", "") for c in all_valid}
check("the true passport number is among them", "P4000001" in nums)
check("but so are wrong ones (why the VZ is needed)", len(nums) > 1, str(sorted(nums)[:6]))

print()
print("=== visual zone selects the true candidate ===")
chosen, decision = pe.select_candidate(all_valid, {"P4000001": 37, "W4000001": 1})
check("a candidate is chosen", chosen is not None)
if chosen:
    check("the chosen one is the true number", chosen[1][0:9].replace("<", "") == "P4000001", chosen[1])
check("selection is recorded as visual-zone confirmed", decision.get("selection") == "visual_zone_confirmed", str(decision))

print()
print("=== visual zone mismatch publishes nothing ===")
chosen2, decision2 = pe.select_candidate(all_valid, {"Z9999999": 12})
check("no candidate chosen on mismatch", chosen2 is None)
check("mismatch is recorded", decision2.get("selection") == "mismatch", str(decision2))

print()
print("=== structural plausibility tie-break ===")
GOOD = "P9000002<8IND8002217M35042332070912943625<02"
BAD = "P9000002<81ND8002217135042332070912943625<02"
check("good line has ISO nationality", pe._pair_plausibility(L1, GOOD) == (1, 1, 1), str(pe._pair_plausibility(L1, GOOD)))
check("bad line scores worse", pe._pair_plausibility(L1, BAD) < pe._pair_plausibility(L1, GOOD), str(pe._pair_plausibility(L1, BAD)))

print()
print("=== the OCR-corrupted passport number is rebuilt from the visual zone ===")
# The FAMCCCCCCCCCCC document: line 2 OCR'd with 'F' for 'C' and several
# digit errors (F9300003 vs P5000003) - four mis-reads, so no single
# substitution repair can reach the truth.
L2_MUNI_TRUE = "P5000003<91ND7004225M35031112067440021925<84"
L2_MUNI_OCR = "F9300003<91ND7004225M35031112067440021925<84"
L1_MUNI = "P<INDFAMCCCCCCCCCCC<<GIVXYZW<GIVQR<<<<<<<<<<"

check(
    "the OCR line 2 does NOT validate on its own",
    pe.score_pair(L1_MUNI, L2_MUNI_OCR)[0] < 3,
    str(pe.score_pair(L1_MUNI, L2_MUNI_OCR)),
)
rebuilt = pe.build_line2_from_vz("P5000003", L2_MUNI_OCR)
check("the visual-zone number rebuilds line 2", rebuilt is not None)
if rebuilt:
    check("the rebuilt line 2 matches the truth", rebuilt == L2_MUNI_TRUE, rebuilt)
    s, d = pe.score_pair(L1_MUNI, rebuilt)
    check("the rebuilt pair validates 4/4", s == 4, f"{s} {d}")

print()
print("=== fail-closed: an unreadable document publishes nothing ===")
check("empty candidate set scores 0", pe.best_pair(set())[2] == 0)
check("garbage candidates score 0", pe.best_pair({"NOTAPASSPORTATALL", "XXXXXXXXXXXXXXXXXXXXXXXXXXXX"})[2] == 0)

print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
    sys.exit(1)
print("ALL CHECKS PASSED")
