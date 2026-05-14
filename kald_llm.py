"""
Kalder OpenAI API med historiske regnskabsdata og gemmer LLM forecasts.

For hvert _input.json i json_filer_CF/input/ sendes de historiske tal
til GPT-4o med en struktureret prompt, og svaret gemmes i json_filer_CF/llm_output/.

Krav:
    pip install openai
"""

import os
import json
import time
from openai import OpenAI

# --- Konfiguration ---
API_NØGLE        = os.environ.get("OPENAI_API_KEY")
MODEL            = "gpt-4.1"
INPUT_MAPPE      = r"C:\Users\miasj\PythonProjects\json_filer_CF\input"
OUTPUT_MAPPE     = r"C:\Users\miasj\PythonProjects\json_filer_CF\llm_output"
FEJL_MAPPE       = r"C:\Users\miasj\PythonProjects\json_filer_CF\llm_fejl"
SEKUNDER_MELLEM_KALD = 1  # Undgå rate limiting
MAX_FILER        = 5       # Sæt til None for at køre alle 1422

SYSTEM_PROMPT = """Assume the role of an equity analyst. Based on the five years of \
historical financial statement data provided, generate forecasts \
for the full set of financial statements for the subsequent three years, \
with the primary objective of estimating Diluted Earnings Per Share as \
accurately as possible, here defined as "Diluted Earnings Per Share (Adjusted)".

The input data relates to a real, non-anonymized company. You may not use \
the company name, for contextual information in your reasoning.

To forecast Diluted Earnings Per Share (Adjusted), follow a structured \
step-by-step approach in accordance with the instructions below.

Step 1
Using the historical input years, perform the necessary calculations \
and analytical reasoning to derive key metrics from the analytical \
financial statements, including Net Operating Profit After Tax (NOPAT), \
Net Operating Assets (NOA), also referred to as "Invested Capital", \
and Net Interest-Bearing Liabilities (NIBL), also referred to as "Net \
Financial Liabilities".

Step 2
Forecast each line item and each key analytical metric for the next \
three years, including NOPAT, NOA, and NIBL.

The forecasts should be based on a value-driver approach, also referred to as a sales-driven \
approach, in which accounting items such as operating expenses and \
investments are modelled as dependent, to some extent, on the expected \
level of business activity, typically proxied by revenue.

While applying this framework, also rely on your broader knowledge of finance \
and accounting.

Output instructions
Return forecasts for Diluted Earnings Per Share and for all line items, \
without omission, across the three financial statements included in the \
input, in addition to the key metrics from the analytical financial \
statements.

The output must be returned strictly in valid JSON format \
using the following structure:

{"forecasts": {"<statement_type>": {"<line_item>": {"t+1": value, "t+2": value, "t+3": value}}}}

NOPAT should be reported under "Income Statement", while NOA and NIBL \
should be reported under "Balance Sheet". All forecasted values must \
be numeric. Perform all calculations internally before producing the \
final output. Do not include any explanations or any additional text.

In the input tables, "t0" denotes the most recent historical year. \
Produce forecasts only for the three future periods, t+1 through t+3, \
and do not repeat t0."""


def formater_historisk_data(historisk: dict, forecast_dato: str) -> str:
    """Konverterer historiske data til teksttabeller med t-labels."""
    forecast_år = int(forecast_dato[:4])
    # t0 = forecast_år - 1, t-1 = forecast_år - 2, osv.
    år_til_label = {}
    for i, offset in enumerate(range(-4, 1)):  # t-4 til t0
        år = forecast_år - 1 + offset  # t0 = forecast_år - 1
        år_til_label[str(år)] = f"t{offset}" if offset < 0 else "t0"

    linjer = []
    for sektion, line_items in historisk.items():
        linjer.append(f"\n[{sektion.upper()}]")

        # Kolonneoverskrifter
        labels = sorted(år_til_label.values(), key=lambda x: int(x[1:]) if x != "t0" else 0)
        linjer.append("Line Item".ljust(40) + "  ".join(l.rjust(10) for l in labels))
        linjer.append("-" * (40 + 12 * len(labels)))

        for line_item, værdier in sorted(line_items.items()):
            række = line_item.ljust(40)
            for år, label in sorted(år_til_label.items(), key=lambda x: int(x[1][1:]) if x[1] != "t0" else 0):
                v = værdier.get(år)
                if v is None:
                    række += "       N/A"
                else:
                    række += f"{v:>10.2f}"
            linjer.append(række)

    return "\n".join(linjer)


def kald_openai(client: OpenAI, bruger_besked: str) -> str:
    """Sender prompt til OpenAI og returnerer rå tekstsvar."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": bruger_besked},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        timeout=120,
    )
    return response.choices[0].message.content


def behandl_fil(client: OpenAI, input_sti: str, output_mappe: str, fejl_mappe: str):
    """Behandler én input-fil og gemmer LLM-output."""
    fil_navn = os.path.basename(input_sti)
    output_navn = fil_navn.replace("_input.json", "_llm.json")
    output_sti  = os.path.join(output_mappe, output_navn)

    # Spring over hvis allerede behandlet
    if os.path.exists(output_sti):
        return "springer_over"

    with open(input_sti, "r", encoding="utf-8") as f:
        data = json.load(f)

    historisk    = data["historisk"]
    forecast_dato = data["forecast_dato"]
    ticker       = data["ticker"]
    selskab      = data["selskab"]

    tabel = formater_historisk_data(historisk, forecast_dato)
    bruger_besked = (
        f"Company: {selskab} ({ticker})\n"
        f"Forecast date: {forecast_dato}\n\n"
        f"Historical financial data:\n{tabel}"
    )

    try:
        svar = kald_openai(client, bruger_besked)
        llm_json = json.loads(svar)

        # Gem output med metadata
        output_data = {
            "forecast_id":   data["forecast_id"],
            "ticker":        ticker,
            "selskab":       selskab,
            "forecast_dato": forecast_dato,
            "llm_model":     MODEL,
            "forecasts":     llm_json.get("forecasts", llm_json),
        }

        with open(output_sti, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        return "succes"

    except Exception as e:
        # Gem fejlinformation
        os.makedirs(fejl_mappe, exist_ok=True)
        fejl_sti = os.path.join(fejl_mappe, fil_navn.replace("_input.json", "_fejl.txt"))
        with open(fejl_sti, "w", encoding="utf-8") as f:
            f.write(f"Fejl: {e}\n")
        return f"fejl: {e}"


def kør():
    os.makedirs(OUTPUT_MAPPE, exist_ok=True)

    client = OpenAI(api_key=API_NØGLE)

    filer = sorted([
        f for f in os.listdir(INPUT_MAPPE)
        if f.endswith("_input.json")
    ])
    if MAX_FILER is not None:
        filer = filer[:MAX_FILER]

    print(f"Antal filer: {len(filer)}")
    print(f"Model: {MODEL}\n")

    succes = fejl = sprunget_over = 0

    for idx, fil_navn in enumerate(filer):
        input_sti = os.path.join(INPUT_MAPPE, fil_navn)

        if idx % 50 == 0:
            print(f"[{idx}/{len(filer)}] {fil_navn}")

        resultat = behandl_fil(client, input_sti, OUTPUT_MAPPE, FEJL_MAPPE)

        if resultat == "succes":
            succes += 1
        elif resultat == "springer_over":
            sprunget_over += 1
        else:
            fejl += 1
            print(f"  FEJL: {fil_navn} — {resultat}")

        if resultat == "succes":
            time.sleep(SEKUNDER_MELLEM_KALD)

    print("\n" + "=" * 50)
    print("FAERDIG")
    print("=" * 50)
    print(f"Succes:         {succes}")
    print(f"Sprunget over:  {sprunget_over}  (allerede behandlet)")
    print(f"Fejl:           {fejl}")
    print(f"Gemt i:         {OUTPUT_MAPPE}")


if __name__ == "__main__":
    kør()
