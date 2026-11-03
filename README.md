# Passport Data Extractor V2.0

A fast, accurate command-line utility that scans passport images (`.jpg`/`.jpeg`), extracts Machine Readable Zone (MRZ) data using **PassportEye** and **Tesseract OCR** (tuned with the ICAO 9303 **OCR-B** font model), cleans and formats all fields, and exports the results to an Excel-compatible CSV file (`Cleaned_Passport_Data.csv`).

---

## ✨ Features

- **High-Accuracy OCR-B Recognition**: Uses a bundled `tessdata/ocrb.traineddata` model specifically trained on the passport MRZ font (`OCR-B`), preventing `<` chevron filler misreads (`K`, `E`, `S`, `C`) common with standard English OCR models.
- **180° Orientation Recovery**: Automatically detects and flips upside-down MRZ regions even when using the strict OCR-B character set.
- **Smart Data Cleaning & Formatting**:
  - Extracts the alphanumeric reference code from each image filename.
  - Cleans passport numbers and strips filler characters (`<`).
  - Removes residual OCR repeat artifacts from Given Names and Surnames.
  - Parses 6-digit MRZ dates (`YYMMDD`) into `DD-MM-YYYY` with century disambiguation for birth dates.
  - Calculates the **Issue Date** automatically (`Expiration Date - 10 years + 1 day`), including leap-year (`29-02`) handling.
- **Strict MRZ Validation & Unextracted Files Summary**: Validates check digits, dates, and MRZ scores (`>= 60`) before saving so corrupted or unreadable images never insert invalid rows into `Cleaned_Passport_Data.csv`, and displays a formatted summary table of any unextracted files at the end of the run.
- **Excel-Ready CSV Export**: Outputs UTF-8 with BOM (`utf-8-sig`) so Microsoft Excel opens `Cleaned_Passport_Data.csv` cleanly on Windows.
- **Standalone Executable Support**: Can be run directly via Python or packaged into a standalone Windows executable (`main.exe` / `dist/main.exe`) with PyInstaller, pausing before exit so you can review the terminal summary.

---

## 📂 Project Structure

```text
Passport_dataV2.0/
├── Files/                        # Input folder for passport images (.jpg, .jpeg)
├── tessdata/
│   └── ocrb.traineddata          # OCR-B Tesseract model for MRZ recognition
├── dist/
│   └── main.exe                  # Standalone Windows executable (built via PyInstaller)
├── main.py                       # Main extraction & CSV cleaning script
├── main.spec                     # PyInstaller specification file
├── requirements.txt              # Python package dependencies
└── Cleaned_Passport_Data.csv     # Generated output CSV
```

---

## 🛠️ Prerequisites

1. **Tesseract OCR** must be installed on your system and available in your `PATH`:
   - Windows installer: [Tesseract at UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
   - Default path: `C:\Program Files\Tesseract-OCR\tesseract.exe`
2. **Python 3.10+** (if running from source).

---

## 🚀 Getting Started

### Option 1: Run the Standalone Executable (`main.exe`)

1. Place your passport images (`.jpg` or `.jpeg`) inside the `Files/` folder in the working directory.
2. Run the executable from PowerShell or Command Prompt:
   ```powershell
   .\dist\main.exe
   ```
3. Open the generated `Cleaned_Passport_Data.csv` file.

### Option 2: Run from Python Source

1. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
2. Place your passport images in the `Files/` directory.
3. Run the script:
   ```powershell
   python main.py
   ```

---

## 📊 Output Format (`Cleaned_Passport_Data.csv`)

| Column | Description | Example |
| :--- | :--- | :--- |
| **File** | First alphanumeric token from the image filename | `AHM1531` |
| **Passport Number** | Cleaned uppercase alphanumeric passport number | `W8448601` |
| **Given Name** | Extracted given name(s) | `PRITISH VIPINKUMAR` |
| **Surname** | Extracted surname | `TRIVEDI` |
| **Nationality** | 3-letter ISO country code | `IND` |
| **Gender** | Holder's sex (`M` / `F`) | `M` |
| **Date of Birth** | Formatted birth date (`DD-MM-YYYY`) | `15-12-1987` |
| **Issue Date** | Calculated issue date (`DD-MM-YYYY`) | `12-12-2022` |
| **Expiration Date** | Formatted expiry date (`DD-MM-YYYY`) | `11-12-2032` |

---

## 📦 Building the `.exe` with PyInstaller

To rebuild `dist/main.exe` after modifying `main.py`, run:

```powershell
python -m PyInstaller --noconfirm --onefile --add-data "tessdata;tessdata" --collect-data skimage --collect-submodules skimage.io._plugins --collect-submodules rich._unicode_data main.py
```

---

## 🐧 Linux port — passport extraction engine

_Branch `feature/linux-passport-extraction`. This section documents the
Linux-native engine added alongside the original Windows script above. The
Windows `.exe` workflow is unchanged and still the supported path on Windows._
A single Linux-runnable pipeline that takes a folder of traveller passport
PDFs and emits **validated passport data rows** plus a **per-file pass/fail
verdict**, so a passport that cannot be read is reported to a human instead
of silently dropped.

This is a port of Sam's two Windows scripts —
[`PDF-JPG_V2.0`](https://github.com/sambasnet/PDF-JPG_V2.0) (PDF → passport
page images) and
[`Passport_dataV2.0`](https://github.com/sambasnet/Passport_dataV2.0)
(images → MRZ → CSV) — merged into one script with the defects measured and
fixed.

---

### Quick start

```bash
# One document at a time
python3 src/passport_extract.py \
    --input  /path/to/passport-pdfs \
    --outdir /path/to/workdir \
    --tesseract /home/hermes/.hermes/cache/scratch/tstest/prefix/usr/bin/tesseract \
    --tessdata  /home/hermes/.hermes/cache/scratch/tstest/tessdata

# A batch, in parallel
python3 src/passport_extract_batch.py \
    --input  /path/to/passport-pdfs \
    --outdir /path/to/workdir \
    --workers 2
```

Outputs, in `--outdir`:

| File | What it is |
|---|---|
| `extracted.json` | the Stage 2 contract — one record per document, with the verdict |
| `Cleaned_Passport_Data.csv` | the clean rows only, in `Passport_dataV2.0`'s CSV format |
| `images/<name>_front.*` | the passport front page image |
| `images/<name>_back.*` | the passport back page image (when found) |

---

### Environment

Verified on this box (2026-11-03).

| Need | Where |
|---|---|
| Python | 3.11+ with `pymupdf`, `numpy`, `Pillow` |
| Tesseract | rootless install at `/home/hermes/.hermes/cache/scratch/tstest/prefix/` |
| `tessdata` | `/home/hermes/.hermes/cache/scratch/tstest/tessdata` — needs **both** `eng.traineddata` (keyword classification, visual zone) and `ocrb.traineddata` (the MRZ pass) |

The rootless Tesseract needs `LD_LIBRARY_PATH` set or it cannot load
`libtesseract.so.5`. The script sets it for you when it detects the
rootless prefix.

**No `sudo` is required and no system install is needed.** To install
Tesseract yourself, without root:

```bash
mkdir -p tessroot && cd tessroot
apt-get download tesseract-ocr libtesseract5 liblept5 libtesseract-dev \
    libjpeg62-turbo libpng16-16 libtiff6 libwebp7 libopenjp2-7 zlib1g
for f in *.deb; do dpkg -x "$f" .; done
# then: --tesseract $PWD/usr/bin/tesseract
#       --tessdata  <a dir holding eng.traineddata and ocrb.traineddata>
```

The script also works with a system Tesseract on `PATH` — just omit
`--tesseract`/`--tessdata`.

---

### What it does

```
PDF ──► Stage 1: find the passport front/back pages ──► page images
                                                        │
                                                        ▼
              Stage 2: read the MRZ, validate it jointly, confirm it
                                                        │
                                                        ▼
                       verdict + fields, or an honest "unread"
```

#### Stage 1 — find the passport pages

Each PDF page is **rendered** and OCR'd (Tesseract `--psm 3`), and scored
against two keyword sets:

* front: `REPUBLIC OF INDIA`, `GIVEN NAME`, `SURNAME`, `DATE OF BIRTH`,
  `PLACE OF BIRTH`, `DATE OF EXPIRY`, `NATIONALITY`, `PASSPORT NO`, `P<IND`
* back: `NAME OF FATHER`, `LEGAL GUARDIAN`, `NAME OF MOTHER`,
  `NAME OF SPOUSE`, `ADDRESS`, `OLD PASSPORT`, `FILE NO`, `PIN:`

A page is the front when it scores ≥2 front keywords and more front than
back keywords. Documents that are not passports (a CV, a hotel brochure) are
rejected.

The page image is saved at the PDF's own resolution, with **the codec the
PDF actually stores** — `extract_image()` reports it, and the file
extension follows.

#### Stage 2 — read the MRZ and prove it

1. **Harvest** candidate MRZ lines from a sliding window over the page,
   using OCR-B (`-l ocrb`). OCR-B is trained on the MRZ font and prevents
   the classic `<` → `K`/`E`/`S`/`C` misreads that corrupt check digits.
2. **Repair** lines that came back damaged — a clipped or mis-read glyph —
   using single-glyph insertion and substitution search.
3. **Score the PAIR**, not line 2 alone. Four ICAO 9303 check digits:
   passport number, DOB, expiry, and the **composite** digit. The
   composite covers positions 1-10, 14-20 and 22-43 of the *lower* line
   and lives at position 44 of that line (ICAO Doc 9303 Part 4,
   §4.2.4). It also covers the optional-data field, so a single wrong
   character anywhere in the data region breaks it.
4. **Confirm** against the printed passport number in the visual zone.

3/4 is accepted (the composite is optional-data-sensitive and genuinely
fails on some real documents); 2/4 or less is refused.

#### The visual zone is what makes this trustworthy

The check digits alone are **not** enough to identify a passport. On the
ground-truth document, 37 different passport numbers — including
`W0000000` and `000000A0` — all satisfy the three per-field check digits.
Picking one by score alone would publish a wrong passport number, which is
worse than publishing nothing.

So the printed passport number in the visual zone is read independently and
used to **select** the candidate, and to **rebuild** a line 2 whose
passport-number field was clipped or mis-read. If the two reads disagree,
nothing is published.

---

### The fixes over the upstream scripts

| # | Upstream behaviour | What this does | Measurement |
|---|---|---|---|
| 1 | keyword classification used `--psm 6` | `--psm 3` | on `FIXTURE00000000013`: psm 3 → front=5/back=0 (**front**); psm 6 → front=0/back=0 (**not a passport**) |
| 2 | when Tesseract was missing, `found.setdefault("front", candidates[0])` guessed and wrote `_front.jpg`/`_back.jpg` with no warning | the guess is removed: no image is written, the file is marked failed | with a bogus `TESSDATA_PREFIX`: `status=no_front`, `files written: []` |
| 3 | wrote the PDF's stored bytes to a `.jpg` filename (the codec is `png` for most xrefs on Sam's own PDFs) | the extension follows the reported codec | the corpus yields both `_front.jpg` and `_front.png`, each a real JPEG/PNG |
| 4 | `passporteye` with `valid_score >= 60` — measured garbage: `number=0000000<9`, `nationality=ND7`, `sex=3` | joint ICAO 9303 validation, no `passporteye` | the ground-truth MRZ now reads exactly `P0000000<9IND0000000M000000000000000000<00` |
| 5 | the MRZ was cropped at 0.60–0.80 of the page height — the **visual zone** | the MRZ band is **located** with Tesseract's TSV output, then cropped to | `FIXTURE00000000018` (a passport photographed on a bed) has its MRZ at 0.61–0.70 of the frame, not at the bottom |
| 6 | one line repaired at a time | both lines repaired jointly, and the pair is scored together | the clipped ground-truth MRZ (both lines one char short) is recovered exactly |
| 7 | given names taken as the **last** `<<`-segment — which is the trailing filler, so the name was lost | the first two segments: surname, then given names | `P<INDTESTNAME<<TESTNAME<<<<<<<<<<<<<<<<<<<<<<<<<<TESTNAME<<...` → surname `TESTNAME`, given `TESTNAME TESTNAME` |
| 8 | — | the composite check digit follows ICAO Doc 9303 Part 4 §4.2.4 (lower line only, position 44, period-3 weights) | the ICAO Appendix A example scores 4/4; 0 of 5000 random line-2 strings score 4/4 |
| 9 | — | a page must carry a passport marker (`REPUBLIC OF INDIA`, `PASSPORT NO`, `P<IND`, `<<<`) to be called a front | a CV in the corpus listing "Date of Birth" and "Nationality" scores front=2 but is rejected, so it is reported as "no passport", not as an unread passport |
| 10 | — | the nationality is normalised to an ISO alpha-3 code | ICAO excludes positions 11-13 from the composite, so a mis-read nationality (`1ND` for `IND`) passes a 4/4 score; the digit is mapped back to its letter |

#### Why the visual zone is load-bearing

The check digits alone do **not** identify a passport. On the ground-truth
document, 36 different passport numbers — including `W0000000` and
`000000A0` — all score 4/4, because that document's optional-data field is
clipped and the composite covers it.

So the printed passport number in the visual zone is read independently and
used to **select** the candidate, and to **rebuild** a line 2 whose
passport-number field was clipped or mis-read (one document has four
mis-read characters in that field — no single-glyph repair can reach the
truth). If the two reads disagree, nothing is published.

#### The acceptance bar is 4/4

With the composite computed correctly, a real TD3 MRZ scores 4/4 and a
random 44-character string scores 4/4 about one time in ten thousand.
3/4 is **refused**: it means the optional-data field or the composite is
wrong, and a wrong passport number in the master sheet is worse than a
missing row, because a missing row is visible and a wrong row is not.

---

### Fail-closed behaviour

The pipeline never publishes an unvalidated field.

* no passport front found → `status: "no_front"`, no fields
* MRZ below 3/4 → `status: "unread"`, no fields
* visual zone disagrees with every MRZ candidate → `status: "unread"`,
  no fields, and the reason is in `notes`

A missing row is visible; a wrong row is not.

---

### Output contract

`extracted.json` follows the Stage 2 contract in EDITH's
`references/exception-report.md`, so `exception_report.py` consumes it
directly:

```json
{
  "source_message_id": "FIXTURE00000000012",
  "original_filename": "fixture-passport-01.pdf",
  "file_sha256": "fixturehash...",
  "chat": "",
  "received_at": "2026-10-28T12:33:31",
  "status": "ok",
  "front_image": "/path/to/front.jpg",
  "back_image": "/path/to/back.jpg",
  "mrz": {
    "line1": "P<INDTESTNAME<<TESTNAME<<<<<<<<<<<<<<<<<<<<<<<<<<",
    "line2": "P0000000<9IND0000000M000000000000000000<00",
    "score": 3,
    "checks": ["passport_no", "dob", "expiry", "composite_BAD"]
  },
  "fields": {
    "passport_no": "P0000000", "surname": "TESTNAME", "given_names": "TESTNAME",
    "nationality": "IND", "sex": "M", "dob": "23-07-1973",
    "expiry": "04-12-2034", "issue_date": "05-12-2024",
    "issue_date_source": "derived"
  },
  "visual_zone": {"passport_no": "P0000000"},
  "notes": ["MRZ passport number confirmed against the printed number (P0000000)"]
}
```

`status` is `ok`, `unread` or `no_front`. Dates are `DD-MM-YYYY`.

`issue_date` is **derived** (expiry − 10 years + 1 day), never read — it is
flagged `derived` so the report raises `ISSUE_DATE_UNCONFIRMED`.

Two extra fields help the exception report tell apart "this document is not
a passport" from "this is a passport but I could not read it":

| Field | Meaning |
|---|---|
| `contains_passport_page` | `true` only if a passport-like page was seen in the document |
| `passport_likelihood` | the best front-keyword score over all pages (0 = nothing passport-like) |

A document with `contains_passport_page: false` needs **no traveller
request** — it is a brochure, a form or a CV, and asking that traveller to
resend a passport would be a false request.

---

### Tests

```bash
python3 test_mrz_logic.py       # ICAO arithmetic, repair, selection — no OCR
python3 verify_ground_truth.py <extracted.json>
python3 prove_fixes.py --pdf <passport.pdf>   # proves fixes 1 and 2
python3 prove_contract.py <extracted.json>    # proves the output contract
```

---

### Limits

* **Personal data.** The pipeline reads real passports. Keep the output in a
  local working directory. Never commit it to a repo, paste it into a chat,
  or write it into a shared spreadsheet.
* **The master spreadsheet is never written.** It is read by
  `exception_report.py`, not by this script.
* **Issue date is an inference**, not a reading.
* The composite check digit fails on some genuine documents — 3/4 is
  accepted, and the visual-zone cross-check is what carries the confidence.
