"""
Udtraekker valuta og enhed fra cashflow modeller og opdaterer JSON filerne.
Bruger historiske_aar_tjek.csv som kilde til filstier.

Krav:
    pip install xlrd pandas
"""

import pandas as pd
import xlrd
import os
import re
import json
from datetime import datetime

HISTORISKE_AAR_STI = r"C:\Users\miasj\PythonProjects\historiske_aar_tjek.csv"
MATCHEDE_PAR_STI   = r"C:\Users\miasj\PythonProjects\matchede_par_final.csv"
JSON_MAPPE         = r"C:\Users\miasj\PythonProjects\json_filer"

LOKAL_ROD_STI = r"C:\Users\miasj\CBS - Copenhagen Business School\Caroline Thøisen Larsen - Data - Forecasting downloadet"
SHAREPOINT_PRAEFIKS = "https://studentcbs-my.sharepoint.com/personal/ctl_acc_cbs_dk/Documents/Data - Forecasting downloadet"


def konverter_sti(sharepoint_sti):
    if pd.isna(sharepoint_sti):
        return None
    sti = str(sharepoint_sti).strip()
    if sti.startswith("http"):
        sti = sti.replace(SHAREPOINT_PRAEFIKS, LOKAL_ROD_STI)
        sti = sti.replace("/", "\\")
        sti = sti.replace("Raadata", "Radata")
    return sti


def udtræk_valuta_enhed(filsti):
    valuta = None
    enhed = None

    try:
        wb = xlrd.open_workbook(filsti)

        inputs_navn = None
        for navn in ['Inputs', 'inputs', 'INPUTS']:
            if navn in wb.sheet_names():
                inputs_navn = navn
                break

        if not inputs_navn:
            return None, None

        ws = wb.sheet_by_name(inputs_navn)

        for row_idx in range(min(600, ws.nrows)):
            row = ws.row_values(row_idx)
            for col_idx, celle in enumerate(row):
                if not isinstance(celle, str):
                    continue
                celle_lower = celle.lower().strip()

                if not valuta and 'reporting currency' in celle_lower:
                    for naeste in range(col_idx + 1, min(col_idx + 8, len(row))):
                        v = str(row[naeste]).strip()
                        if v and v.lower() not in ('', '0', '0.0', 'nan', 'none'):
                            if len(v) > 15:
                                continue
                            valuta_match = re.match(r'^([A-Z]{2,6})', v.upper())
                            if valuta_match:
                                valuta = valuta_match.group(1)
                                break

                if not enhed and 'financial statement unit' in celle_lower:
                    for naeste in range(col_idx + 1, min(col_idx + 8, len(row))):
                        v = str(row[naeste]).strip().lower()
                        if 'million' in v:
                            enhed = 'Millions'
                            break
                        elif 'thousand' in v:
                            enhed = 'Thousands'
                            break
                        elif 'billion' in v:
                            enhed = 'Billions'
                            break

            if valuta and enhed:
                break

        # Fallback: de foerste 5 raekker
        if not valuta or not enhed:
            for row_idx in range(min(5, ws.nrows)):
                row = ws.row_values(row_idx)
                for celle in row:
                    if not isinstance(celle, str):
                        continue
                    if not valuta:
                        m = re.search(r'([A-Z]{3})\s+(Millions|Thousands|Billions)', celle, re.IGNORECASE)
                        if m:
                            valuta = m.group(1).upper()
                            enhed = m.group(2).capitalize()

    except Exception:
        pass

    return valuta, enhed


def opdater_json_filer():
    print("Indlaeser data...")
    df_hist = pd.read_csv(HISTORISKE_AAR_STI)
    df_match = pd.read_csv(MATCHEDE_PAR_STI, sep=';', encoding='utf-8-sig', on_bad_lines='skip')

    df = df_hist.merge(df_match[['cf_fil', 'cf_mappe']], on='cf_fil', how='left')

    filsti_lookup = {}
    for _, raekke in df.iterrows():
        cf_fil = raekke['cf_fil']
        cf_mappe = konverter_sti(raekke.get('cf_mappe', None))
        if cf_mappe and cf_fil:
            filsti_lookup[cf_fil] = os.path.join(cf_mappe, cf_fil)

    print(f"Filstier fundet: {len(filsti_lookup)}")

    json_filer = [f for f in os.listdir(JSON_MAPPE) if f.endswith('.json')]
    print(f"JSON filer: {len(json_filer)}")

    succes = 0
    ingen_sti = 0
    ingen_valuta = 0
    fejl = 0
    valuta_fordeling = {}
    enhed_fordeling = {}

    for idx, json_fil in enumerate(json_filer):
        if idx % 100 == 0:
            print(f"Behandler {idx}/{len(json_filer)}: {json_fil}")

        try:
            json_sti = os.path.join(JSON_MAPPE, json_fil)
            with open(json_sti, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cf_fil = data['cf_model_fil']

            if cf_fil not in filsti_lookup or not os.path.exists(filsti_lookup[cf_fil]):
                ingen_sti += 1
                data['valuta'] = 'Ukendt'
                data['enhed'] = 'Millions'
                with open(json_sti, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                continue

            filsti = filsti_lookup[cf_fil]
            valuta, enhed = udtræk_valuta_enhed(filsti)

            if not valuta:
                ingen_valuta += 1

            data['valuta'] = valuta if valuta else 'Ukendt'
            data['enhed'] = enhed if enhed else 'Millions'

            with open(json_sti, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            valuta_fordeling[data['valuta']] = valuta_fordeling.get(data['valuta'], 0) + 1
            enhed_fordeling[data['enhed']] = enhed_fordeling.get(data['enhed'], 0) + 1
            succes += 1

        except Exception as e:
            print(f"  FEJL: {json_fil}: {e}")
            fejl += 1

    print("\n" + "="*50)
    print("FAERDIG")
    print("="*50)
    print(f"Behandlet:      {succes}")
    print(f"Ingen filsti:   {ingen_sti}")
    print(f"Ingen valuta:   {ingen_valuta}")
    print(f"Fejl:           {fejl}")

    print("\n=== VALUTA FORDELING ===")
    for v, antal in sorted(valuta_fordeling.items(), key=lambda x: -x[1]):
        print(f"  {v:<10}: {antal}")

    print("\n=== ENHED FORDELING ===")
    for e, antal in sorted(enhed_fordeling.items(), key=lambda x: -x[1]):
        print(f"  {e:<12}: {antal}")


if __name__ == "__main__":
    start = datetime.now()
    opdater_json_filer()
    slut = datetime.now()
    minutter = (slut - start).seconds // 60
    sekunder = (slut - start).seconds % 60
    print(f"\nTid brugt: {minutter} minutter og {sekunder} sekunder")
