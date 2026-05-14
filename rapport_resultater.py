"""
Generer resultat-tabeller til specialet i stil med Peter Vangs (2025).

Per metric (EPS, Revenue Growth, Asset Turnover, EBIT Margin) og per
horisont (t+1, t+2, t+3) rapporteres:
  - MAE (mean absolute error)
  - Median |error|
  - IQR af |error|
  - Bias = Mean(signed error) — positiv = overvurdering, negativ = undervurdering
  - N (antal parrede observationer)
  - % hvor LLM slaar analytiker (mindre |error|)
  - Wilcoxon signed-rank p (paa |error|)
  - Paired t-test p (paa |error|)

Outliers (>95. percentil af |error|, per metric × horisont × source)
droppes foer aggregat — identisk metode som Peter.

Koersel:
  python rapport_resultater.py --prompt baseline --effort medium
  python rapport_resultater.py --prompt equity   --effort medium
"""
import argparse
import logging
import psycopg2
import pandas as pd
import numpy as np
from scipy.stats import wilcoxon, ttest_rel

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DB_PARAMS = {
    "dbname": "forecast_studie", "user": "postgres",
    "password": "Miamia97!", "host": "localhost", "port": 5432,
}

METRICS = [
    ("EPS", "eps_error"),
    ("Revenue Growth", "revenue_growth_error"),
    ("Asset Turnover", "asset_turnover_error"),
    ("EBIT Margin", "ebit_margin_error"),
]


def fetch_data(prompt_template, effort):
    conn = psycopg2.connect(**DB_PARAMS)
    query = """
    SELECT forecast_id, sektion, line_item, årstal, værdi, datakilde
    FROM regnskabsdata
    WHERE line_item IN ('Revenue','EBIT','Net_Income','Shares_Diluted','Total_Assets','EPS_GAAP')
      AND (
          (datakilde = 'llm' AND prompt_template = %s AND reasoning_effort = %s)
          OR datakilde IN ('analytiker','realiseret','historisk')
      )
    """
    df = pd.read_sql_query(query, conn, params=[prompt_template, effort])
    t0 = pd.read_sql_query(
        "SELECT forecast_id, MAX(årstal) AS t0 FROM regnskabsdata WHERE datakilde='historisk' GROUP BY forecast_id",
        conn,
    )
    conn.close()
    return df, t0


def compute_errors(df, t0):
    """
    Signerede fejl per forecast × horizon × source.
    Revenue Growth: AAR-OVER-AAR vækst (matcher Peters eq. 4).
      h=1: (Rev_t+1 / Rev_t0) - 1
      h=2: (Rev_t+2 / Rev_t+1) - 1
      h=3: (Rev_t+3 / Rev_t+2) - 1
    """
    df = df.merge(t0, on="forecast_id", how="left").dropna(subset=["t0"])
    df["t0"] = df["t0"].astype(int)

    rows = []
    for fid, g in df.groupby("forecast_id"):
        t0_year = int(g["t0"].iloc[0])
        pivot = g.pivot_table(
            index=["datakilde", "line_item"], columns="årstal",
            values="værdi", aggfunc="first",
        )

        def val(source, item, year):
            try:
                return pivot.loc[(source, item), year]
            except KeyError:
                return np.nan

        for h in (1, 2, 3):
            fy = t0_year + h
            for src in ("llm", "analytiker"):
                entry = {"forecast_id": fid, "horizon": h, "source": src}

                # --- EPS (signeret: forecast - actual) ---
                ni_f, sh_f = val(src, "Net_Income", fy), val(src, "Shares_Diluted", fy)
                ni_a, sh_a = val("realiseret", "Net_Income", fy), val("realiseret", "Shares_Diluted", fy)
                if pd.notna(ni_f) and pd.notna(sh_f) and pd.notna(ni_a) and pd.notna(sh_a) and sh_f and sh_a:
                    entry["eps_error"] = (ni_f / sh_f) - (ni_a / sh_a)

                # --- Revenue growth (YoY, Peters eq. 4) ---
                rev_f_h = val(src, "Revenue", fy)
                rev_real_h = val("realiseret", "Revenue", fy)
                if h == 1:
                    prev_f = val("historisk", "Revenue", t0_year)
                    prev_real = val("historisk", "Revenue", t0_year)
                else:
                    prev_f = val(src, "Revenue", fy - 1)
                    prev_real = val("realiseret", "Revenue", fy - 1)
                if all(pd.notna(x) for x in [rev_f_h, rev_real_h, prev_f, prev_real]) and prev_f and prev_real:
                    g_f = rev_f_h / prev_f - 1
                    g_r = rev_real_h / prev_real - 1
                    entry["revenue_growth_error"] = g_f - g_r

                # --- Asset Turnover ---
                ta_f = val(src, "Total_Assets", fy)
                ta_real = val("realiseret", "Total_Assets", fy)
                if all(pd.notna(x) for x in [rev_f_h, ta_f, rev_real_h, ta_real]) and ta_f and ta_real:
                    entry["asset_turnover_error"] = (rev_f_h / ta_f) - (rev_real_h / ta_real)

                # --- EBIT margin ---
                eb_f = val(src, "EBIT", fy)
                eb_real = val("realiseret", "EBIT", fy)
                if all(pd.notna(x) for x in [eb_f, rev_f_h, eb_real, rev_real_h]) and rev_f_h and rev_real_h:
                    entry["ebit_margin_error"] = (eb_f / rev_f_h) - (eb_real / rev_real_h)

                if any(k in entry for k in ["eps_error","revenue_growth_error","asset_turnover_error","ebit_margin_error"]):
                    rows.append(entry)

    return pd.DataFrame(rows)


def drop_outliers(df, pct=95):
    """Drop observations hvor |error| > 95-percentilen, per metric × horizon × source (Peters metode)."""
    out = df.copy()
    for _, col in METRICS:
        if col not in out:
            continue
        for h in (1, 2, 3):
            for src in ("llm", "analytiker"):
                mask = (out["horizon"] == h) & (out["source"] == src) & out[col].notna()
                if mask.sum() > 0:
                    abs_err = out.loc[mask, col].abs()
                    cutoff = abs_err.quantile(pct / 100)
                    drop = mask & (out[col].abs() > cutoff)
                    out.loc[drop, col] = np.nan
    return out


def build_tables(df):
    tables = {}
    for name, col in METRICS:
        rows = []
        for h in (1, 2, 3):
            llm = df[(df["source"] == "llm") & (df["horizon"] == h)][["forecast_id", col]].set_index("forecast_id")
            ana = df[(df["source"] == "analytiker") & (df["horizon"] == h)][["forecast_id", col]].set_index("forecast_id")
            paired = llm.join(ana, lsuffix="_llm", rsuffix="_ana").dropna()
            n = len(paired)
            if n == 0:
                rows.append([f"t+{h}"] + [np.nan] * 11 + [0])
                continue

            x_signed = paired[f"{col}_llm"].values
            y_signed = paired[f"{col}_ana"].values
            x_abs = np.abs(x_signed)
            y_abs = np.abs(y_signed)

            mae_l, med_l = x_abs.mean(), np.median(x_abs)
            iqr_l = np.percentile(x_abs, 75) - np.percentile(x_abs, 25)
            bias_l = x_signed.mean()

            mae_a, med_a = y_abs.mean(), np.median(y_abs)
            iqr_a = np.percentile(y_abs, 75) - np.percentile(y_abs, 25)
            bias_a = y_signed.mean()

            pct_llm_wins = (x_abs < y_abs).mean() * 100
            try:
                _, p_w = wilcoxon(x_abs, y_abs)
            except ValueError:
                p_w = np.nan
            try:
                _, p_t = ttest_rel(x_abs, y_abs)
            except Exception:
                p_t = np.nan

            rows.append([
                f"t+{h}",
                mae_l, med_l, iqr_l, bias_l,
                mae_a, med_a, iqr_a, bias_a,
                pct_llm_wins, p_w, p_t, n,
            ])
        tables[name] = pd.DataFrame(rows, columns=[
            "Horizon",
            "MAE (LLM)", "Median |err| (LLM)", "IQR (LLM)", "Bias (LLM)",
            "MAE (Ana)", "Median |err| (Ana)", "IQR (Ana)", "Bias (Ana)",
            "% LLM wins", "Wilcoxon p", "t-test p", "N",
        ])
    return tables


def format_table(df):
    d = df.copy()
    for c in d.columns:
        if c in ("Horizon", "N"):
            continue
        if c in ("% LLM wins",):
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:.1f}%")
        elif "p" in c:
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else (f"{v:.2e}" if v < 1e-4 else f"{v:.4f}"))
        else:
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:.4f}")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True, help="prompt_template (baseline | equity)")
    ap.add_argument("--effort", default="medium", help="reasoning_effort (low | medium | high)")
    ap.add_argument("--output-prefix", default=None)
    args = ap.parse_args()

    prefix = args.output_prefix or f"rapport_{args.prompt}_{args.effort}"

    logging.info(f"Hent data for prompt={args.prompt}, effort={args.effort}...")
    df, t0 = fetch_data(args.prompt, args.effort)
    logging.info(f"{len(df):,} rows, {len(t0):,} forecasts")

    logging.info("Beregn fejl (signerede)...")
    err = compute_errors(df, t0)
    logging.info(f"{len(err):,} forecast×horizon×source rows")

    err_clean = drop_outliers(err, pct=95)
    tables = build_tables(err_clean)

    print("\n" + "=" * 110)
    print(f"RESULTATER: prompt={args.prompt}, effort={args.effort}")
    print("=" * 110)
    all_rows = []
    for name, tbl in tables.items():
        print(f"\n{name}")
        print(format_table(tbl).to_string(index=False))
        tbl_out = tbl.copy()
        tbl_out.insert(0, "Metric", name)
        all_rows.append(tbl_out)

    combined = pd.concat(all_rows, ignore_index=True)
    combined.to_csv(f"{prefix}.csv", index=False)
    print(f"\nGemt: {prefix}.csv")


if __name__ == "__main__":
    main()
