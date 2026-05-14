# tables_main_coherence.py
#
# Genererer hovedtabeller og figurer til coherence-sektionen i specialet.
# Folger Vang (2025) §5.3 struktur:
#
#   Tabel X1:  Pass rates pr. tjek pr. horisont pr. kilde (hovedtabel)
#              Med signifikansstjerner vs. naest-bedste kilde (Peter-stil).
#   Tabel X1b: Direkte hypotesetest P1 vs P2 med Wilcoxon + t-test.
#   Tabel X2:  Gennemsnitlig rank pr. horisont (Peter's Table 10)
#   Tabel X3:  Gennemsnitlig rank pr. tjek (Peter's Table 11)
#   Figur 1-3: Pass rates pr. horisont pr. kilde (Peter's Figur 10-12)
#
# Tre kilder: P1 baseline, P2 equity, Analytiker (uden realiseret).
# Signifikansstjerner: * p<0,05, ** p<0,01 (Wilcoxon Signed-Rank og paired
# t-test paa signerede residualer, parret paa forecast_id inden for horisont).

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import stats

INPUT_FILE = "coherence_results_v3.csv"

CHECKS = [
    ("IS_gross_profit", "Income check",   "Gross Profit-identitet"),
    ("BS_identity",     "Balance check",  "Balance Sheet-identitet"),
    ("CFS_identity",    "Cash check",     "Cash Flow-identitet"),
]

SOURCE_NAMES = ["P1 baseline", "P2 equity", "Analytiker"]
SOURCE_CRITERIA = {
    "P1 baseline":  {"source": "llm", "prompt_template": "baseline"},
    "P2 equity":    {"source": "llm", "prompt_template": "equity"},
    "Analytiker":   {"source": "analytiker"},
}

HORIZONS = [2, 3]


# ============================================================
# HJAELPEFUNKTIONER
# ============================================================

def filter_source(df, criteria):
    out = df.copy()
    for col, val in criteria.items():
        out = out[out[col] == val]
    return out


def pass_rate(df, check_key, source_crit, horizon=None):
    d = df[df["check"] == check_key]
    d = filter_source(d, source_crit)
    if horizon is not None:
        d = d[d["horizon"] == horizon]
    if len(d) == 0:
        return np.nan, 0
    return d["within_tolerance"].mean() * 100, len(d)


def pair_residuals(df, check_key, crit_a, crit_b, horizon=None):
    """
    Parret DataFrame med signerede residualer fra to kilder.
    Parring sker altid paa (forecast_id, horizon) for at undgaa cross-joins.
    Hvis horizon angives, filtreres der foerst paa den.
    """
    d = df[df["check"] == check_key]
    if horizon is not None:
        d = d[d["horizon"] == horizon]

    a = filter_source(d, crit_a)[["forecast_id", "horizon", "residual_pct_signed"]] \
        .rename(columns={"residual_pct_signed": "res_a"})
    b = filter_source(d, crit_b)[["forecast_id", "horizon", "residual_pct_signed"]] \
        .rename(columns={"residual_pct_signed": "res_b"})
    # Vigtigt: merge paa BEGGE forecast_id OG horizon for at undgaa cross-joins
    paired = a.merge(b, on=["forecast_id", "horizon"], how="inner")
    paired = paired.dropna(subset=["res_a", "res_b"])
    return paired


def run_tests(paired):
    if len(paired) < 3:
        return np.nan, np.nan, len(paired)
    diff = paired["res_a"] - paired["res_b"]
    if (diff == 0).all():
        return 1.0, 1.0, len(paired)
    try:
        _, p_w = stats.wilcoxon(paired["res_a"], paired["res_b"],
                                 zero_method="wilcox", alternative="two-sided")
    except ValueError:
        p_w = np.nan
    try:
        _, p_t = stats.ttest_rel(paired["res_a"], paired["res_b"])
    except Exception:
        p_t = np.nan
    return p_w, p_t, len(paired)


def sig_stars(p):
    if pd.isna(p):
        return ""
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return ""


def sig_stars_wilcoxon_t(p_w, p_t):
    """
    Separate markeringer for hver test, som Vang (2025) Table 9.
    Wilcoxon-stjerner vises foerst, derefter t-test-stjerner.
    Returnerer tuple: (wilcoxon_str, t_test_str)
    """
    def single(p):
        if pd.isna(p):
            return ""
        if p < 0.01:
            return "**"
        if p < 0.05:
            return "*"
        return ""
    return single(p_w), single(p_t)


def format_pct(v, decimals=1):
    if pd.isna(v):
        return "—"
    return f"{v:.{decimals}f}".replace(".", ",")


def format_pct_with_stars(v, stars, decimals=1):
    if pd.isna(v):
        return "—"
    return f"{format_pct(v, decimals)}{stars}"


def format_diff(v, decimals=1):
    if pd.isna(v):
        return "—"
    sign = "+" if v > 0 else ("" if v == 0 else "−")
    return f"{sign}{abs(v):.{decimals}f}".replace(".", ",")


def format_rank(v, decimals=2):
    if pd.isna(v):
        return "—"
    return f"{v:.{decimals}f}".replace(".", ",")


def format_p(p):
    if pd.isna(p):
        return "—"
    if p < 0.001:
        return "<0,001"
    return f"{p:.3f}".replace(".", ",")


def format_p_with_stars(p):
    if pd.isna(p):
        return "—"
    return format_p(p) + sig_stars(p)


# ============================================================
# TABEL X1
# ============================================================

def compute_stars_for_check_horizon(df, check_key, horizon):
    """
    For hver kilde: beregn separate stjerner for Wilcoxon og t-test
    vs. naest-bedste kilde.
    Returnerer dict: {source_name: {"w": "**", "t": "*"}}
    """
    rates = {}
    for name in SOURCE_NAMES:
        rate, _ = pass_rate(df, check_key, SOURCE_CRITERIA[name], horizon)
        rates[name] = rate

    ranked = sorted(rates.items(),
                    key=lambda x: -x[1] if pd.notna(x[1]) else float('inf'))

    stars = {name: {"w": "", "t": ""} for name in SOURCE_NAMES}
    for i in range(len(ranked) - 1):
        name_i, rate_i = ranked[i]
        name_next, rate_next = ranked[i + 1]
        if pd.isna(rate_i) or pd.isna(rate_next):
            continue
        paired = pair_residuals(df, check_key,
                                 SOURCE_CRITERIA[name_i],
                                 SOURCE_CRITERIA[name_next],
                                 horizon)
        p_w, p_t, _ = run_tests(paired)
        w_str, t_str = sig_stars_wilcoxon_t(p_w, p_t)
        stars[name_i] = {"w": w_str, "t": t_str}
    return stars


def build_table_x1(df):
    rows = []
    for check_key, _, check_full in CHECKS:
        for h in HORIZONS:
            stars = compute_stars_for_check_horizon(df, check_key, h)
            row = {"Tjek": check_full, "Horisont": f"t+{h}"}
            for name in SOURCE_NAMES:
                rate, n = pass_rate(df, check_key, SOURCE_CRITERIA[name], h)
                row[name] = rate
                row[f"N_{name}"] = n
                row[f"wstars_{name}"] = stars[name]["w"]
                row[f"tstars_{name}"] = stars[name]["t"]
            rows.append(row)

    for check_key, _, check_full in CHECKS:
        row = {"Tjek": check_full, "Horisont": "Samlet"}
        for name in SOURCE_NAMES:
            rate, n = pass_rate(df, check_key, SOURCE_CRITERIA[name])
            row[name] = rate
            row[f"N_{name}"] = n
            row[f"wstars_{name}"] = ""
            row[f"tstars_{name}"] = ""
        rows.append(row)

    return pd.DataFrame(rows)


def format_pct_with_two_stars(v, w_stars, t_stars, decimals=1):
    """
    Format pass rate med Wilcoxon og t-test stjerner adskilt.
    Format: "rate [W/t]" hvor W = Wilcoxon stjerner, t = t-test stjerner.
    Eksempler:
      "98,4"          - ingen signifikans
      "98,4 [*/ ]"    - Wilcoxon signif. ved 5%, t-test ikke signif.
      "98,4 [** /*]"  - Wilcoxon signif. ved 1%, t-test signif. ved 5%
    """
    if pd.isna(v):
        return "—"
    num = format_pct(v, decimals)
    if not w_stars and not t_stars:
        return num
    # Format med klart separator
    w_display = w_stars if w_stars else "—"
    t_display = t_stars if t_stars else "—"
    return f"{num} [{w_display}/{t_display}]"


def print_table_x1(table):
    print("\n" + "="*105)
    print("TABEL X1 — Pass rates pr. tjek pr. horisont pr. kilde (%)")
    print("       Signifikansformat: 'rate [W/t]' hvor W = Wilcoxon, t = t-test vs. naest-bedste kilde")
    print("       * p<0,05, ** p<0,01, '—' = ikke signifikant")
    print("="*105)
    print(f"{'Tjek':<26}{'Horisont':<10}{'P1 baseline':>22}{'P2 equity':>22}{'Analytiker':>22}")
    print("-"*105)
    for _, r in table.iterrows():
        is_total = r["Horisont"] == "Samlet"
        if is_total:
            print("-"*105)
        print(f"{r['Tjek']:<26}{r['Horisont']:<10}"
              f"{format_pct_with_two_stars(r['P1 baseline'], r['wstars_P1 baseline'], r['tstars_P1 baseline']):>22}"
              f"{format_pct_with_two_stars(r['P2 equity'], r['wstars_P2 equity'], r['tstars_P2 equity']):>22}"
              f"{format_pct_with_two_stars(r['Analytiker'], r['wstars_Analytiker'], r['tstars_Analytiker']):>22}")


def save_table_x1_latex(table):
    lines = [
        r"\begin{table}[ht]",
        r"\centering",
        r"\caption{Pass rates pr. koh\ae renstjek pr. horisont og kilde (\%)}",
        r"\label{tab:coherence_pass_rates_main}",
        r"\begin{tabular}{llrrr}",
        r"\toprule",
        r"\textbf{Tjek} & \textbf{Horisont} & \textbf{P1 baseline} & "
        r"\textbf{P2 equity} & \textbf{Analytiker} \\",
        r"\midrule",
    ]

    def latex_cell(v, w_stars, t_stars):
        if pd.isna(v):
            return "—"
        num = format_pct(v)
        if not w_stars and not t_stars:
            return num
        # Format: num [W/t] hvor W og t er Wilcoxon/t-test stjerner
        def fmt_star(s):
            if s == "**":
                return r"$^{**}$"
            elif s == "*":
                return r"$^{*}$"
            return "—"
        w_display = fmt_star(w_stars)
        t_display = fmt_star(t_stars)
        return f"{num} [{w_display}/{t_display}]"

    current_check = None
    for _, r in table.iterrows():
        if r["Horisont"] == "Samlet":
            lines.append(r"\cmidrule{1-5}")
        check_str = r["Tjek"] if r["Tjek"] != current_check else ""
        current_check = r["Tjek"]

        p1_str = latex_cell(r["P1 baseline"], r["wstars_P1 baseline"], r["tstars_P1 baseline"])
        p2_str = latex_cell(r["P2 equity"], r["wstars_P2 equity"], r["tstars_P2 equity"])
        an_str = latex_cell(r["Analytiker"], r["wstars_Analytiker"], r["tstars_Analytiker"])

        lines.append(
            f"{check_str} & {r['Horisont']} & {p1_str} & {p2_str} & {an_str} \\\\"
        )
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\small",
        r"\item \textit{Note:} Pass rate er andelen af forecast-observationer hvor den "
        r"absolutte relative residual er under 1\% af en passende skalering. "
        r"Signifikansmarkeringer rapporteres i format \texttt{[W/t]}, hvor W angiver "
        r"Wilcoxon Signed-Rank-testen og t angiver den parret t-test, begge udf\o rt "
        r"p\aa\ signerede residualer mellem kilden og den n\ae st-bedste kilde p\aa\ "
        r"samme horisont ($^{*}$ p $<$ 0{,}05, $^{**}$ p $<$ 0{,}01, ``\textemdash'' = "
        r"ikke signifikant). Analyse baseret p\aa\ h=2 og h=3.",
        r"\end{tablenotes}",
        r"\end{table}"
    ]
    with open("table_x1_pass_rates_main.tex", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Gemt: table_x1_pass_rates_main.tex")


# ============================================================
# TABEL X1b
# ============================================================

def build_table_x1b(df):
    rows = []
    p1_crit = SOURCE_CRITERIA["P1 baseline"]
    p2_crit = SOURCE_CRITERIA["P2 equity"]

    for check_key, _, check_full in CHECKS:
        for h in HORIZONS:
            p1_rate, _ = pass_rate(df, check_key, p1_crit, h)
            p2_rate, _ = pass_rate(df, check_key, p2_crit, h)
            diff = p2_rate - p1_rate if pd.notna(p1_rate) and pd.notna(p2_rate) else np.nan
            paired = pair_residuals(df, check_key, p1_crit, p2_crit, h)
            p_w, p_t, n_par = run_tests(paired)
            rows.append({
                "Tjek": check_full, "Horisont": f"t+{h}",
                "P1 %": p1_rate, "P2 %": p2_rate, "Diff (pp)": diff,
                "N_paired": n_par, "p_wilcoxon": p_w, "p_t_test": p_t,
            })

        p1_rate, _ = pass_rate(df, check_key, p1_crit)
        p2_rate, _ = pass_rate(df, check_key, p2_crit)
        diff = p2_rate - p1_rate if pd.notna(p1_rate) and pd.notna(p2_rate) else np.nan
        paired = pair_residuals(df, check_key, p1_crit, p2_crit)
        p_w, p_t, n_par = run_tests(paired)
        rows.append({
            "Tjek": check_full, "Horisont": "Samlet",
            "P1 %": p1_rate, "P2 %": p2_rate, "Diff (pp)": diff,
            "N_paired": n_par, "p_wilcoxon": p_w, "p_t_test": p_t,
        })

    return pd.DataFrame(rows)


def print_table_x1b(table):
    print("\n" + "="*100)
    print("TABEL X1b — Direkte hypotesetest: P1 vs P2")
    print("       Wilcoxon og paired t-test paa signerede residualer.")
    print("       * p<0,05, ** p<0,01")
    print("="*100)
    print(f"{'Tjek':<26}{'Horisont':<10}{'P1 %':>8}{'P2 %':>8}{'Diff':>8}"
          f"{'N_par':>8}{'p-Wilcox':>14}{'p-t-test':>14}")
    print("-"*100)
    current_check = None
    for _, r in table.iterrows():
        is_total = r["Horisont"] == "Samlet"
        check_str = r["Tjek"] if r["Tjek"] != current_check else ""
        current_check = r["Tjek"]
        print(f"{check_str:<26}{r['Horisont']:<10}"
              f"{format_pct(r['P1 %']):>8}"
              f"{format_pct(r['P2 %']):>8}"
              f"{format_diff(r['Diff (pp)']):>8}"
              f"{r['N_paired']:>8}"
              f"{format_p_with_stars(r['p_wilcoxon']):>14}"
              f"{format_p_with_stars(r['p_t_test']):>14}")
        if is_total:
            print()


def save_table_x1b_latex(table):
    lines = [
        r"\begin{table}[ht]",
        r"\centering",
        r"\caption{Hypotesetest P1 vs P2: Pass rates og statistiske tests}",
        r"\label{tab:hypothesis_test_p1_p2}",
        r"\begin{tabular}{llrrrrrr}",
        r"\toprule",
        r"\textbf{Tjek} & \textbf{Horisont} & \textbf{P1 \%} & \textbf{P2 \%} & "
        r"\textbf{Diff (pp)} & \textbf{N$_{par}$} & "
        r"\textbf{p$_{Wilcoxon}$} & \textbf{p$_{t-test}$} \\",
        r"\midrule",
    ]

    def latex_p(p):
        if pd.isna(p):
            return "—"
        val = format_p(p)
        s = sig_stars(p)
        if s == "**":
            return f"{val}$^{{**}}$"
        elif s == "*":
            return f"{val}$^{{*}}$"
        return val

    current_check = None
    for _, r in table.iterrows():
        if current_check is not None and r["Tjek"] != current_check:
            lines.append(r"\midrule")
        check_str = r["Tjek"] if r["Tjek"] != current_check else ""
        current_check = r["Tjek"]

        p_w_str = latex_p(r["p_wilcoxon"])
        p_t_str = latex_p(r["p_t_test"])
        n_str = f"{r['N_paired']:,}".replace(",", r"\,")
        is_total = r["Horisont"] == "Samlet"
        horisont_str = r["Horisont"]
        p1_str = format_pct(r["P1 %"])
        p2_str = format_pct(r["P2 %"])
        diff_str = format_diff(r["Diff (pp)"])

        if is_total:
            horisont_str = r"\textit{" + horisont_str + "}"
            p1_str = r"\textit{" + p1_str + "}"
            p2_str = r"\textit{" + p2_str + "}"
            diff_str = r"\textit{" + diff_str + "}"

        lines.append(
            f"{check_str} & {horisont_str} & {p1_str} & {p2_str} & "
            f"{diff_str} & {n_str} & {p_w_str} & {p_t_str} \\\\"
        )

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{tablenotes}\small",
        r"\item \textit{Note:} Direkte parret sammenligning mellem P1 baseline og P2 equity. "
        r"Pass rates angivet i procent; differencen er P2 $-$ P1 i procentpoint. "
        r"Wilcoxon Signed-Rank og paired t-test k\o rt p\aa\ signerede residualer "
        r"($\varepsilon$), parret p\aa\ forecast\_id inden for hver horisont. "
        r"Signifikansniveauer: $^{*}$p $<$ 0{,}05, $^{**}$p $<$ 0{,}01.",
        r"\end{tablenotes}",
        r"\end{table}"
    ]
    with open("table_x1b_hypothesis_test.tex", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Gemt: table_x1b_hypothesis_test.tex")


# ============================================================
# TABEL X2 / X3 (ranks)
# ============================================================

def compute_ranks(df, check_key, horizon=None):
    rates = {}
    for name in SOURCE_NAMES:
        rate, _ = pass_rate(df, check_key, SOURCE_CRITERIA[name], horizon)
        rates[name] = rate if pd.notna(rate) else -1

    sorted_items = sorted(rates.items(), key=lambda x: -x[1])
    ranks = {}
    i = 0
    while i < len(sorted_items):
        j = i
        while j + 1 < len(sorted_items) and sorted_items[j+1][1] == sorted_items[i][1]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            ranks[sorted_items[k][0]] = avg_rank
        i = j + 1
    return ranks


def build_table_x2(df):
    rows = []
    for h in HORIZONS:
        rank_sums = {name: [] for name in SOURCE_NAMES}
        for check_key, _, _ in CHECKS:
            ranks = compute_ranks(df, check_key, h)
            for name in SOURCE_NAMES:
                rank_sums[name].append(ranks[name])
        row = {"Horisont": f"t+{h}"}
        for name in SOURCE_NAMES:
            row[name] = np.mean(rank_sums[name])
        rows.append(row)

    rank_sums = {name: [] for name in SOURCE_NAMES}
    for check_key, _, _ in CHECKS:
        ranks = compute_ranks(df, check_key, None)
        for name in SOURCE_NAMES:
            rank_sums[name].append(ranks[name])
    row = {"Horisont": "Samlet"}
    for name in SOURCE_NAMES:
        row[name] = np.mean(rank_sums[name])
    rows.append(row)

    return pd.DataFrame(rows)


def print_table_x2(table):
    print("\n" + "="*70)
    print("TABEL X2 — Gennemsnitlig coherence-rank pr. horisont")
    print("       (1 = bedste pass rate, gennemsnit over de 3 tjek)")
    print("="*70)
    print(f"{'Horisont':<14}{'P1 baseline':>14}{'P2 equity':>14}{'Analytiker':>14}")
    print("-"*70)
    for _, r in table.iterrows():
        is_total = r["Horisont"] == "Samlet"
        if is_total:
            print("-"*70)
        print(f"{r['Horisont']:<14}"
              f"{format_rank(r['P1 baseline']):>14}"
              f"{format_rank(r['P2 equity']):>14}"
              f"{format_rank(r['Analytiker']):>14}")


def save_table_x2_latex(table):
    lines = [
        r"\begin{table}[ht]", r"\centering",
        r"\caption{Gennemsnitlig coherence-rank pr. horisont}",
        r"\label{tab:coherence_rank_horizon}",
        r"\begin{tabular}{lrrr}", r"\toprule",
        r"\textbf{Horisont} & \textbf{P1 baseline} & \textbf{P2 equity} & \textbf{Analytiker} \\",
        r"\midrule",
    ]
    for _, r in table.iterrows():
        if r["Horisont"] == "Samlet":
            lines.append(r"\midrule")
        lines.append(
            f"{r['Horisont']} & {format_rank(r['P1 baseline'])} & "
            f"{format_rank(r['P2 equity'])} & {format_rank(r['Analytiker'])} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    with open("table_x2_rank_horizon.tex", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Gemt: table_x2_rank_horizon.tex")


def build_table_x3(df):
    rows = []
    for check_key, _, check_full in CHECKS:
        rank_sums = {name: [] for name in SOURCE_NAMES}
        for h in HORIZONS:
            ranks = compute_ranks(df, check_key, h)
            for name in SOURCE_NAMES:
                rank_sums[name].append(ranks[name])
        row = {"Tjek": check_full}
        for name in SOURCE_NAMES:
            row[name] = np.mean(rank_sums[name])
        rows.append(row)

    total_row = {"Tjek": "Samlet"}
    for name in SOURCE_NAMES:
        total_row[name] = np.mean([r[name] for r in rows])
    rows.append(total_row)
    return pd.DataFrame(rows)


def print_table_x3(table):
    print("\n" + "="*70)
    print("TABEL X3 — Gennemsnitlig coherence-rank pr. tjek")
    print("       (1 = bedste pass rate, gennemsnit over horisonter)")
    print("="*70)
    print(f"{'Tjek':<30}{'P1 baseline':>14}{'P2 equity':>14}{'Analytiker':>14}")
    print("-"*70)
    for _, r in table.iterrows():
        is_total = r["Tjek"] == "Samlet"
        if is_total:
            print("-"*70)
        print(f"{r['Tjek']:<30}"
              f"{format_rank(r['P1 baseline']):>14}"
              f"{format_rank(r['P2 equity']):>14}"
              f"{format_rank(r['Analytiker']):>14}")


def save_table_x3_latex(table):
    lines = [
        r"\begin{table}[ht]", r"\centering",
        r"\caption{Gennemsnitlig coherence-rank pr. tjek}",
        r"\label{tab:coherence_rank_check}",
        r"\begin{tabular}{lrrr}", r"\toprule",
        r"\textbf{Tjek} & \textbf{P1 baseline} & \textbf{P2 equity} & \textbf{Analytiker} \\",
        r"\midrule",
    ]
    for _, r in table.iterrows():
        if r["Tjek"] == "Samlet":
            lines.append(r"\midrule")
        lines.append(
            f"{r['Tjek']} & {format_rank(r['P1 baseline'])} & "
            f"{format_rank(r['P2 equity'])} & {format_rank(r['Analytiker'])} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    with open("table_x3_rank_check.tex", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Gemt: table_x3_rank_check.tex")


# ============================================================
# FIGURER
# ============================================================

def make_figures(df):
    colors = {"P1 baseline": "#2E4057",
              "P2 equity":   "#D9544D",
              "Analytiker":  "#8B9DB5"}

    for check_key, _, check_full in CHECKS:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        x = np.arange(len(HORIZONS))
        bar_width = 0.25

        for i, name in enumerate(SOURCE_NAMES):
            rates = []
            for h in HORIZONS:
                rate, _ = pass_rate(df, check_key, SOURCE_CRITERIA[name], h)
                rates.append(rate)
            ax.bar(x + (i - 1) * bar_width, rates, bar_width,
                   label=name, color=colors[name], edgecolor='white', linewidth=0.8)
            for j, v in enumerate(rates):
                if pd.notna(v):
                    ax.text(x[j] + (i - 1) * bar_width, v + 0.5,
                            f"{v:.1f}".replace(".", ","),
                            ha='center', va='bottom', fontsize=8)

        ax.set_xticks(x)
        ax.set_xticklabels([f"t+{h}" for h in HORIZONS])
        ax.set_ylabel("Pass rate (%)")
        ax.set_title(f"{check_full}: Pass rates pr. horisont og kilde", fontsize=11)
        ax.set_ylim(0, 105)
        ax.legend(loc='lower right', frameon=False)
        ax.grid(axis='y', alpha=0.3)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

        fname = f"figure_{check_key.lower()}_pass_rates.png"
        plt.tight_layout()
        plt.savefig(fname, dpi=200, bbox_inches='tight')
        plt.close()
        print(f"Gemt: {fname}")


# ============================================================
# MAIN
# ============================================================

def main():
    df = pd.read_csv(INPUT_FILE)
    print(f"Indlaest {len(df):,} obs. fra {INPUT_FILE}\n")

    t1 = build_table_x1(df)
    print_table_x1(t1)
    t1.to_csv("table_x1_pass_rates_main.csv", index=False,
              float_format="%.2f", decimal=",", sep=";")
    print("\nGemt: table_x1_pass_rates_main.csv")
    save_table_x1_latex(t1)

    t1b = build_table_x1b(df)
    print_table_x1b(t1b)
    t1b.to_csv("table_x1b_hypothesis_test.csv", index=False,
               float_format="%.4f", decimal=",", sep=";")
    print("Gemt: table_x1b_hypothesis_test.csv")
    save_table_x1b_latex(t1b)

    t2 = build_table_x2(df)
    print_table_x2(t2)
    t2.to_csv("table_x2_rank_horizon.csv", index=False,
              float_format="%.3f", decimal=",", sep=";")
    print("Gemt: table_x2_rank_horizon.csv")
    save_table_x2_latex(t2)

    t3 = build_table_x3(df)
    print_table_x3(t3)
    t3.to_csv("table_x3_rank_check.csv", index=False,
              float_format="%.3f", decimal=",", sep=";")
    print("Gemt: table_x3_rank_check.csv")
    save_table_x3_latex(t3)

    print("\n" + "="*70)
    print("GENERERER FIGURER")
    print("="*70)
    make_figures(df)

    print("\n" + "="*70)
    print("Faerdig.")
    print("="*70)


if __name__ == "__main__":
    main()
