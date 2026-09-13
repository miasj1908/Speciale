"""
Udtraekker narrative tekst fra equity report PDFs og gemmer som JSON.
Opretter ogsaa combined JSON filer med baade CF model data og PDF tekst.

Struktur:
    json_filer/                    <- eksisterer allerede (CF model data)
    json_filer_pdf/                <- ny (equity report tekst)
    json_filer_combined/           <- ny (CF model + equity report)

Navngivning:
    NAS_AAPL_20191031.json         <- CF model
    NAS_AAPL_20191031_pdf.json     <- equity report
    NAS_AAPL_20191031_combined.json <- begge

Krav:
    pip install pdfplumber pandas
"""

import pandas as pd
import pdfplumber
import os
import re
import json
from datetime import datetime

MATCHEDE_PAR_STI    = r"C:\Users\miasj\PythonProjects\matchede_par_final.csv"
JSON_CF_MAPPE       = r"C:\Users\miasj\PythonProjects\json_filer"
JSON_PDF_MAPPE      = r"C:\Users\miasj\PythonProjects\json_filer_pdf"
JSON_COMBINED_MAPPE = r"C:\Users\miasj\PythonProjects\json_filer_combined"

LOKAL_ROD_STI = r"C:\Users\miasj\CBS - Copenhagen Business School\Ulrikke Hansen - Equity Reports"
SHAREPOINT_PRAEFIKS_RAPPORT = "https://studentcbs-my.sharepoint.com/personal/ctl_acc_cbs_dk/Documents"


def konverter_rapport_sti(sharepoint_sti):
    """Konverterer SharePoint URL til lokal sti for equity reports."""
    if pd.isna(sharepoint_sti):
        return None
    sti = str(sharepoint_sti).strip()
    if sti.startswith("http"):
        # Udtræk den relative del efter /Documents/
        match = re.search(r'/Documents/(.+)', sti)
        if match:
            relativ = match.group(1).replace("/", "\\")
            # Find rod-mappen baseret paa første del af stien
            if 'Ulrikke' in sti or 'Equity' in sti:
                sti = os.path.join(LOKAL_ROD_STI, relativ)
            else:
                sti = sti.replace(SHAREPOINT_PRAEFIKS_RAPPORT, 
                                  r"C:\Users\miasj\CBS - Copenhagen Business School")
                sti = sti.replace("/", "\\")
    return sti


def udtræk_narrative_tekst(pdf_sti):
    """
    Udtraekker narrative tekst fra en Morningstar equity report PDF.
    Fokuserer paa analytikerens perspektiv og kvalitative vurdering.
    """
    if not os.path.exists(pdf_sti):
        return None

    try:
        fuld_tekst = ""
        with pdfplumber.open(pdf_sti) as pdf:
            for side in pdf.pages:
                tekst = side.extract_text()
                if tekst:
                    fuld_tekst += tekst + "\n\n"

        if not fuld_tekst.strip():
            return None

        # Udtræk narrative sektioner
        narrative = udtræk_narrative_sektioner(fuld_tekst)

        return {
            "full_report_text": fuld_tekst[:50000],  # Max 50.000 tegn
            "narrative_text": narrative,
            "antal_tegn": len(fuld_tekst),
            "antal_sider": len(pdf.pages) if pdf else 0,
        }

    except Exception as e:
        return None


def udtræk_narrative_sektioner(tekst):
    """
    Udtraekker de narrative sektioner fra rapporten.
    Inkluderer: Analyst's Perspective, Investment Thesis,
    Economic Moat, Financial Outlook, Risk & Uncertainty.
    """
    narrative_dele = []

    # Sektioner vi vil fange
    sektioner = [
        "Analyst's Perspective",
        "Analyst Note",
        "Investment Thesis",
        "Economic Moat",
        "Moat Trend",
        "Financial Outlook",
        "Risk and Uncertainty",
        "Bulls Say",
        "Bears Say",
        "Key Investment Considerations",
        "Competitive Advantage Period",
        "Management and Stewardship",
    ]

    linjer = tekst.split('\n')
    i = 0
    aktiv_sektion = None
    aktiv_tekst = []

    while i < len(linjer):
        linje = linjer[i].strip()

        # Tjek om vi starter en ny sektion
        for sektion in sektioner:
            if sektion.lower() in linje.lower() and len(linje) < 100:
                # Gem forrige sektion
                if aktiv_sektion and aktiv_tekst:
                    narrative_dele.append(f"## {aktiv_sektion}\n" + '\n'.join(aktiv_tekst).strip())

                aktiv_sektion = sektion
                aktiv_tekst = []
                break
        else:
            # Tilføj linje til aktiv sektion
            if aktiv_sektion and linje:
                # Stop ved tabel-lignende indhold (mange tal i træk)
                if re.search(r'(\d+\s+){5,}', linje):
                    pass  # Skip talrækker
                elif len(linje) > 10:  # Skip meget korte linjer
                    aktiv_tekst.append(linje)

        i += 1

    # Gem sidste sektion
    if aktiv_sektion and aktiv_tekst:
        narrative_dele.append(f"## {aktiv_sektion}\n" + '\n'.join(aktiv_tekst).strip())

    if narrative_dele:
        return '\n\n'.join(narrative_dele)
    else:
        # Fallback: returner de første 5.000 tegn af teksten
        return tekst[:5000]


def udtræk_ticker_dato(cf_fil):
    """Udtraekker ticker og dato fra CF model filnavn."""
    m = re.match(r'^(.+)_CashFlowModel_(\d{8})\.xls$', str(cf_fil))
    if m:
        return m.group(1), m.group(2)
    return None, None


def koer():
    """Hovedfunktion."""

    # Opret output mapper
    os.makedirs(JSON_PDF_MAPPE, exist_ok=True)
    os.makedirs(JSON_COMBINED_MAPPE, exist_ok=True)

    print("Indlaeser matchede par...")
    df = pd.read_csv(MATCHEDE_PAR_STI, sep=';', encoding='utf-8-sig', on_bad_lines='skip')
    print(f"Antal par: {len(df)}")

    # Hent eksisterende CF JSON filer
    cf_json_filer = set(os.listdir(JSON_CF_MAPPE))
    print(f"CF JSON filer: {len(cf_json_filer)}")

    succes_pdf = 0
    succes_combined = 0
    fejl_pdf = 0
    fejl_sti = 0

    for idx, raekke in df.iterrows():
        if idx % 100 == 0:
            print(f"Behandler {idx}/{len(df)}: {raekke['cf_fil']}")

        cf_fil = raekke['cf_fil']
        rapport_fil = raekke['rapport_fil']
        rapport_mappe = str(raekke.get('rapport_mappe', '')).strip()

        # Udtræk ticker og dato fra CF filnavn
        ticker, dato = udtræk_ticker_dato(cf_fil)
        if not ticker or not dato:
            fejl_sti += 1
            continue

        # Byg JSON filnavne
        json_navn = f"{ticker}_{dato}"
        cf_json_navn = f"{json_navn}.json"
        pdf_json_navn = f"{json_navn}_pdf.json"
        combined_json_navn = f"{json_navn}_combined.json"

        # Tjek om CF JSON eksisterer
        if cf_json_navn not in cf_json_filer:
            fejl_sti += 1
            continue

        # Find PDF sti
        if rapport_mappe.startswith("http"):
            # Konverter SharePoint URL
            rapport_mappe_lokal = konverter_rapport_sti(rapport_mappe)
        else:
            rapport_mappe_lokal = rapport_mappe

        if not rapport_mappe_lokal:
            fejl_sti += 1
            continue

        pdf_sti = os.path.join(rapport_mappe_lokal, str(rapport_fil))

        # Udtræk PDF tekst
        pdf_data = udtræk_narrative_tekst(pdf_sti)

        if pdf_data is None:
            fejl_pdf += 1
            # Gem tom PDF JSON
            pdf_json = {
                "ticker": ticker,
                "dato": dato,
                "cf_model_fil": cf_fil,
                "rapport_fil": str(rapport_fil),
                "fejl": "PDF ikke fundet eller kunne ikke laeses",
                "narrative_text": None,
                "full_report_text": None,
            }
        else:
            # Gem PDF JSON
            pdf_json = {
                "ticker": ticker,
                "dato": dato,
                "cf_model_fil": cf_fil,
                "rapport_fil": str(rapport_fil),
                "narrative_text": pdf_data["narrative_text"],
                "full_report_text": pdf_data["full_report_text"],
                "antal_tegn": pdf_data["antal_tegn"],
                "antal_sider": pdf_data["antal_sider"],
            }
            succes_pdf += 1

        # Gem PDF JSON fil
        pdf_json_sti = os.path.join(JSON_PDF_MAPPE, pdf_json_navn)
        with open(pdf_json_sti, 'w', encoding='utf-8') as f:
            json.dump(pdf_json, f, ensure_ascii=False, indent=2)

        # Byg combined JSON
        try:
            cf_json_sti = os.path.join(JSON_CF_MAPPE, cf_json_navn)
            with open(cf_json_sti, 'r', encoding='utf-8') as f:
                cf_data = json.load(f)

            combined = {**cf_data}
            combined["equity_report"] = {
                "rapport_fil": str(rapport_fil),
                "narrative_text": pdf_json.get("narrative_text"),
                "full_report_text": pdf_json.get("full_report_text"),
            }

            combined_sti = os.path.join(JSON_COMBINED_MAPPE, combined_json_navn)
            with open(combined_sti, 'w', encoding='utf-8') as f:
                json.dump(combined, f, ensure_ascii=False, indent=2)

            succes_combined += 1

        except Exception as e:
            print(f"  FEJL combined: {cf_json_navn}: {e}")

    print("\n" + "="*50)
    print("FAERDIG")
    print("="*50)
    print(f"PDF udtrukket:     {succes_pdf}")
    print(f"PDF fejl:          {fejl_pdf}")
    print(f"Sti fejl:          {fejl_sti}")
    print(f"Combined oprettet: {succes_combined}")
    print(f"\nGemt i:")
    print(f"  PDF:      {JSON_PDF_MAPPE}")
    print(f"  Combined: {JSON_COMBINED_MAPPE}")


if __name__ == "__main__":
    start = datetime.now()
    koer()
    slut = datetime.now()
    minutter = (slut - start).seconds // 60
    sekunder = (slut - start).seconds % 60
    print(f"\nTid brugt: {minutter} minutter og {sekunder} sekunder")
