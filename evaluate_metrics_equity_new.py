# evaluate_metrics_new.py
# Thesis-evaluation: EPS, Revenue Growth (YoY), Asset Turnover, EBIT Margin.
#
# Forskelle fra evaluate_metrics.py:
#  - Revenue Growth = YoY ((Rev_fy / Rev_{fy-1}) - 1) i stedet for kumulativ
#  - Bulk-query (én DB round-trip) -> ~30s i stedet for 20+ min
#  - Outlier-filter (>95 percentil pr. metric*horizon) bevares
#
# Output: evaluation_results_yoy_growth.csv

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

PROMPT = "equity"
MODEL = "gpt-5.1"
EFFORT = "medium"

OUTPUT_FILE = "evaluation_results_yoy_growth_equity.csv"


def fetch_all():
    conn = psycopg2.connect(**DB_PARAMS)
    q = """
    SELECT r.forecast_id, f.selskab_id,
           MAX(CASE WHEN r.datakilde='historisk' THEN r.årstal END)
               OVER (PARTITION BY r.forecast_id) AS t0_year,
           r.datakilde, r.line_item, r.årstal, r.værdi
    FROM regnskabsdata r
    JOIN forecasts f ON f.forecast_id = r.forecast_id
    WHERE r.line_item IN ('Revenue','EBIT','Net_Income','Shares_Diluted','Total_Assets')
      AND (
           (r.datakilde='llm' AND r.prompt_template=%s AND r.model_name=%s AND r.reasoning_effort=%s)
           OR r.datakilde IN ('analytiker','realiseret','historisk')
      )
    """
    df = pd.read_sql_query(q, conn, params=[PROMPT, MODEL, EFFORT])
    conn.close()
    return df


def build_actual_lookup(df):
    """(selskab_id, line_item, år) -> faktisk vaerdi.
    Bygges fra 'realiseret' (foretrukket) og 'historisk' (fallback) paa tvaers
    af alle forecasts for samme selskab. Det giver os fx Revenue ved t0+1
    selv naar denne forecasts egen realiseret starter ved t0+2.
    """
    lookup = {}
    # historisk foerst (lavere prioritet)
    hist = df[df["datakilde"] == "historisk"]
    for _, r in hist.iterrows():
        key = (r["selskab_id"], r["line_item"], r["årstal"])
        lookup[key] = r["værdi"]
    # realiseret overskriver historisk (hvis begge findes, stol paa realiseret)
    real = df[df["datakilde"] == "realiseret"]
    for _, r in real.iterrows():
        key = (r["selskab_id"], r["line_item"], r["årstal"])
        lookup[key] = r["værdi"]
    return lookup


def compute_errors(df, actual_lookup):
    rows = []
    for fid, g in df.groupby("forecast_id"):
        t0_year = g["t0_year"].dropna().iloc[0] if g["t0_year"].notna().any() else None
        if t0_year is None:
            continue
        t0_year = int(t0_year)
        selskab = g["selskab_id"].iloc[0]

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

        def actual(item, year):
            """Faktisk vaerdi: denne forecasts realiseret foerst, ellers cross-forecast lookup."""
            v = val("realiseret", item, year)
            if pd.notna(v):
                return v
            v = val("historisk", item, year)
            if pd.notna(v):
                return v
            return actual_lookup.get((selskab, item, year), np.nan)

        def source_or_actual(source, item, year):
            """Source's egen vaerdi hvis tilgaengelig, ellers faktisk vaerdi som fallback."""
            v = val(source, item, year)
            if pd.notna(v):
                return v
            return actual(item, year)

        for h in (1, 2, 3):
            fy = t0_year + h
            prev_fy = fy - 1
            for src in ("llm", "analytiker"):
                # _bias = signed (forecast - actual): positiv = overbias, negativ = underbias
                # _error = abs(_bias)
                entry = {"forecast_id": fid, "horizon": h, "source": src,
                         "eps_bias": np.nan, "revenue_growth_bias": np.nan,
                         "asset_turnover_bias": np.nan, "ebit_margin_bias": np.nan,
                         "eps_error": np.nan, "revenue_growth_error": np.nan,
                         "asset_turnover_error": np.nan, "ebit_margin_error": np.nan}

                # EPS
                ni_f = val(src, "Net_Income", fy)
                sh_f = val(src, "Shares_Diluted", fy)
                ni_a = actual("Net_Income", fy)
                sh_a = actual("Shares_Diluted", fy)
                if all(pd.notna(x) for x in [ni_f, sh_f, ni_a, sh_a]) and sh_f and sh_a:
                    entry["eps_bias"] = (ni_f / sh_f) - (ni_a / sh_a)
                    entry["eps_error"] = abs(entry["eps_bias"])

                # Revenue Growth (YoY). prev_fy hentes fra source hvis muligt, ellers faktisk.
                rev_f = val(src, "Revenue", fy)
                rev_r = actual("Revenue", fy)
                prev_f = source_or_actual(src, "Revenue", prev_fy)
                prev_r = actual("Revenue", prev_fy)
                if all(pd.notna(x) for x in [rev_f, rev_r, prev_f, prev_r]) and prev_f and prev_r:
                    entry["revenue_growth_bias"] = (rev_f / prev_f - 1) - (rev_r / prev_r - 1)
                    entry["revenue_growth_error"] = abs(entry["revenue_growth_bias"])

                # Asset Turnover
                ta_f = val(src, "Total_Assets", fy)
                ta_r = actual("Total_Assets", fy)
                if all(pd.notna(x) for x in [rev_f, ta_f, rev_r, ta_r]) and ta_f and ta_r:
                    entry["asset_turnover_bias"] = (rev_f / ta_f) - (rev_r / ta_r)
                    entry["asset_turnover_error"] = abs(entry["asset_turnover_bias"])

                # EBIT Margin
                eb_f = val(src, "EBIT", fy)
                eb_r = actual("EBIT", fy)
                if all(pd.notna(x) for x in [eb_f, rev_f, eb_r, rev_r]) and rev_f and rev_r:
                    entry["ebit_margin_bias"] = (eb_f / rev_f) - (eb_r / rev_r)
                    entry["ebit_margin_error"] = abs(entry["ebit_margin_bias"])

                if any(pd.notna(entry[k]) for k in
                       ("eps_error","revenue_growth_error","asset_turnover_error","ebit_margin_error")):
                    rows.append(entry)

    return pd.DataFrame(rows)


def remove_outliers(df, percentile=95):
    # Outlier defineres paa _error (absolut). Tilsvarende _bias saettes ogsaa til NaN,
    # saa _error og _bias deler samme NaN-moenster.
    result = df.copy()
    for metric in ["eps_error", "revenue_growth_error", "asset_turnover_error", "ebit_margin_error"]:
        bias_col = metric.replace("_error", "_bias")
        for horizon in [1, 2, 3]:
            subset = df[(df["horizon"] == horizon) & (df[metric].notna())]
            if len(subset) > 0:
                p95 = subset[metric].quantile(percentile / 100)
                mask = (result["horizon"] == horizon) & (result[metric] > p95)
                result.loc[mask, metric] = np.nan
                result.loc[mask, bias_col] = np.nan
    return result


def main():
    logging.info(f"Henter data (prompt={PROMPT}, model={MODEL}, effort={EFFORT})...")
    df = fetch_all()
    logging.info(f"  {len(df):,} rows, {df['forecast_id'].nunique():,} forecasts")

    logging.info("Bygger cross-forecast actuals lookup (selskab_id, line_item, aar)...")
    actual_lookup = build_actual_lookup(df)
    logging.info(f"  {len(actual_lookup):,} unikke (selskab, line_item, aar)-vaerdier")

    logging.info("Beregner fejl (EPS, YoY rev-growth, AT, EBIT margin)...")
    err = compute_errors(df, actual_lookup)
    logging.info(f"  {len(err):,} forecast*horizon*source raekker")

    logging.info("Fjerner outliers (>95 percentil pr. metric*horizon)...")
    err_clean = remove_outliers(err)
    n_removed = (err != err_clean).sum().sum()
    logging.info(f"  {n_removed:,} cellevaerdier sat til NaN")

    cols = ["forecast_id","horizon","source",
            "eps_error","eps_bias",
            "revenue_growth_error","revenue_growth_bias",
            "asset_turnover_error","asset_turnover_bias",
            "ebit_margin_error","ebit_margin_bias"]
    err_clean = err_clean.reindex(columns=cols)
    err_clean.to_csv(OUTPUT_FILE, index=False)
    logging.info(f"Gemt: {OUTPUT_FILE}")

    print("\n=== SUMMARY STATISTICS (outliers fjernet) ===")
    for metric in ["eps", "revenue_growth", "asset_turnover", "ebit_margin"]:
        err = f"{metric}_error"
        bias = f"{metric}_bias"
        print(f"\n{metric}:")
        for source in ["llm", "analytiker"]:
            for h in (1, 2, 3):
                sub = err_clean[(err_clean["source"]==source) & (err_clean["horizon"]==h) & err_clean[err].notna()]
                if len(sub):
                    print(f"  {source:<10} h={h}  N={len(sub):>4}  "
                          f"mean_err={sub[err].mean():.4f}  median_err={sub[err].median():.4f}  "
                          f"mean_bias={sub[bias].mean():+.4f}  median_bias={sub[bias].median():+.4f}")


if __name__ == "__main__":
    main()
