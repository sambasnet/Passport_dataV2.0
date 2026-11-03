"""Independently verify the composite check digit, per ICAO Doc 9303 Part 4
section 4.2.4, against the official Appendix A example and the two real
passports.

The three claims being tested:
  1. the composite input is the LOWER LINE ONLY
  2. the composite digit lives at line2[43], not line2[42]
  3. the ICAO weights cycle with period 3, so zip() truncation is a bug
"""
import random
import string
import sys

# --- the CURRENT (broken) implementation -------------------------------
W10 = [7, 3, 1, 7, 3, 1, 7, 3, 1, 7]
W7 = [7, 3, 1, 7, 3, 1]


def check_digit_broken(chars, weights):
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


def composite_broken(line1, line2):
    composite = (
        line1[0:10] + line1[13:20] + line2[0:10] + line2[13:20] + line2[21:43]
    )
    return check_digit_broken(composite, W10)


# --- the FIXED implementation -----------------------------------------
W3 = [7, 3, 1]


def check_digit_fixed(chars, weights):
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


def composite_input(line2):
    return line2[0:10] + line2[13:20] + line2[21:43]


def composite_fixed(line2):
    return check_digit_fixed(composite_input(line2), W3)


CASES = [
    ("ICAO Appendix A",
     "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
     "L898902C36UT07408122F2504155184226<<<<<<<<18"),
    ("FAMBB (real)", None,
     "P9000002<8IND8002217M35042332070912943625<02"),
    ("FAMAA (real)", None,
     "P4000001<0IND7307236M34120421070426541824<06"),
]

print("=== claim 3: zip() truncation ===")
long_input = composite_input(CASES[0][2])
print(f"composite input length: {len(long_input)}")
print(f"zip() with a 10-element weight list scores only {min(len(long_input), 10)} characters")
print()

print("=== the two implementations on the ground-truth cases ===")
for name, l1, l2 in CASES:
    got = composite_fixed(l2)
    want = l2[43]
    print(f"{name}:")
    print(f"  composite input = {composite_input(l2)}")
    print(f"  computed (fixed) = {got}   line2[43] = {want}   "
          f"{'MATCH' if got == want else 'MISMATCH'}")
    print(f"  computed (broken) = {composite_broken(l1 or 'P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<', l2)}"
          f"   line2[42] = {l2[42]}")

print()
print("=== the ICAO Appendix A example, all four check digits ===")
l1a = CASES[0][1]
l2a = CASES[0][2]
print(f"  passport_no : {check_digit_fixed(l2a[0:9], W7 if False else [7,3,1,7,3,1,7])} vs {l2a[9]}")
print(f"  dob         : {check_digit_fixed(l2a[13:19], W7)} vs {l2a[19]}")
print(f"  expiry      : {check_digit_fixed(l2a[21:27], W7)} vs {l2a[27]}")
print(f"  composite   : {composite_fixed(l2a)} vs {l2a[43]}")

print()
print("=== negative control: random line-2 strings must NOT score 4/4 ===")
random.seed(20261103)
alphabet = string.ascii_uppercase + string.digits + "<"
false_pos = 0
N = 20000
for _ in range(N):
    l2 = "".join(random.choice(alphabet) for _ in range(44))
    pn = check_digit_fixed(l2[0:9], [7, 3, 1, 7, 3, 1, 7]) == l2[9]
    dob = check_digit_fixed(l2[13:19], W7) == l2[19]
    exp = check_digit_fixed(l2[21:27], W7) == l2[27]
    comp = composite_fixed(l2) == l2[43]
    if pn and dob and exp and comp:
        false_pos += 1
print(f"  {false_pos}/{N} random 44-char line-2 strings scored 4/4")
print(f"  expected ~0 (a 1-in-10000 chance per string)")

print()
print("=== does the composite separate the 37 ambiguous candidates? ===")
# The FAMAA repair space: 37 pairs all satisfy the three per-field digits.
L1_43 = "<INDFAMAA<<GIVAAA<<<<<<<<<<<<<<<<<<<<<<<<<<"
L2_43 = "4000001<0IND7307236M34120421070426541824<06"
ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
sys.path.insert(0, "/home/hermes/.hermes/kanban/workspaces/t_8047a784/src")


def repair_insertions(cand):
    out = []
    for pos in range(len(cand) + 1):
        for ch in ALPHA:
            trial = cand[:pos] + ch + cand[pos:]
            if len(trial) == 44:
                out.append(trial)
    return out


l1_fixes = [t for t in repair_insertions(L1_43) if t[0:2] == "P<"]
l2_fixes = repair_insertions(L2_43)

three_of_four = []
four_of_four = []
for l1 in l1_fixes:
    for l2 in l2_fixes:
        pn = check_digit_fixed(l2[0:9], [7, 3, 1, 7, 3, 1, 7]) == l2[9]
        dob = check_digit_fixed(l2[13:19], W7) == l2[19]
        exp = check_digit_fixed(l2[21:27], W7) == l2[27]
        comp = composite_fixed(l2) == l2[43]
        n = sum((pn, dob, exp, comp))
        if n >= 3:
            three_of_four.append((l2, n))
        if n == 4:
            four_of_four.append(l2)

print(f"  candidates scoring >=3/4 : {len(three_of_four)}")
print(f"  candidates scoring  4/4  : {len(four_of_four)}")
for l2 in four_of_four:
    print(f"     4/4 -> passport_no='{l2[0:9].replace('<','')}'")
