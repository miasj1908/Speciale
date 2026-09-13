# persistence_of_coherence.py
#
# Beregner persistence af coherence:
#   P(pass at horizon t+3 | pass at horizon t+2)
# vs.
#   P(pass at horizon t+3)
#
# Tilpasset Vang (2025) Tables 12-14, men med h=2 som betingelses-
# horisont i stedet for h=1, da analysen er begrænset til h=2 og h=3
# (samme horisontvalg som accuracy-analysen).
#
# Konceptuelt tester vi det samme: om kohaerens, naar etableret paa
# første tilgaengelige forecast-horisont, fastholdes paa den efter-
# foelgende horisont.
#
# Bemaerk: Da bade LLM og analytiker har data ved h=2 og h=3, kan
# vi inkludere alle tre kilder i denne analyse (modsat den oprindelige
# Peter-tilgang hvor analytiker manglede h=1).
#
# Output:
#   - persistence_results.csv (alle data)
#   - table_persistence_<check>.tex (én pr. tjek)

import pandas as pd
import numpy as np

INPUT_FILE = "coherence_results_v3_clean.csv"

LEVEL1_CHECKS = [
    ("IS_gross_profit", "Gross Profit identity"),
    ("BS_identity",     "Balance Sheet identity"),
    ("CFS_identity",    "Cash Flow identity"),
]

LEVEL2_CHECKS = [
    ("IS_net_income",     "Net Income (full equation)"),
    ("BS_equity_decomp",  "Equity decomposition"),
    ("X_ni_match",        "Net Income consistency"),
    ("X_div_match",       "Dividends consistency"),
]

ALL_CHECKS = LEVEL1_CHECKS + LEVEL2_CHECKS

# Inkluderer analytiker da begge horisonter (h=2, h=3) er tilgaengelige
SOURCE_NAMES = ["P1 baseline", "P2 equity", "Analytiker"]
SOURCE_CRITERIA = {
    "P1 baseline":  {"source": "llm", "prompt_template": "baseline"},
    "P2 equity":    {"source": "llm", "prompt_template": "equity"},
    "Analytiker":   {"source": "analytiker"},
}

# h=2 som betingelseshorisont, h=3 som fremtidig
CONDITIONING_HORIZON = 2
FUTURE_HORIZONS = [3]


def filter_source(df, criteria):
    out = df.copy()
    for col, val in criteria.items():
        out = out[out[col] == val]
    return out


def compute_persistence(df, check_key, source_name):
    crit = SOURCE_CRITERIA[source_name]
    d = filter_source(df[df["check"] == check_key], crit)

    rows = []

    for h_future in FUTURE_HORIZONS:
        h_cond = d[d["horizon"] == CONDITIONING_HORIZON][
            ["forecast_id", "within_tolerance"]
        ].rename(columns={"within_tolerance": "pass_cond"})

        h_fut = d[d["horizon"] == h_future][
            ["forecast_id", "within_tolerance"]
        ].rename(columns={"within_tolerance": "pass_hfut"})

        joined = h_cond.merge(h_fut, on="forecast_id", how="inner")

        if len(joined) == 0:
            rows.append({
                "Tjek": check_key,
                "Source": source_name,
                "Horizon": f"t+{h_future}",
                "N": 0,
                "P(pass h_fut | pass h_cond)": np.nan,
                "P(pass h_fut)": np.nan,
                "N pass h_cond": 0,
            })
            continue

        passed_cond = joined[joined["pass_cond"] == True]
        cond_prob = passed_cond["pass_hfut"].mean() if len(passed_cond) > 0 else np.nan
        uncond_prob = joined["pass_hfut"].mean()

        rows.append({
            "Tjek": check_key,
            "Source": source_name,
            "Horizon": f"t+{h_future}",
            "N": len(joined),
            "P(pass h_fut | pass h_cond)": cond_prob,
            "P(pass h_fut)": uncond_prob,
            "N pass h_cond": int(len(passed_cond)),
        })

    return pd.DataFrame(rows)


def format_prob(p):
    if pd.isna(p):
        return "—"
    return f"{p:.3f}".replace(".", ",")


def print_persistence_table(check_key, check_label, df_persistence):
    print("\n" + "="*90)
    print(f"  {check_label} ({check_key})")
    print("="*90)
    print(f"{'Source':<16}{'Horizon':<10}{'P(pass | pass t+2)':>22}{'P(pass)':>15}{'N':>10}")
    print("-"*90)

    for source in SOURCE_NAMES:
        sub = df_persistence[df_persistence["Source"] == source]
        for _, r in sub.iterrows():
            print(f"{source:<16}{r['Horizon']:<10}"
                  f"{format_prob(r['P(pass h_fut | pass h_cond)']):>22}"
                  f"{format_prob(r['P(pass h_fut)']):>15}"
                  f"{r['N']:>10}")
        print()


def save_persistence_latex(check_key, check_label, df_persistence, label_prefix):
    lines = [
        r"\begin{table}[ht]",
        r"\centering",
        rf"\caption{{Persistence of {check_label.lower()}: probability of passing at $t+3$ given pass at $t+2$}}",
        rf"\label{{tab:persistence_{label_prefix}}}",
        r"\begin{tabular}{llrrr}",
        r"\toprule",
        r"\textbf{Source} & \textbf{Horizon} & "
        r"$\mathbf{P(pass_{t+3} | pass_{t+2})}$ & "
        r"$\mathbf{P(pass_{t+3})}$ & \textbf{N} \\",
        r"\midrule",
    ]

    current_source = None
    for _, r in df_persistence.iterrows():
        if current_source is not None and r["Source"] != current_source:
            lines.append(r"\midrule")
        source_str = r["Source"] if r["Source"] != current_source else ""
        current_source = r["Source"]

        n_str = f"{r['N']:,}".replace(",", r"\,")
        lines.append(
            f"{source_str} & {r['Horizon']} & "
            f"{format_prob(r['P(pass h_fut | pass h_cond)'])} & "
            f"{format_prob(r['P(pass h_fut)'])} & "
            f"{n_str} \\\\"
        )

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\small",
        r"\item \textit{Note:} Conditional probability is computed over forecasts that "
        r"have observations at both $t+2$ and $t+3$. The unconditional probability "
        r"$P(pass_{t+3})$ is computed over the same set of forecasts. "
        r"$N$ refers to the number of forecasts with data at both horizons.",
        r"\end{tablenotes}",
        r"\end{table}",
    ]

    fname = f"table_persistence_{label_prefix}.tex"
    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Gemt: {fname}")


def main():
    df = pd.read_csv(INPUT_FILE)
    print(f"Indlaest {len(df):,} obs. fra {INPUT_FILE}")

    available_checks = df["check"].unique()
    print(f"\nTilgaengelige tjek: {sorted(available_checks)}")
    print(f"Horisonter i data: {sorted(df['horizon'].unique())}")

    print("\n" + "="*90)
    print("PERSISTENCE OF COHERENCE")
    print(f"P(pass ved t+3 | pass ved t+2) vs P(pass ved t+3)")
    print("Tilpasset Vang (2025) Tables 12-14")
    print("="*90)

    all_results = []

    for check_key, check_label in ALL_CHECKS:
        if check_key not in available_checks:
            print(f"\n[SKIP] {check_key}: Tjek ikke i data.")
            continue

        check_results = []
        for source_name in SOURCE_NAMES:
            source_results = compute_persistence(df, check_key, source_name)
            check_results.append(source_results)

        check_df = pd.concat(check_results, ignore_index=True)
        all_results.append(check_df)

        print_persistence_table(check_key, check_label, check_df)
        save_persistence_latex(check_key, check_label, check_df,
                               check_key.lower())

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        combined.to_csv("persistence_results.csv", index=False,
                        float_format="%.4f", decimal=",", sep=";")
        print(f"\nGemt: persistence_results.csv")

    print("\n" + "="*90)
    print("Faerdig.")
    print("="*90)


if __name__ == "__main__":
    main()
