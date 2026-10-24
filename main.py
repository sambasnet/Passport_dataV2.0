import os
import sys
import re
import csv
from passporteye import read_mrz
from passporteye.mrz.image import BoxToMRZ
from passporteye.mrz.text import MRZ
from passporteye.util.ocr import ocr
from datetime import datetime, timedelta
import warnings
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

# Avoid Windows Unicode encoding crashes for emoji console output
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

warnings.filterwarnings("ignore", category=FutureWarning)

console = Console()

# Point Tesseract to local tessdata containing ocrb.traineddata (OCR-B MRZ font)
BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
TESSDATA_DIR = os.path.join(BASE_DIR, 'tessdata')
USE_OCRB = os.path.exists(os.path.join(TESSDATA_DIR, 'ocrb.traineddata'))
if USE_OCRB:
    os.environ['TESSDATA_PREFIX'] = TESSDATA_DIR

# Patch BoxToMRZ so 180-degree rotated MRZ boxes are still flipped when using
# ocrb.traineddata (which only outputs A-Z, 0-9, and '<', never '>').
_orig_box_to_mrz_call = BoxToMRZ.__call__


def _patched_box_to_mrz_call(self, box, img, img_small, scale_factor):
    roi, text, mrz = _orig_box_to_mrz_call(self, box, img, img_small, scale_factor)
    if not mrz.valid and '<' not in text:
        roi_flipped = roi[::-1, ::-1]
        text_flipped = ocr(roi_flipped, extra_cmdline_params=self.extra_cmdline_params)
        mrz_flipped = MRZ.from_ocr(text_flipped)
        if mrz_flipped.valid_score > mrz.valid_score:
            mrz_flipped.aux['method'] = 'direct(flipped)'
            return roi_flipped, text_flipped, mrz_flipped
    return roi, text, mrz


BoxToMRZ.__call__ = _patched_box_to_mrz_call


def read_mrz_from_file(file_name):
    extra_params = '-l ocrb' if USE_OCRB else ''
    mrz = read_mrz(file_name, extra_cmdline_params=extra_params)
    return None if mrz is None else mrz.to_dict()


def format_date(date_str, is_birth_date=False):
    if not date_str or date_str == 'N/A':
        return 'N/A'
    try:
        if len(date_str) == 6 and date_str.isdigit():
            date_obj = datetime.strptime(date_str, '%y%m%d')
            if is_birth_date:
                # If DOB parsed into the future, subtract 100 years
                current_year = datetime.now().year
                if date_obj.year > current_year:
                    date_obj = date_obj.replace(year=date_obj.year - 100)
            return date_obj.strftime('%d-%m-%Y')
        else:
            return 'N/A'
    except Exception as e:
        console.print(f"[red]Error formatting date {date_str}: {e}[/red]")
        return 'N/A'


def calculate_issue_date(expiry_str):
    if not expiry_str or expiry_str == 'N/A':
        return 'N/A'
    try:
        expiry = datetime.strptime(expiry_str, '%d-%m-%Y')
        try:
            issue = expiry.replace(year=expiry.year - 10)
        except ValueError:
            # Handles February 29th expiry leap year edge case
            issue = expiry.replace(year=expiry.year - 10, day=28)
        issue = issue + timedelta(days=1)
        return issue.strftime('%d-%m-%Y')
    except Exception as e:
        console.print(f"[red]Error calculating issue date from {expiry_str}: {e}[/red]")
        return 'N/A'


def clean_file_name(file_name):
    """Extract first alphanumeric code from filename"""
    if not isinstance(file_name, str):
        return 'N/A'
    match = re.search(r"[A-Za-z0-9]+", file_name)
    return match.group(0) if match else 'N/A'


def clean_passport_number(pn):
    """Clean passport: alphanumerics only, uppercase"""
    if not isinstance(pn, str):
        return 'N/A'
    pn = pn.strip().replace('<', '')
    cleaned = re.sub(r"[^A-Za-z0-9]", "", pn).upper()
    return cleaned if cleaned else 'N/A'


def squash_repeats(name):
    """Collapse repeated letters (KKKKK -> KK)"""
    if not isinstance(name, str):
        return 'N/A'
    # Collapse 3+ same letters to 2
    name = re.sub(r"(.)\1{2,}", r"\1\1", name)
    # Remove known garbage patterns
    name = re.sub(r"\b(KK|SS|XS|EK)+$", "", name, flags=re.IGNORECASE)
    return name.strip() if name.strip() else 'N/A'


def save_to_csv(data, output_file):
    fieldnames = [
        'File', 'Passport Number', 'Given Name', 'Surname',
        'Nationality', 'Gender', 'Date of Birth',
        'Issue Date', 'Expiration Date'
    ]

    # Use 'utf-8-sig' for seamless compatibility with Microsoft Excel on Windows
    with open(output_file, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for item in data:
            dob = format_date(item.get('date_of_birth', 'N/A'), is_birth_date=True)
            exp = format_date(item.get('expiration_date', 'N/A'), is_birth_date=False)
            issue_date = calculate_issue_date(exp)

            writer.writerow({
                'File': clean_file_name(item['file_name']),
                'Passport Number': clean_passport_number(item.get('number', 'N/A')),
                'Given Name': squash_repeats(item.get('names', 'N/A')),
                'Surname': squash_repeats(item.get('surname', 'N/A')),
                'Nationality': item.get('nationality', 'N/A'),
                'Gender': item.get('sex', 'N/A'),
                'Date of Birth': dob,
                'Issue Date': issue_date,
                'Expiration Date': exp
            })


def main():
    folder_path = 'Files'
    data_to_save = []

    if not os.path.exists(folder_path):
        console.print("[red]❌ Folder path does not exist.[/red]")
        return

    image_files = [
        f for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f)) and f.lower().endswith(('.jpg', '.jpeg'))
    ]

    if not image_files:
        console.print("[yellow]⚠️ No image files found in folder.[/yellow]")
        return

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("[cyan]Processing images...", total=len(image_files))

        for file_name in image_files:
            mrz_data = read_mrz_from_file(os.path.join(folder_path, file_name))
            if mrz_data is not None:
                mrz_data['file_name'] = file_name
                data_to_save.append(mrz_data)
            else:
                progress.console.print(f"[yellow]Warning: MRZ data could not be extracted from {file_name}[/yellow]")
            progress.update(task, advance=1)

    if data_to_save:
        with console.status("[bold green]Saving cleaned passport data...[/bold green]", spinner="dots"):
            save_to_csv(data_to_save, 'Cleaned_Passport_Data.csv')
        console.print(f"[green]✅ {len(data_to_save)} passports saved to Cleaned_Passport_Data.csv[/green]")
    else:
        console.print("[yellow]⚠️ No valid MRZ data found.[/yellow]")


if __name__ == '__main__':
    main()
