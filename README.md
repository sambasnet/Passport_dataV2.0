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
