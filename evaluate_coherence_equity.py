"""
Coherence-evaluering for equity-prompt forecasts.
Samme identitetstjek som evaluate_coherence.py, men filtreret paa prompt_template='equity'.
Output: coherence_results_equity.csv
"""
import argparse
import psycopg2
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DB_PARAMS = {
    "dbname": "forecast_studie", "user": "postgres",
    "password": "Miamia97!", "host": "localhost", "port": 5432,
}

NEEDED = [
    "Net_Income", "CF_Net_Income",
    "Total_Assets", "Total_Liabilities", "Total_Equity",
    "Cash", "Net_Change_Cash", "CFO", "CFI", "CFF",
    "Net_PPE", "Capex", "Depreciation",
]


def fetch_all(prompt, model, effort):
    conn = psycopg2.connect(**DB_PARAMS)
    q = """
    SELECT r.forecast_id,
           MAX(CASE WHEN r.datakilde='historisk' THEN r.årstal END)
               OVER (PARTITION BY r.forecast_id) AS t0_year,
           r.datakilde, r.line_item, r.årstal, r.værdi
    FROM regnskabsdata r
    WHERE r.line_item = ANY(%s)
      AND (
           (r.datakilde='llm' AND r.prompt_template=%s AND r.model_name=%s AND r.reasoning_effort=%s)
           OR r.datakilde IN ('analytiker','realiseret','historisk')
      )
    """
    df = pd.read_sql_query(q, conn, params=[NEEDED, prompt, model, effort])
    conn.close()
    return df


def compute_coherence(df):
    rows = []
    for fid, g in df.groupby("forecast_id"):
        t0_year = g["t0_year"].dropna().iloc[0] if g["t0_year"].notna().any() else None
        if t0_year is None:
            continue
        t0_year = int(t0_year)

        pivot = g.pivot_table(
            index=["datakilde", "line_item"], columns="årstal",
            values="værdi", aggfunc="first",
        )

        def val(source, item, year):
            try:
                v = pivot.loc[(source, item), year]
                return v if pd.notna(v) else np.nan
            except KeyError:
                return np.nan

        def cash_prev(source, year):
            v = val(source, "Cash", year)
            if pd.notna(v):
                return v
            return val("historisk", "Cash", year)

        def ppe_prev(source, year):
            v = val(source, "Net_PPE", year)
            if pd.notna(v):
                return v
            return val("historisk", "Net_PPE", year)

        for h in (1, 2, 3):
            fy = t0_year + h
            prev_fy = fy - 1
            for src in ("llm", "analytiker", "realiseret"):
                entry = {"forecast_id": fid, "horizon": h, "source": src}

                ta = val(src, "Total_Assets", fy)
                scaler = ta if pd.notna(ta) and ta else None

                ni = val(src, "Net_Income", fy)
                cfni = val(src, "CF_Net_Income", fy)
                if pd.notna(ni) and pd.notna(cfni) and scaler:
                    entry["ni_recon_pct"] = abs(ni - cfni) / scaler * 100

                tl = val(src, "Total_Liabilities", fy)
                te = val(src, "Total_Equity", fy)
                if pd.notna(ta) and pd.notna(tl) and pd.notna(te) and scaler:
                    entry["balance_id_pct"] = abs(ta - (tl + te)) / scaler * 100

                cash_t = val(src, "Cash", fy)
                cash_prev_v = cash_prev(src, prev_fy)
                ncc = val(src, "Net_Change_Cash", fy)
                if pd.notna(cash_t) and pd.notna(cash_prev_v) and pd.notna(ncc) and scaler:
                    delta_cash = cash_t - cash_prev_v
                    entry["cash_recon_a_pct"] = abs(delta_cash - ncc) / scaler * 100

                cfo = val(src, "CFO", fy)
                cfi = val(src, "CFI", fy)
                cff = val(src, "CFF", fy)
                if all(pd.notna(x) for x in [ncc, cfo, cfi, cff]) and scaler:
                    entry["cash_recon_b_pct"] = abs(ncc - (cfo + cfi + cff)) / scaler * 100

                ppe_t = val(src, "Net_PPE", fy)
                ppe_prev_v = ppe_prev(src, prev_fy)
                capex = val(src, "Capex", fy)
                depr = val(src, "Depreciation", fy)
                if all(pd.notna(x) for x in [ppe_t, ppe_prev_v, capex, depr]) and scaler:
                    delta_ppe = ppe_t - ppe_prev_v
                    implied = (-capex) - depr
                    entry["accrual_pct"] = abs(delta_ppe - implied) / scaler * 100

                if any(k in entry for k in
                       ("ni_recon_pct","balance_id_pct","cash_recon_a_pct",
                        "cash_recon_b_pct","accrual_pct")):
                    rows.append(entry)
    return pd.DataFrame(rows)


def summary(df):
    print("\n" + "=" * 90)
    print("COHERENCE RESULTATER (equity) — fejl som % af Total Assets")
    print("=" * 90)
    metrics = [
        ("ni_recon_pct",      "Net Income (IS vs CF)"),
        ("balance_id_pct",    "Balance: TA = TL + TE"),
        ("cash_recon_a_pct",  "Cash: dCash = Net_Change_Cash"),
        ("cash_recon_b_pct",  "Cash: NCC = CFO+CFI+CFF"),
        ("accrual_pct",       "Accrual: dPPE ~ -Capex - Dep"),
    ]
    print(f"\n{'Metric':<35}{'Source':<14}{'h':>3}{'N':>7}{'Median %':>10}{'Mean %':>10}{'P95 %':>10}")
    for col, name in metrics:
        for src in ("llm", "analytiker", "realiseret"):
            for h in (1, 2, 3):
                sub = df[(df["source"] == src) & (df["horizon"] == h)][col].dropna()
                if len(sub) == 0:
                    continue
                print(f"{name:<35}{src:<14}{h:>3}{len(sub):>7}"
                      f"{sub.median():>10.3f}{sub.mean():>10.3f}{sub.quantile(0.95):>10.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="equity")
    ap.add_argument("--model", default="gpt-5.1")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--output", default="coherence_results_equity.csv")
    args = ap.parse_args()

    logging.info(f"Henter coherence-data (prompt={args.prompt}, model={args.model}, effort={args.effort})...")
    df = fetch_all(args.prompt, args.model, args.effort)
    logging.info(f"  {len(df):,} rows, {df['forecast_id'].nunique():,} forecasts")

    logging.info("Beregner coherence-identiteter...")
    res = compute_coherence(df)
    logging.info(f"  {len(res):,} forecast×horizon×source raekker")

    cols = ["forecast_id","horizon","source",
            "ni_recon_pct","balance_id_pct","cash_recon_a_pct",
            "cash_recon_b_pct","accrual_pct"]
    res = res.reindex(columns=cols)
    res.to_csv(args.output, index=False)
    logging.info(f"Gemt: {args.output}")

    summary(res)


if __name__ == "__main__":
    main()
