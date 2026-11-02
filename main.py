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
from rich.table import Table

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
    if not mrz.valid:
        roi_flipped = roi[::-1, ::-1]
        text_flipped = ocr(roi_flipped, extra_cmdline_params=self.extra_cmdline_params)
        mrz_flipped = MRZ.from_ocr(text_flipped)
        if not mrz_flipped.valid:
            text_flipped, mrz_flipped = self._try_larger_image(roi_flipped, text_flipped, mrz_flipped)
        if mrz_flipped.valid_score > mrz.valid_score:
            mrz_flipped.aux['method'] = 'direct(flipped)'
            return roi_flipped, text_flipped, mrz_flipped
    return roi, text, mrz


BoxToMRZ.__call__ = _patched_box_to_mrz_call


def is_valid_mrz_data(mrz_dict):
    """Verify that extracted MRZ data represents a genuinely readable passport."""
    if not mrz_dict:
        return False
    if mrz_dict.get('valid_score', 0) < 60:
        return False
    if not (mrz_dict.get('valid_date_of_birth') and mrz_dict.get('valid_expiration_date')):
        return False
    dob = format_date(mrz_dict.get('date_of_birth', 'N/A'), is_birth_date=True)
    exp = format_date(mrz_dict.get('expiration_date', 'N/A'), is_birth_date=False)
    if dob == 'N/A' or exp == 'N/A':
        return False
    if clean_passport_number(mrz_dict.get('number', 'N/A')) == 'N/A':
        return False
    return True


def read_mrz_from_file(file_name):
    try:
        extra_params = '-l ocrb' if USE_OCRB else ''
        mrz = read_mrz(file_name, extra_cmdline_params=extra_params)
        if mrz is None or mrz.mrz_type is None:
            return None
        mrz_dict = mrz.to_dict()
        return mrz_dict if is_valid_mrz_data(mrz_dict) else None
    except Exception:
        return None


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
    """Clean MRZ name field and strip trailing filler artifacts."""
    if not isinstance(name, str):
        return 'N/A'
    # In MRZ names, 2+ spaces represent '<<' (end of name / start of filler zone)
    name = re.split(r"\s{2,}", name.strip())[0]
    # Collapse 3+ same letters to 2
    name = re.sub(r"(.)\1{2,}", r"\1\1", name)
    # Remove known garbage patterns
    name = re.sub(r"\b(KK|SS|XS|EK)+$", "", name, flags=re.IGNORECASE)
    return name.strip() if name.strip() else 'N/A'


def clean_nationality(item):
    """Return 3-letter nationality code, falling back to issuing country code if corrupted."""
    nat = str(item.get('nationality', '')).strip().upper().replace('<', '')
    country = str(item.get('country', '')).strip().upper().replace('<', '')
    if country == 'IND' and nat != 'IND':
        return 'IND'
    return nat if (len(nat) == 3 and nat.isalpha()) else (country if len(country) == 3 and country.isalpha() else 'N/A')


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
                'Nationality': clean_nationality(item),
                'Gender': item.get('sex', 'N/A'),
                'Date of Birth': dob,
                'Issue Date': issue_date,
                'Expiration Date': exp
            })


def main():
    folder_path = 'Files'
    data_to_save = []
    failed_files = []

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
                failed_files.append(file_name)
                progress.console.print(f"[yellow]Warning: MRZ data could not be extracted from {file_name}[/yellow]")
            progress.update(task, advance=1)

    if data_to_save:
        with console.status("[bold green]Saving cleaned passport data...[/bold green]", spinner="dots"):
            save_to_csv(data_to_save, 'Cleaned_Passport_Data.csv')
        console.print(f"[green]✅ {len(data_to_save)}/{len(image_files)} passports saved to Cleaned_Passport_Data.csv[/green]")
    else:
        console.print("[yellow]⚠️ No valid MRZ data found.[/yellow]")

    if failed_files:
        console.print(f"\n[bold red]❌ Unable to extract data from {len(failed_files)} file(s):[/bold red]")
        table = Table(show_header=True, header_style="bold red")
        table.add_column("#", style="dim", width=4)
        table.add_column("File Code", style="yellow")
        table.add_column("File Name", style="white")
        for idx, fname in enumerate(failed_files, start=1):
            table.add_row(str(idx), clean_file_name(fname), fname)
        console.print(table)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        console.print(f"\n[bold red]❌ Unexpected error: {e}[/bold red]")
    finally:
        console.print("\n[dim]Press Enter to exit...[/dim]", end="")
        try:
            input()
        except EOFError:
            pass
