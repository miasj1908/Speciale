# Koden henter dato for alle equity rapporter og gemmer i en Excelfil
import os
import re
import pandas as pd
from datetime import datetime
from pypdf import PdfReader

folder_path = r"C:\Users\miasj\CBS - Copenhagen Business School\Ulrikke Hansen - Equity Reports"

month_pattern = r"(Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)"

def extract_date_from_filename(filename):
    name = os.path.splitext(filename)[0]
    matches = []

    # YYYY_MM_DD
    for match in re.finditer(r"\d{4}_\d{2}_\d{2}", name):
        try:
            parsed = datetime.strptime(match.group(), "%Y_%m_%d")
            matches.append((match.start(), parsed))
        except ValueError:
            pass

    # YYYY-MM-DD
    for match in re.finditer(r"\d{4}-\d{2}-\d{2}", name):
        try:
            parsed = datetime.strptime(match.group(), "%Y-%m-%d")
            matches.append((match.start(), parsed))
        except ValueError:
            pass

    # YYYY.MM.DD
    for match in re.finditer(r"\d{4}\.\d{2}\.\d{2}", name):
        try:
            parsed = datetime.strptime(match.group(), "%Y.%m.%d")
            matches.append((match.start(), parsed))
        except ValueError:
            pass

    # Month Day Year, fx May_06,_2021
    for match in re.finditer(rf"{month_pattern}[_\-\s]+(\d{{1,2}}),?[_\-\s]+(\d{{4}})", name, flags=re.IGNORECASE):
        month, day, year = match.groups()
        try:
            parsed = datetime.strptime(f"{month} {day} {year}", "%b %d %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(f"{month} {day} {year}", "%B %d %Y")
            except ValueError:
                continue
        matches.append((match.start(), parsed))

    # DayMonthYear, fx 03May2019
    for match in re.finditer(rf"(\d{{1,2}}){month_pattern}(\d{{4}})", name, flags=re.IGNORECASE):
        day, month, year = match.groups()
        try:
            parsed = datetime.strptime(f"{day} {month} {year}", "%d %b %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
            except ValueError:
                continue
        matches.append((match.start(), parsed))

    # Day Month Year, fx 21 Mar 2016
    for match in re.finditer(rf"(\d{{1,2}})[_\-\s\.]+{month_pattern}[,_\-\s\.]+(\d{{4}})", name, flags=re.IGNORECASE):
        day, month, year = match.groups()
        try:
            parsed = datetime.strptime(f"{day} {month} {year}", "%d %b %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
            except ValueError:
                continue
        matches.append((match.start(), parsed))

    if matches:
        matches.sort(key=lambda x: x[0])  # sidste dato i filnavnet
        return matches[-1][1].strftime("%Y-%m-%d")

    return None


def extract_date_from_pdf(pdf_path, max_pages=3):
    """
    Leder i PDF-teksten efter:
    - Research as of 13 May 2013
    - Report as of 13 May 2013
    """
    try:
        reader = PdfReader(pdf_path)
        text = ""

        for page in reader.pages[:max_pages]:
            page_text = page.extract_text()
            if page_text:
                text += "\n" + page_text

        # Ryd lidt op i whitespace
        text = re.sub(r"\s+", " ", text)

        patterns = [
            r"Research as of\s+(\d{1,2}\s+(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\s+\d{4})",
            r"Report as of\s+(\d{1,2}\s+(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)\s+\d{4})"
        ]

        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                date_str = match.group(1)
                try:
                    parsed = datetime.strptime(date_str, "%d %b %Y")
                except ValueError:
                    parsed = datetime.strptime(date_str, "%d %B %Y")
                return parsed.strftime("%Y-%m-%d")

    except Exception:
        return None

    return None


data = []

for root, dirs, files in os.walk(folder_path):
    for file in files:
        if file.lower().endswith(".pdf"):
            full_path = os.path.join(root, file)

            report_date = extract_date_from_filename(file)
            date_source = "filename"

            if report_date is None:
                report_date = extract_date_from_pdf(full_path)
                if report_date is not None:
                    date_source = "pdf_text"
                else:
                    date_source = None

            rel_path = os.path.relpath(root, folder_path)
            path_parts = rel_path.split(os.sep)

            sector = path_parts[0] if len(path_parts) >= 1 else None
            company_folder = path_parts[1] if len(path_parts) >= 2 else os.path.basename(root)

            data.append({
                "sector": sector,
                "folder": root,
                "company_folder": company_folder,
                "file_name": file,
                "report_date": report_date,
                "date_source": date_source
            })

df = pd.DataFrame(data)
df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")

print(df.head(20))
print("\nAntal PDF-filer:", len(df))
print("Antal filer med dato:", df["report_date"].notna().sum())
print("Antal filer uden dato:", df["report_date"].isna().sum())

missing_df = df[df["report_date"].isna()].copy()

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_path_all = fr"C:\Users\miasj\PythonProjects\equity_reports_{timestamp}.xlsx"
output_path_missing = fr"C:\Users\miasj\PythonProjects\equity_reports_missing_dates_{timestamp}.xlsx"

df.to_excel(output_path_all, index=False)
missing_df.to_excel(output_path_missing, index=False)

print(f"\nAlle filer gemt her: {output_path_all}")
print(f"Filer uden dato gemt her: {output_path_missing}")