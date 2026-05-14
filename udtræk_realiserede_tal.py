"""
Udtraekker realiserede tal fra den nyeste cashflow model per selskab
og gemmer dem i PostgreSQL databasen med datakilde = 'realiseret'.

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

MATCHEDE_PAR_STI = r"C:\Users\miasj\PythonProjects\matchede_par_final.csv"

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
DOBBELT_LABELS = {"Net Income": ["Net_Income", "CF_Net_Income"]}


# ── HJÆLPEFUNKTIONER ──────────────────────────────────────────────────────────

def konverter_sti(sharepoint_sti):
    if pd.isna(sharepoint_sti):
        return None
    sti = str(sharepoint_sti).strip()
    if sti.startswith("http"):
        sti = sti.replace(SHAREPOINT_PRAEFIKS, LOKAL_ROD_STI)
        sti = sti.replace("/", "\\")
        sti = sti.replace("R\u00e5data", "Radata")
    return sti


def find_alle_kolonner(ws):
    """
    Finder ALLE kolonner med årstal — både historiske og prognoser.
    Returnerer dict: {aarstal: col_idx} for alle offset-10 til +10.
    """
    offset_row = None
    year_row = None

    for row_idx in range(min(10, ws.nrows)):
        row = ws.row_values(row_idx)
        if -10 in row or -10.0 in row:
            offset_row = row
        if any(isinstance(v, float) and 2000 < v < 2035 for v in row):
            year_row = row

    if not offset_row or not year_row:
        return {}

    alle_kol = {}
    for col_idx, (offset, year) in enumerate(zip(offset_row, year_row)):
        if not isinstance(offset, (int, float)) or not isinstance(year, (int, float)):
            continue
        year_int = int(year)
        offset_int = int(offset)
        # Gem alle historiske kolonner (offset -10 til -1)
        if -10 <= offset_int <= -1:
            alle_kol[year_int] = col_idx

    return alle_kol


def find_label_raekker(ws):
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
                    if isinstance(v, str):
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
                if isinstance(v, str):
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


def hent_selskab_id(cur, ticker):
    cur.execute("SELECT selskab_id FROM selskaber WHERE ticker = %s", (ticker,))
    result = cur.fetchone()
    return result[0] if result else None


def gem_realiserede_tal(cur, selskab_id, sektion, vaerdier, nyeste_model_dato):
    rows = []
    for line_item, aar_dict in vaerdier.items():
        for aarstal, vaerdi in aar_dict.items():
            if vaerdi is not None and vaerdi != "":
                rows.append((selskab_id, sektion, 'realiseret',
                             line_item, aarstal, vaerdi,
                             nyeste_model_dato))
    if rows:
        cur.executemany("""
            INSERT INTO regnskabsdata
                (forecast_id, sektion, datakilde, line_item, årstal, værdi)
            SELECT f.forecast_id, %s, %s, %s, %s, %s
            FROM forecasts f
            WHERE f.selskab_id = %s
            ON CONFLICT DO NOTHING
        """, [(r[1], r[2], r[3], r[4], r[5], selskab_id) for r in rows])


def gem_realiserede_direkte(cur, selskab_id, sektion, vaerdier):
    """Gem realiserede tal koblet til alle forecasts for dette selskab."""
    # Hent alle forecast_ids for dette selskab
    cur.execute("""
        SELECT forecast_id, første_prognose_år, sidste_prognose_år
        FROM forecasts
        WHERE selskab_id = %s
    """, (selskab_id,))
    forecasts = cur.fetchall()

    rows = []
    for forecast_id, foerste_prog, sidst_prog in forecasts:
        for line_item, aar_dict in vaerdier.items():
            for aarstal, vaerdi in aar_dict.items():
                # Gem kun realiserede tal for prognoseårene
                if (vaerdi is not None and vaerdi != "" and
                        foerste_prog <= aarstal <= sidst_prog):
                    rows.append((forecast_id, sektion, 'realiseret',
                                line_item, aarstal, float(vaerdi)))

    if rows:
        cur.executemany("""
            INSERT INTO regnskabsdata
                (forecast_id, sektion, datakilde, line_item, årstal, værdi)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, rows)

    return len(rows)


# ── HOVEDFUNKTION ─────────────────────────────────────────────────────────────

def koer_realiserede_tal():
    print("Indlaeser matchede par...")
    df_match = pd.read_csv(
        MATCHEDE_PAR_STI, sep=';',
        encoding='utf-8-sig', on_bad_lines='skip'
    )

    df_match['cf_dato'] = pd.to_datetime(df_match['cf_dato'], dayfirst=True)

    # Find nyeste model per selskab
    nyeste_per_selskab = (
        df_match.sort_values('cf_dato', ascending=False)
        .groupby('selskab')
        .first()
        .reset_index()
    )

    print(f"Antal unikke selskaber: {len(nyeste_per_selskab)}")
    print("Forbinder til database...")

    conn = opret_forbindelse()
    cur = conn.cursor()

    succes = 0
    fejl = 0
    total_rækker = 0

    for idx, raekke in nyeste_per_selskab.iterrows():
        selskab = raekke['selskab']
        cf_fil = raekke['cf_fil']
        cf_mappe = konverter_sti(raekke['cf_mappe'])
        cf_dato = raekke['cf_dato']

        if idx % 20 == 0:
            print(f"Behandler {idx}/{len(nyeste_per_selskab)}: {selskab}")
            conn.commit()

        if cf_mappe is None:
            print(f"  FEJL: Ingen mappe-sti for {selskab}")
            fejl += 1
            continue

        filsti = os.path.join(cf_mappe, cf_fil)

        if not os.path.exists(filsti):
            print(f"  FEJL: Fil ikke fundet: {cf_fil}")
            fejl += 1
            continue

        try:
            wb = xlrd.open_workbook(filsti)

            inputs_navn = None
            for navn in ['Inputs', 'inputs', 'INPUTS']:
                if navn in wb.sheet_names():
                    inputs_navn = navn
                    break

            if not inputs_navn:
                print(f"  FEJL: Ingen Inputs-fane i {cf_fil}")
                fejl += 1
                continue

            ws = wb.sheet_by_name(inputs_navn)

            # Hent ALLE historiske kolonner fra nyeste model
            alle_kol = find_alle_kolonner(ws)

            if not alle_kol:
                print(f"  FEJL: Ingen kolonner fundet i {cf_fil}")
                fejl += 1
                continue

            print(f"  {selskab}: realiserede år = {sorted(alle_kol.keys())}")

            label_forekomster = find_label_raekker(ws)
            realiserede = udtræk_vaerdier(ws, label_forekomster, alle_kol)

            # Find selskab_id i databasen
            ticker_match = re.match(r'^([^_]+_[^_]+)_', cf_fil)
            ticker = ticker_match.group(1) if ticker_match else cf_fil[:10]

            selskab_id = hent_selskab_id(cur, ticker)

            if selskab_id is None:
                print(f"  FEJL: Selskab ikke fundet i DB: {ticker}")
                fejl += 1
                continue

            # Gem realiserede tal koblet til relevante forecasts
            antal_rækker = 0
            for sektion_navn, sektion_items in SEKTIONER.items():
                real_sek = {k: v for k, v in realiserede.items()
                           if k in sektion_items.values()}
                antal_rækker += gem_realiserede_direkte(
                    cur, selskab_id, sektion_navn, real_sek
                )

            total_rækker += antal_rækker
            succes += 1

        except Exception as e:
            print(f"  FEJL: {selskab}: {str(e)[:100]}")
            conn.rollback()
            fejl += 1
            continue

    conn.commit()

    # Tjek evaluerbarhed
    print("\nTjekker evaluerbarhed...")
    cur.execute("""
        SELECT
            f.forecast_id,
            f.første_prognose_år,
            f.sidste_prognose_år,
            COUNT(DISTINCT r.årstal) as antal_realiserede_aar
        FROM forecasts f
        LEFT JOIN regnskabsdata r ON (
            r.forecast_id = f.forecast_id
            AND r.datakilde = 'realiseret'
            AND r.line_item = 'EPS_Adjusted'
        )
        GROUP BY f.forecast_id, f.første_prognose_år, f.sidste_prognose_år
    """)
    resultater = cur.fetchall()

    fuldt_t1 = sum(1 for r in resultater if r[3] >= 1)
    fuldt_t2 = sum(1 for r in resultater if r[3] >= 2)
    fuldt_t3 = sum(1 for r in resultater if r[3] >= 3)

    print(f"\n=== EVALUERBARHED (baseret paa EPS_Adjusted) ===")
    print(f"Fuldt evaluerbare t+1: {fuldt_t1} / {len(resultater)}")
    print(f"Fuldt evaluerbare t+2: {fuldt_t2} / {len(resultater)}")
    print(f"Fuldt evaluerbare t+3: {fuldt_t3} / {len(resultater)}")

    cur.close()
    conn.close()

    print("\n" + "="*50)
    print("REALISEREDE TAL FAERDIG")
    print("="*50)
    print(f"Selskaber behandlet:  {succes}")
    print(f"Fejl:                 {fejl}")
    print(f"Rækker gemt i DB:     {total_rækker}")


if __name__ == "__main__":
    start = datetime.now()
    koer_realiserede_tal()
    slut = datetime.now()
    minutter = (slut - start).seconds // 60
    sekunder = (slut - start).seconds % 60
    print(f"\nTid brugt: {minutter} minutter og {sekunder} sekunder")
