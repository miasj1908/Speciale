"""
Udtraekker regnskabsdata fra Morningstar .xls cashflow modeller
og gemmer det direkte i PostgreSQL databasen.

Koerer paa de kvalificerede filer fra historiske_aar_tjek.csv.

Krav:
    pip install xlrd psycopg2-binary pandas
"""

import pandas as pd
import xlrd
import psycopg2
import os
import re
from datetime import datetime

# ── INDSTILLINGER ─────────────────────────────────────────────────────────────

HISTORISKE_AAR_STI = r"C:\Users\miasj\PythonProjects\historiske_aar_tjek.csv"
MATCHEDE_PAR_STI   = r"C:\Users\miasj\PythonProjects\matchede_par_final.csv"

LOKAL_ROD_STI = r"C:\Users\miasj\CBS - Copenhagen Business School\Caroline Thøisen Larsen - Data - Forecasting downloadet"
SHAREPOINT_PRAEFIKS = "https://studentcbs-my.sharepoint.com/personal/ctl_acc_cbs_dk/Documents/Data - Forecasting downloadet"

DB_HOST    = "localhost"
DB_PORT    = 5432
DB_BRUGER  = "postgres"
DB_KODEORD = "Miamia97!"
DB_NAVN    = "forecast_studie"

# ── LINE ITEMS ────────────────────────────────────────────────────────────────

INCOME_STATEMENT = {
    "Revenue":                                      "Revenue",
    "Cost of Goods Sold":                           "COGS",
    "Gross Profit":                                 "Gross_Profit",
    "Selling, General, and Administrative Expenses":"SGA",
    "Other Operating Expense (Income)":             "Other_Opex",
    "Depreciation & Amortization":                  "DA",
    "Operating Income (ex charges)":                "EBIT",
    "Restructuring & Other Cash Charges":           "Restructuring",
    "Impairment Charges":                           "Impairment",
    "Other Non-Cash (Income) / Charges":            "Other_NonCash",
    "Operating Income (incl charges)":              "EBIT_incl_charges",
    "Interest Expense":                             "Interest_Expense",
    "Interest Income":                              "Interest_Income",
    "Pre-Tax Income":                               "Pre_Tax_Income",
    "Income Tax Expense":                           "Tax_Expense",
    "Other After-Tax Cash Gains (Losses)":          "Other_AfterTax_Cash",
    "Other After-Tax Non-Cash Gains (Losses)":      "Other_AfterTax_NonCash",
    "(Minority Interest)":                          "Minority_Interest",
    "(Preferred Dividends)":                        "Preferred_Dividends",
    "Net Income":                                   "Net_Income",
    "Weighted Average Diluted Shares Outstanding":  "Shares_Diluted",
    "Diluted Earnings Per Share (GAAP)":            "EPS_GAAP",
    "Adjustments to Net Income":                    "EPS_Adjustments",
    "Adjusted Net Income":                          "Net_Income_Adjusted",
    "Diluted Earnings Per Share (Adjusted)":        "EPS_Adjusted",
    "Regular Dividends Per Share":                  "DPS_Regular",
    "Special Dividends Per Share":                  "DPS_Special",
    "(Total Common Dividends)":                     "Total_Dividends",
    "EBITDA":                                       "EBITDA",
    "Adjusted EBITDA":                              "EBITDA_Adjusted",
}

BALANCE_SHEET = {
    "Cash and Equivalents":                         "Cash",
    "Investments":                                  "Investments",
    "Accounts Receivable":                          "Accounts_Receivable",
    "Inventory":                                    "Inventory",
    "Deferred Tax Assets (Current)":                "DTA_Current",
    "Other Short-Term Assets":                      "Other_ST_Assets",
    "Current Assets":                               "Current_Assets",
    "Net Property, Plant, and Equipment":           "Net_PPE",
    "Goodwill":                                     "Goodwill",
    "Other Intangibles":                            "Other_Intangibles",
    "Deferred Tax Assets (Long-Term)":              "DTA_LongTerm",
    "Other Long-Term Operating Assets":             "Other_LT_Operating_Assets",
    "Long-Term Non-Operating Assets":               "Other_LT_NonOp_Assets",
    "Total Assets":                                 "Total_Assets",
    "Accounts Payable":                             "Accounts_Payable",
    "Short-Term Debt":                              "ST_Debt",
    "Deferred Tax Liabilities (Current)":           "DTL_Current",
    "Other Short-Term Liabilities":                 "Other_ST_Liabilities",
    "Current Liabilities":                          "Current_Liabilities",
    "Long-Term Debt":                               "LT_Debt",
    "Deferred Tax Liabilities (Long-Term)":         "DTL_LongTerm",
    "Other Long-Term Operating Liabilities":        "Other_LT_Operating_Liab",
    "Long-Term Non-Operating Liabilities":          "Other_LT_NonOp_Liab",
    "Total Liabilities":                            "Total_Liabilities",
    "Preferred Stock":                              "Preferred_Stock",
    "Common Stock":                                 "Common_Stock",
    "Additional Paid-In Capital":                   "APIC",
    "Retained Earnings (Deficit)":                  "Retained_Earnings",
    "(Treasury Stock)":                             "Treasury_Stock",
    "Other Equity":                                 "Other_Equity",
    "Shareholder's Equity":                         "Shareholders_Equity",
    "Minority Interest":                            "Minority_Interest_BS",
    "Total Equity":                                 "Total_Equity",
}

CASH_FLOW = {
    "Net Income":                                   "CF_Net_Income",
    "Depreciation":                                 "Depreciation",
    "Amortization":                                 "Amortization",
    "Stock-Based Compensation":                     "SBC",
    "Impairment of Goodwill":                       "Impairment_Goodwill",
    "Impairment of Other Intangibles":              "Impairment_Intangibles",
    "Deferred Taxes":                               "Deferred_Taxes",
    "Other Non-Cash Adjustments":                   "Other_NonCash_CF",
    "(Increase) Decrease in Accounts Receivable":   "Change_AR",
    "(Increase) Decrease in Inventory":             "Change_Inventory",
    "Change in Other Short-Term Assets":            "Change_Other_ST_Assets",
    "Increase (Decrease) in Accounts Payable":      "Change_AP",
    "Change in Other Short-Term Liabilities":       "Change_Other_ST_Liab",
    "Cash From Operations":                         "CFO",
    "(Capital Expenditures)":                       "Capex",
    "Net (Acquisitions), Asset Sales, and Disposals": "Net_Acquisitions",
    "Net (Purchases) Sales of Investments":         "Net_Investments",
    "Other Investing Cash Flow":                    "Other_Investing_CF",
    "Cash From Investing":                          "CFI",
    "Common Stock Issuance or (Repurchase)":        "Stock_Issuance_Repurchase",
    "Common Stock (Dividends)":                     "Dividends_Paid",
    "Short-Term Debt Issuance (Retirement)":        "ST_Debt_Change",
    "Long-Term Debt Issuance (Retirement)":         "LT_Debt_Change",
    "Other Financing Cash Flows":                   "Other_Financing_CF",
    "Cash From Financing":                          "CFF",
    "Exchange Rates, Discontinued Ops, etc. (net)": "FX_Other",
    "Net Change in Cash":                           "Net_Change_Cash",
}

SEKTIONER = {
    "income":   INCOME_STATEMENT,
    "balance":  BALANCE_SHEET,
    "cashflow": CASH_FLOW,
}

ALL_ITEMS = {**INCOME_STATEMENT, **BALANCE_SHEET, **CASH_FLOW}

# Dobbelt-labels der optræder i både IS og CF
DOBBELT_LABELS = {"Net Income": ["Net_Income", "CF_Net_Income"]}

# ── HJÆLPEFUNKTIONER ──────────────────────────────────────────────────────────

def konverter_sti(sharepoint_sti):
    if pd.isna(sharepoint_sti):
        return None
    sti = str(sharepoint_sti).strip()
    if sti.startswith("http"):
        sti = sti.replace(SHAREPOINT_PRAEFIKS, LOKAL_ROD_STI)
        sti = sti.replace("/", "\\")
        sti = sti.replace("Rådata", "Radata")
    return sti


def find_kolonne_mapping(ws):
    """Finder historiske (offset -5 til -1) og prognose (offset +1 til +3) kolonner."""
    offset_row = None
    year_row = None

    for row_idx in range(min(10, ws.nrows)):
        row = ws.row_values(row_idx)
        if -10 in row or -10.0 in row:
            offset_row = row
        if any(isinstance(v, float) and 2000 < v < 2035 for v in row):
            year_row = row

    if not offset_row or not year_row:
        return {}, {}

    hist_kol = {}
    prog_kol = {}

    for col_idx, (offset, year) in enumerate(zip(offset_row, year_row)):
        if not isinstance(offset, (int, float)) or not isinstance(year, (int, float)):
            continue
        year, offset = int(year), int(offset)
        if -5 <= offset <= -1:
            hist_kol[year] = col_idx
        elif 1 <= offset <= 3:  # Kun 3 prognoseår
            prog_kol[year] = col_idx

    return hist_kol, prog_kol


def find_label_raekker(ws):
    """Scanner kolonne E for labels. Håndterer dobbelt-labels."""
    label_forekomster = {}
    for row_idx in range(min(600, ws.nrows)):
        row = ws.row_values(row_idx)
        if len(row) < 5:
            continue
        cell = row[4]
        if cell and isinstance(cell, str):
            label = cell.strip()
            if label in ALL_ITEMS:
                label_forekomster.setdefault(label, []).append(row_idx)
    return label_forekomster


def udtræk_vaerdier(ws, label_forekomster, kolonne_mapping):
    """Udtrækker tal for alle labels og år."""
    resultater = {}

    for excel_label, vores_navn in ALL_ITEMS.items():
        forekomster = label_forekomster.get(excel_label, [])

        if excel_label in DOBBELT_LABELS:
            navne = DOBBELT_LABELS[excel_label]
            for i, navn in enumerate(navne):
                row_idx = forekomster[i] if i < len(forekomster) else (forekomster[0] if forekomster else None)
                if row_idx is None:
                    resultater[navn] = {yr: None for yr in kolonne_mapping}
                    continue
                row = ws.row_values(row_idx)
                resultater[navn] = {}
                for aarstal, col_idx in kolonne_mapping.items():
                    v = row[col_idx] if col_idx < len(row) else None
                    if isinstance(v, str) and any(c in v for c in ['#', 'NM', 'N/A']):
                        v = None
                    if isinstance(v, float) and v == 0.0:
                        v = None
                    resultater[navn][aarstal] = v
        else:
            if not forekomster:
                resultater[vores_navn] = {yr: None for yr in kolonne_mapping}
                continue
            row = ws.row_values(forekomster[0])
            resultater[vores_navn] = {}
            for aarstal, col_idx in kolonne_mapping.items():
                v = row[col_idx] if col_idx < len(row) else None
                if isinstance(v, str) and any(c in v for c in ['#', 'NM', 'N/A']):
                    v = None
                resultater[vores_navn][aarstal] = v

    return resultater


# ── DATABASE FUNKTIONER ───────────────────────────────────────────────────────

def opret_forbindelse():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_BRUGER, password=DB_KODEORD,
        dbname=DB_NAVN
    )


def gem_selskab(cur, ticker, navn, sektor):
    """Indsæt eller hent selskab_id."""
    cur.execute("""
        INSERT INTO selskaber (ticker, navn, sektor)
        VALUES (%s, %s, %s)
        ON CONFLICT (ticker) DO UPDATE SET navn = EXCLUDED.navn
        RETURNING selskab_id
    """, (ticker, navn, sektor))
    return cur.fetchone()[0]


def gem_forecast(cur, selskab_id, forecast_dato, foerste_hist_aar,
                 foerste_prog_aar, sidst_prog_aar, har_equity_report, filnavn):
    """Indsæt forecast-par."""
    cur.execute("""
        INSERT INTO forecasts (selskab_id, forecast_dato, første_prognose_år,
                               sidste_prognose_år, første_hist_år,
                               har_equity_report, filnavn)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING forecast_id
    """, (selskab_id, forecast_dato, foerste_prog_aar, sidst_prog_aar,
          foerste_hist_aar, har_equity_report, filnavn))
    return cur.fetchone()[0]


def gem_regnskabsdata(cur, forecast_id, datakilde, sektion, vaerdier):
    """Gem alle regnskabstal for én datakilde (historisk eller analytiker)."""
    rows = []
    for line_item, aar_dict in vaerdier.items():
        for aarstal, vaerdi in aar_dict.items():
            if vaerdi is not None:
                rows.append((forecast_id, sektion, datakilde,
                             line_item, aarstal, vaerdi))

    if rows:
        cur.executemany("""
            INSERT INTO regnskabsdata
                (forecast_id, sektion, datakilde, line_item, årstal, værdi)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, rows)


def log_handling(cur, handling, filnavn, status, besked=""):
    cur.execute("""
        INSERT INTO log (handling, filnavn, status, besked)
        VALUES (%s, %s, %s, %s)
    """, (handling, filnavn, status, besked))


# ── HOVEDFUNKTION ─────────────────────────────────────────────────────────────

def koer_udtrækning():
    print("Indlaeser kvalificerede filer...")
    df_kval = pd.read_csv(HISTORISKE_AAR_STI)
    df_kval = df_kval[(df_kval['antal_hist_aar'] == 5) &
                      (df_kval['sammenhaengende'] == True)].copy()

    df_match = pd.read_csv(MATCHEDE_PAR_STI, sep=';',
                           encoding='utf-8-sig', on_bad_lines='skip')

    # Merge for at få cf_mappe og dage_forskel
    df = df_kval.merge(
        df_match[['cf_fil', 'cf_mappe', 'rapport_dato',
                  'rapport_fil', 'rapport_mappe', 'dage_forskel']],
        on='cf_fil', how='left'
    )

    print(f"Antal kvalificerede observationer: {len(df)}")
    print(f"Forbinder til database...")

    conn = opret_forbindelse()
    cur = conn.cursor()

    succes = 0
    fejl = 0

    for idx, raekke in df.iterrows():
        cf_fil = raekke['cf_fil']
        cf_mappe = konverter_sti(raekke['cf_mappe'])

        if idx % 100 == 0:
            print(f"Behandler {idx}/{len(df)}: {cf_fil}")
            conn.commit()

        if cf_mappe is None:
            log_handling(cur, 'udtrækning', cf_fil, 'fejl', 'Ingen mappe-sti')
            fejl += 1
            continue

        filsti = os.path.join(cf_mappe, cf_fil)

        if not os.path.exists(filsti):
            log_handling(cur, 'udtrækning', cf_fil, 'fejl', 'Fil ikke fundet')
            fejl += 1
            continue

        try:
            wb = xlrd.open_workbook(filsti)

            # Find Inputs sheet
            inputs_navn = None
            for navn in ['Inputs', 'inputs', 'INPUTS']:
                if navn in wb.sheet_names():
                    inputs_navn = navn
                    break

            if not inputs_navn:
                log_handling(cur, 'udtrækning', cf_fil, 'fejl', 'Ingen Inputs-fane')
                fejl += 1
                continue

            ws = wb.sheet_by_name(inputs_navn)

            # Find kolonne-mapping
            hist_kol, prog_kol = find_kolonne_mapping(ws)

            if not hist_kol or not prog_kol:
                log_handling(cur, 'udtrækning', cf_fil, 'fejl', 'Kunne ikke finde kolonne-mapping')
                fejl += 1
                continue

            # Find labels
            label_forekomster = find_label_raekker(ws)

            # Udtræk historiske og prognose værdier
            hist_vaerdier = udtræk_vaerdier(ws, label_forekomster, hist_kol)
            prog_vaerdier = udtræk_vaerdier(ws, label_forekomster, prog_kol)

            # Udtræk ticker fra filnavn (f.eks. NAS_AAPL_CashFlow... -> NAS_AAPL)
            ticker_match = re.match(r'^([^_]+_[^_]+)_', cf_fil)
            ticker = ticker_match.group(1) if ticker_match else cf_fil[:10]

            # Gem i database
            selskab_id = gem_selskab(cur, ticker, raekke['selskab'], raekke['sektor'])

            forecast_dato = pd.to_datetime(raekke['rapport_dato'],
                                           dayfirst=True).date()

            forecast_id = gem_forecast(
                cur, selskab_id, forecast_dato,
                min(hist_kol.keys()), min(prog_kol.keys()),
                max(prog_kol.keys()), True, cf_fil
            )

            # Gem regnskabsdata per sektion
            for sektion_navn, sektion_items in SEKTIONER.items():
                hist_sek = {k: v for k, v in hist_vaerdier.items()
                           if k in sektion_items.values()}
                prog_sek = {k: v for k, v in prog_vaerdier.items()
                           if k in sektion_items.values()}

                gem_regnskabsdata(cur, forecast_id, 'historisk', sektion_navn, hist_sek)
                gem_regnskabsdata(cur, forecast_id, 'analytiker', sektion_navn, prog_sek)

            log_handling(cur, 'udtrækning', cf_fil, 'ok')
            succes += 1

        except Exception as e:
            log_handling(cur, 'udtrækning', cf_fil, 'fejl', str(e)[:200])
            fejl += 1
            continue

    conn.commit()
    cur.close()
    conn.close()

    print("\n" + "="*50)
    print("UDTRÆKNING FAERDIG")
    print("="*50)
    print(f"Succes: {succes}")
    print(f"Fejl:   {fejl}")
    print(f"Total:  {succes + fejl}")


if __name__ == "__main__":
    start = datetime.now()
    koer_udtrækning()
    slut = datetime.now()
    minutter = (slut - start).seconds // 60
    sekunder = (slut - start).seconds % 60
    print(f"\nTid brugt: {minutter} minutter og {sekunder} sekunder")
