# evaluate_coherence_v3.py
# Thesis-evaluering: Regnskabsmaessig kohaerens af forecasts. (VERSION 3)
#
# Aendringer ift. v2:
#   (1) UDVIDET IS_ebit -> IS_operating_income:
#       Tester nu hele Gross Profit -> EBIT broen:
#         EBIT = Gross_Profit - SGA - Other_Opex - D&A
#       Det fanger ekstraktionsfejl og LLM-fejl der ligger i SG&A og
#       Other Operating Expenses, ikke kun i den simple EBITDA->EBIT bridge.
#
#   (2) UDVIDET IS_pretax_income:
#       Tester nu med non-operating items mellem EBIT og Pre-Tax:
#         PTI = EBIT - Restructuring - Impairment - SBC - Other_NonCash
#               + Interest_Income - Interest_Expense
#       Det matcher Morningstar-konventionen for non-operating items mere
#       praecist. Bemaerk: identiteten antager at EBIT-vaerdien i data er
#       "operating EBIT" (ex-charges).
#
#   (3) UDVIDET IS_net_income:
#       Tester nu med after-tax items efter Net Income:
#         NI = PTI - Tax + Minority + Preferred
#              + Other_AfterTax_Cash + Other_AfterTax_NonCash
#       Det fanger after-tax adjustments der ligger mellem income tax og
#       reported Net Income.
#
# Alle andre tjek er uaendrede ift. v2.
#
# NIVEAU 1 (intra-statement):
#   IS_gross_profit  : Gross_Profit    = Revenue - COGS
#   IS_ebit          : EBIT            = Gross_Profit - SGA - Other_Opex - DA_eff   [UDVIDET]
#   IS_pretax_income : Pre_Tax_Income  = EBIT - Restructuring - Impairment - SBC
#                                        - Other_NonCash + Interest_Income          [UDVIDET]
#                                        - Interest_Expense
#   IS_net_income    : Net_Income      = Pre_Tax_Income - Tax_Expense + Minority    [UDVIDET]
#                                        + Preferred + Other_AfterTax_Cash
#                                        + Other_AfterTax_NonCash
#   IS_eps           : EPS_GAAP        = Net_Income / Shares_Diluted
#   BS_identity      : Total_Assets    = Total_Liabilities + Total_Equity
#   BS_equity_decomp : Shareholders_Equity = Common_Stock + APIC + Retained_Earnings
#                                           + Other_Equity + Treasury_Stock
#   CFS_identity     : Net_Change_Cash = CFO + CFI + CFF + FX_Other
#
# NIVEAU 2 (cross-statement articulation):
#   X_cash_roll      : Cash_t          = Cash_{t-1} + Net_Change_Cash_t
#   X_re_roll        : Retained_Earn_t = Retained_Earn_{t-1} + Net_Income_t + Total_Dividends_t
#   X_ni_match       : CF_Net_Income_t = Net_Income_t - Minority_Interest_t
#   X_div_match      : |Dividends_Paid_CFS| = |Total_Dividends_IS|     [NYT]
#   X_ppe_roll       : Net_PPE_t       = Net_PPE_{t-1} - Capex_t - Depreciation_t - Impairment_t
#   X_debt_roll      : LT_Debt_t       = LT_Debt_{t-1} + LT_Debt_Change_t
#
# Tolerance: 0.01 (1%) relativ til passende skala.
# Output: coherence_results_v3.csv

import psycopg2
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DB_PARAMS = {
    "dbname": "forecast_studie",
    "user": "postgres",
    "password": "Miamia97!",
    "host": "localhost",
    "port": 5432,
}

MODEL = "gpt-5.1"
EFFORT = "medium"
PROMPTS = ["baseline", "equity"]
TOLERANCE = 0.01
HORIZONS = (2, 3)

OUTPUT_FILE = "coherence_results_v3.csv"

# Udvidet line item-liste med nye items til Niveau 2-tjek
LINE_ITEMS = [
    # Resultatopgoerelse
    "Revenue", "COGS", "Gross_Profit",
    "SGA", "Other_Opex",                                  # NYE
    "EBITDA", "DA", "Depreciation", "Amortization", "EBIT",
    "Restructuring", "Impairment", "SBC", "Other_NonCash", # NYE (mellem EBIT og PTI)
    "Interest_Income", "Interest_Expense", "Pre_Tax_Income",
    "Tax_Expense", "Minority_Interest", "Preferred_Dividends",
    "Other_AfterTax_Cash", "Other_AfterTax_NonCash",      # NYE (mellem skat og NI)
    "Net_Income",
    "EPS_GAAP", "Shares_Diluted",
    # Balance
    "Total_Assets", "Total_Liabilities", "Total_Equity", "Shareholders_Equity",
    "Common_Stock", "APIC", "Retained_Earnings", "Other_Equity", "Treasury_Stock",
    # Cash flow
    "Cash", "Net_Change_Cash", "CFO", "CFI", "CFF", "FX_Other",
    "CF_Net_Income",
    "Total_Dividends",
    # Cross-statement roll-forwards
    "Net_PPE", "Capex",
    "LT_Debt", "LT_Debt_Change",
]


def fetch_all():
    conn = psycopg2.connect(**DB_PARAMS)
    q = """
    SELECT r.forecast_id, f.selskab_id,
           MAX(CASE WHEN r.datakilde='historisk' THEN r.årstal END)
               OVER (PARTITION BY r.forecast_id) AS t0_year,
           r.datakilde, r.prompt_template, r.line_item, r.årstal, r.værdi
    FROM regnskabsdata r
    JOIN forecasts f ON f.forecast_id = r.forecast_id
    WHERE r.line_item = ANY(%s)
      AND (
           (r.datakilde='llm' AND r.prompt_template = ANY(%s)
                AND r.model_name=%s AND r.reasoning_effort=%s)
           OR r.datakilde IN ('analytiker','realiseret','historisk')
      )
    """
    df = pd.read_sql_query(q, conn, params=[LINE_ITEMS, PROMPTS, MODEL, EFFORT])
    conn.close()
    return df


def source_key(datakilde, prompt_template):
    if datakilde == "llm":
        return f"llm_{prompt_template}"
    return datakilde


def compute_checks(df):
    df = df.copy()
    df["source_key"] = [source_key(d, p) for d, p in zip(df["datakilde"], df["prompt_template"])]

    rows = []

    for fid, g in df.groupby("forecast_id"):
        t0_year = g["t0_year"].dropna().iloc[0] if g["t0_year"].notna().any() else None
        if t0_year is None:
            continue
        t0_year = int(t0_year)
        selskab = int(g["selskab_id"].iloc[0])

        pivot = g.pivot_table(
            index=["source_key", "line_item"], columns="årstal",
            values="værdi", aggfunc="first",
        )

        def val(src, item, year):
            try:
                v = pivot.loc[(src, item), year]
                return float(v) if pd.notna(v) else np.nan
            except KeyError:
                return np.nan

        def val_or(src, item, year, default=0.0):
            v = val(src, item, year)
            return v if pd.notna(v) else default

        def val_prior(src, item, year):
            v = val(src, item, year)
            if pd.notna(v):
                return v
            v = val("realiseret", item, year)
            if pd.notna(v):
                return v
            return val("historisk", item, year)

        def add(source_k, h, check, category, statement, lhs, rhs, scale):
            if pd.isna(lhs) or pd.isna(rhs) or pd.isna(scale):
                return
            scale_abs = abs(scale)
            if scale_abs == 0:
                return
            residual = lhs - rhs
            residual_pct = residual / scale_abs
            if source_k.startswith("llm_"):
                source = "llm"
                prompt = source_k[4:]
            else:
                source = source_k
                prompt = ""
            rows.append({
                "forecast_id": fid,
                "selskab_id": selskab,
                "horizon": h,
                "source": source,
                "prompt_template": prompt,
                "check": check,
                "category": category,
                "statement": statement,
                "lhs": lhs,
                "rhs": rhs,
                "residual_signed": residual,
                "scale": scale_abs,
                "residual_pct_signed": residual_pct,
                "within_tolerance": bool(abs(residual_pct) < TOLERANCE),
            })

        for h in HORIZONS:
            fy = t0_year + h
            prev_fy = fy - 1

            for sk in ("llm_baseline", "llm_equity", "analytiker", "realiseret"):
                rev = val(sk, "Revenue", fy)
                ta = val(sk, "Total_Assets", fy)
                te = val(sk, "Total_Equity", fy)
                ni = val(sk, "Net_Income", fy)

                # ------- NIVEAU 1: INTRA-STATEMENT -------

                # IS_gross_profit: Gross_Profit = Revenue - COGS
                cogs = val(sk, "COGS", fy)
                gp = val(sk, "Gross_Profit", fy)
                if pd.notna(rev) and pd.notna(cogs):
                    add(sk, h, "IS_gross_profit", "intra", "IS", gp, rev - cogs, rev)

                # IS_ebit (UDVIDET): EBIT = Gross_Profit - SGA - Other_Opex - D&A
                # DA_eff: brug DA hvis !=0, ellers Dep+Amor, ellers Dep
                da = val(sk, "DA", fy)
                dep = val(sk, "Depreciation", fy)
                amor = val(sk, "Amortization", fy)
                if pd.notna(da) and da != 0:
                    da_eff = da
                elif pd.notna(dep) and pd.notna(amor):
                    da_eff = dep + amor
                elif pd.notna(dep):
                    da_eff = dep
                elif pd.notna(da):
                    da_eff = da
                else:
                    da_eff = np.nan

                sga = val_or(sk, "SGA", fy, 0.0)
                other_opex = val_or(sk, "Other_Opex", fy, 0.0)
                ebit = val(sk, "EBIT", fy)
                if pd.notna(gp) and pd.notna(da_eff) and pd.notna(ebit):
                    add(sk, h, "IS_ebit", "intra", "IS",
                        ebit, gp - sga - other_opex - da_eff, rev)

                # IS_pretax_income (UDVIDET):
                # PTI = EBIT - Restructuring - Impairment - SBC - Other_NonCash
                #       + Interest_Income - Interest_Expense
                restructuring = val_or(sk, "Restructuring", fy, 0.0)
                impairment_op = val_or(sk, "Impairment", fy, 0.0)
                sbc = val_or(sk, "SBC", fy, 0.0)
                other_noncash = val_or(sk, "Other_NonCash", fy, 0.0)
                iinc = val_or(sk, "Interest_Income", fy, 0.0)
                iexp = val_or(sk, "Interest_Expense", fy, 0.0)
                pti = val(sk, "Pre_Tax_Income", fy)
                if pd.notna(ebit) and pd.notna(pti):
                    add(sk, h, "IS_pretax_income", "intra", "IS",
                        pti,
                        ebit - restructuring - impairment_op - sbc - other_noncash
                        + iinc - iexp,
                        rev)

                # IS_net_income (UDVIDET):
                # NI = PTI - Tax + Minority + Preferred
                #      + Other_AfterTax_Cash + Other_AfterTax_NonCash
                tax = val(sk, "Tax_Expense", fy)
                minority = val_or(sk, "Minority_Interest", fy, 0.0)
                preferred = val_or(sk, "Preferred_Dividends", fy, 0.0)
                other_at_cash = val_or(sk, "Other_AfterTax_Cash", fy, 0.0)
                other_at_noncash = val_or(sk, "Other_AfterTax_NonCash", fy, 0.0)
                if pd.notna(pti) and pd.notna(tax):
                    add(sk, h, "IS_net_income", "intra", "IS",
                        ni,
                        pti - tax + minority + preferred
                        + other_at_cash + other_at_noncash,
                        rev)

                # IS_eps: EPS_GAAP = Net_Income / Shares_Diluted
                eps = val(sk, "EPS_GAAP", fy)
                sh = val(sk, "Shares_Diluted", fy)
                if pd.notna(ni) and pd.notna(sh) and sh != 0 and pd.notna(eps) and eps != 0:
                    add(sk, h, "IS_eps", "intra", "IS", eps, ni / sh, abs(eps))

                # BS_identity: Total_Assets = Total_Liabilities + Total_Equity
                tl = val(sk, "Total_Liabilities", fy)
                if pd.notna(tl) and pd.notna(te):
                    add(sk, h, "BS_identity", "intra", "BS", ta, tl + te, ta)

                # BS_equity_decomp
                se = val(sk, "Shareholders_Equity", fy)
                cs = val_or(sk, "Common_Stock", fy, 0.0)
                apic = val_or(sk, "APIC", fy, 0.0)
                re_cur = val(sk, "Retained_Earnings", fy)
                oth_eq = val_or(sk, "Other_Equity", fy, 0.0)
                treasury = val_or(sk, "Treasury_Stock", fy, 0.0)
                if pd.notna(se) and pd.notna(re_cur):
                    add(sk, h, "BS_equity_decomp", "intra", "BS",
                        se, cs + apic + re_cur + oth_eq + treasury, ta)

                # CFS_identity: Net_Change_Cash = CFO + CFI + CFF + FX_Other
                ncc = val(sk, "Net_Change_Cash", fy)
                cfo = val(sk, "CFO", fy)
                cfi = val(sk, "CFI", fy)
                cff = val(sk, "CFF", fy)
                fx = val_or(sk, "FX_Other", fy, 0.0)
                if all(pd.notna(x) for x in [cfo, cfi, cff]):
                    add(sk, h, "CFS_identity", "intra", "CFS",
                        ncc, cfo + cfi + cff + fx, rev)

                # ------- NIVEAU 2: CROSS-STATEMENT ARTICULATION -------

                # X_cash_roll
                cash_t = val(sk, "Cash", fy)
                cash_prev = val_prior(sk, "Cash", prev_fy)
                if pd.notna(cash_prev) and pd.notna(ncc):
                    add(sk, h, "X_cash_roll", "cross", "X",
                        cash_t, cash_prev + ncc, ta)

                # X_re_roll
                re_prev = val_prior(sk, "Retained_Earnings", prev_fy)
                div = val_or(sk, "Total_Dividends", fy, 0.0)
                if pd.notna(re_prev) and pd.notna(ni):
                    add(sk, h, "X_re_roll", "cross", "X",
                        re_cur, re_prev + ni + div, te)

                # X_ni_match: CF_Net_Income = Net_Income - Minority_Interest
                cf_ni = val(sk, "CF_Net_Income", fy)
                minority_x = val_or(sk, "Minority_Interest", fy, 0.0)
                if pd.notna(cf_ni) and pd.notna(ni) and ni != 0:
                    add(sk, h, "X_ni_match", "cross", "X",
                        cf_ni, ni - minority_x, abs(ni))

                # X_div_match: Dividends consistency (IS vs CFS)
                # Total_Dividends paa IS (typisk positiv som omkostning paa equity)
                # Dividends_Paid paa CFS (typisk negativ som udflow)
                # Test: |Dividends_Paid| = |Total_Dividends|
                total_div = val(sk, "Total_Dividends", fy)
                div_paid = val(sk, "Dividends_Paid", fy)
                if pd.notna(total_div) and pd.notna(div_paid):
                    # Brug absolutte vaerdier for at undgaa fortegns-konventionsproblemer
                    # Skala: brug den stoerste i absolut vaerdi (eller 1 hvis begge er 0)
                    scale_div = max(abs(total_div), abs(div_paid), 1.0)
                    add(sk, h, "X_div_match", "cross", "X",
                        abs(div_paid), abs(total_div), scale_div)

                # X_ppe_roll
                ppe_t = val(sk, "Net_PPE", fy)
                ppe_prev = val_prior(sk, "Net_PPE", prev_fy)
                capex = val(sk, "Capex", fy)
                dep_only = val(sk, "Depreciation", fy)
                imp = val_or(sk, "Impairment", fy, 0.0)
                if all(pd.notna(x) for x in [ppe_prev, capex, dep_only]):
                    add(sk, h, "X_ppe_roll", "cross", "X",
                        ppe_t, ppe_prev - capex - dep_only - imp, ta)

                # X_debt_roll
                ltd_t = val(sk, "LT_Debt", fy)
                ltd_prev = val_prior(sk, "LT_Debt", prev_fy)
                ltd_ch = val(sk, "LT_Debt_Change", fy)
                if all(pd.notna(x) for x in [ltd_prev, ltd_ch]):
                    add(sk, h, "X_debt_roll", "cross", "X",
                        ltd_t, ltd_prev + ltd_ch, ta)

    return pd.DataFrame(rows)


def print_summary(res):
    def stat_line(label, data):
        if len(data) == 0:
            return None
        n = len(data)
        pass_rate = data["within_tolerance"].mean() * 100
        abs_pct = data["residual_pct_signed"].abs()
        med = abs_pct.median() * 100
        p95 = abs_pct.quantile(0.95) * 100
        return (f"  {label:<26} N={n:>5}  pass={pass_rate:>5.1f}%  "
                f"|err| med={med:>7.3f}%  p95={p95:>8.3f}%")

    print(f"\n=== COHERENCE SUMMARY  (tolerance = {TOLERANCE:.0%}, horisonter = {HORIZONS}) ===")

    for cat in ("intra", "cross"):
        print(f"\n--- {cat.upper()}-STATEMENT ---")
        for check in sorted(res[res["category"] == cat]["check"].unique()):
            print(f"\n{check}:")
            for src_label, src_filter in [
                ("realiseret",          (res["source"] == "realiseret")),
                ("analytiker",          (res["source"] == "analytiker")),
                ("llm (baseline)",      (res["source"] == "llm") & (res["prompt_template"] == "baseline")),
                ("llm (equity)",        (res["source"] == "llm") & (res["prompt_template"] == "equity")),
            ]:
                d = res[(res["check"] == check) & src_filter]
                line = stat_line(src_label, d)
                if line:
                    print(line)


def main():
    logging.info(f"Henter data (prompts={PROMPTS}, model={MODEL}, effort={EFFORT})...")
    logging.info(f"Horisonter: {HORIZONS}")
    logging.info(f"Antal line items: {len(LINE_ITEMS)}")
    df = fetch_all()
    logging.info(f"  {len(df):,} rows, {df['forecast_id'].nunique():,} forecasts")

    logging.info("Beregner kohaerens-checks...")
    res = compute_checks(df)
    logging.info(f"  {len(res):,} check-raekker")

    cols = ["forecast_id", "selskab_id", "horizon", "source", "prompt_template",
            "check", "category", "statement",
            "lhs", "rhs", "residual_signed",
            "scale", "residual_pct_signed", "within_tolerance"]
    res = res.reindex(columns=cols)
    res.to_csv(OUTPUT_FILE, index=False)
    logging.info(f"Gemt: {OUTPUT_FILE}")

    print_summary(res)


if __name__ == "__main__":
    main()
