# align_baseline_equity.py
# Aligner baseline- og equity-evalueringer saa de har identisk observationsunivers.
#
# To niveauer af alignment:
#   1) Raekke-intersection paa (forecast_id, horizon, source).
#   2) Per metric: hvis _error er NaN i EN af de to CSVs, saettes BAADE _error og _bias
#      til NaN i BEGGE CSVs. Saa matcher N praecist pr. metric.
#
# Input (produceret af evaluate_metrics_new.py og evaluate_metrics_equity_new.py):
#   - evaluation_results_yoy_growth.csv         (baseline)
#   - evaluation_results_yoy_growth_equity.csv  (equity)
#
# Output:
#   - evaluation_results_yoy_growth_aligned.csv
#   - evaluation_results_yoy_growth_equity_aligned.csv

import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BASELINE_IN = "evaluation_results_yoy_growth.csv"
EQUITY_IN = "evaluation_results_yoy_growth_equity.csv"
BASELINE_OUT = "evaluation_results_yoy_growth_aligned.csv"
EQUITY_OUT = "evaluation_results_yoy_growth_equity_aligned.csv"

METRICS = ["eps", "revenue_growth", "asset_turnover", "ebit_margin"]
KEY = ["forecast_id", "horizon", "source"]


def main():
    b = pd.read_csv(BASELINE_IN)
    e = pd.read_csv(EQUITY_IN)
    logging.info(f"Input:  baseline={len(b):,} rows   equity={len(e):,} rows")

    # 1) Raekke-intersection paa (forecast_id, horizon, source)
    b_key = b[KEY].apply(tuple, axis=1)
    e_key = e[KEY].apply(tuple, axis=1)
    common = set(b_key) & set(e_key)
    only_b = set(b_key) - common
    only_e = set(e_key) - common
    logging.info(f"  kun i baseline: {len(only_b):,}   kun i equity: {len(only_e):,}   i begge: {len(common):,}")

    b = b[b_key.isin(common)].copy()
    e = e[e_key.isin(common)].copy()

    # Sorter og verificer at noeglerne nu er identiske raekke-for-raekke
    b = b.sort_values(KEY).reset_index(drop=True)
    e = e.sort_values(KEY).reset_index(drop=True)
    if not (b[KEY].values == e[KEY].values).all():
        raise RuntimeError("Noegle-alignment fejlede efter sortering")

    # 2) Per metric: intersektion af non-NaN observationer
    for m in METRICS:
        err = f"{m}_error"
        bias = f"{m}_bias"
        mask = b[err].isna() | e[err].isna()  # hvor EN af dem er NaN -> drop begge
        b.loc[mask, [err, bias]] = np.nan
        e.loc[mask, [err, bias]] = np.nan
        logging.info(f"  {m:<16} aligned N = {(~mask).sum():,}")

    b.to_csv(BASELINE_OUT, index=False)
    e.to_csv(EQUITY_OUT, index=False)
    logging.info(f"Gemt: {BASELINE_OUT}")
    logging.info(f"Gemt: {EQUITY_OUT}")

    # Summary: samme N pr. (metric, source, horizon) for baseline og equity
    print("\n=== ALIGNED SUMMARY (N matcher pr. metric*source*horizon) ===")
    for m in METRICS:
        err = f"{m}_error"
        bias = f"{m}_bias"
        print(f"\n{m}:")
        for src in ("llm", "analytiker"):
            for h in (1, 2, 3):
                bsub = b[(b.source == src) & (b.horizon == h) & b[err].notna()]
                esub = e[(e.source == src) & (e.horizon == h) & e[err].notna()]
                if len(bsub) != len(esub):
                    raise RuntimeError(f"N mismatch efter alignment: {m} {src} h={h} "
                                       f"baseline={len(bsub)} equity={len(esub)}")
                n = len(bsub)
                if not n:
                    continue
                print(f"  {src:<10} h={h}  N={n:>4}  "
                      f"baseline err={bsub[err].mean():.4f} bias={bsub[bias].mean():+.4f}  |  "
                      f"equity  err={esub[err].mean():.4f} bias={esub[bias].mean():+.4f}")


if __name__ == "__main__":
    main()
