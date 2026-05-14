# batch_forecast_runner_equity.py
# Treatment condition: LLM forecast pipeline with equity report text added to prompt
# Mirrors batch_forecast_runner.py, with qualitative equity research appended.

import os
import json
import psycopg2
import psycopg2.extras
from openai import OpenAI
from collections import defaultdict
from decimal import Decimal
import logging
import argparse
import math
import re

from beregn_value_drivers import bygg_driver_block

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

parser = argparse.ArgumentParser()
parser.add_argument("--prompt", required=True, help="Prompt template name (e.g., equity)")
parser.add_argument("--model", required=True, help="Model name (e.g., gpt-5.1)")
parser.add_argument("--effort", default="medium", help="Reasoning effort level")
parser.add_argument("--limit", type=int, default=250, help="Max antal forecasts pr. koersel")
args = parser.parse_args()

PROMPT_TEMPLATE_NAME = args.prompt
MODEL_NAME = args.model
REASONING_EFFORT = args.effort
LIMIT = args.limit

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DB_PARAMS = {
    "dbname": "forecast_studie",
    "user": "postgres",
    "password": "Miamia97!",
    "host": "localhost",
    "port": 5432,
}

BATCH_INPUT_DIR = "batch_inputs"
os.makedirs(BATCH_INPUT_DIR, exist_ok=True)
INPUT_FILE = os.path.join(BATCH_INPUT_DIR, f"batch_input_{PROMPT_TEMPLATE_NAME}_{MODEL_NAME}.jsonl")

EQUITY_MAPPING_PATH = "equity_report_mapping.json"
PER_REPORT_DIR = "per_report_json"


def load_equity_mapping():
    with open(EQUITY_MAPPING_PATH, encoding="utf-8") as f:
        mapping_list = json.load(f)
    mapping = {}
    for entry in mapping_list:
        fid = entry.get("forecast_id")
        if fid is not None:
            mapping[int(fid)] = entry["per_report_file"]
    return mapping


def extract_qualitative_text(text, max_chars=8000):
    """Strip financial projection tables, TOC, boilerplate from Morningstar reports."""
    if not text:
        return None

    start_pos = 0
    body_patterns = [
        r'Investment Thesis\n',
        r"Business Strategy[^\n]*(?:Analyst|Senior|Director|Vice)",
        r"Analyst's Perspective\s+\d{2}\s",
        r'Analyst Note \(\d{2}',
    ]
    for pat in body_patterns:
        m = re.search(pat, text)
        if m:
            start_pos = m.start()
            break
    if start_pos == 0:
        for marker in ['Investment Thesis', 'Business Strategy', 'Business Description',
                       "Analyst's Perspective", 'Analyst Note (']:
            idx = text.find(marker)
            if idx >= 0:
                start_pos = idx
                break

    search_from = start_pos + 2000
    cutoff_pos = len(text)
    year_seq = re.search(
        r'\b(19|20)\d{2}\s+(19|20)\d{2}\s+(19|20)\d{2}\s+(19|20)\d{2}',
        text[search_from:]
    )
    if year_seq:
        cutoff_pos = min(cutoff_pos, search_from + year_seq.start())
    for marker in ['Analyst Notes Archive', '\nFinancials\n', 'Fiscal Year ends', 'Fiscal Year,']:
        idx = text.find(marker, search_from)
        if idx > 0:
            cutoff_pos = min(cutoff_pos, idx)

    qualitative = text[start_pos:cutoff_pos].strip()
    if len(qualitative) < 200:
        return None

    qualitative = re.sub(r'Morningstar Equity Analyst Report \| Report as of[^\n]*\n[^\n]*\n', '', qualitative)
    qualitative = re.sub(
        r'[\u00a9\uf0e9]?\s*Morningstar \d{4}\. All Rights Reserved\..*?(?=\n\n|\Z)',
        '', qualitative, flags=re.DOTALL,
    )
    qualitative = re.sub(
        r'The information, data, analyses and[^\n]*\n.*?(?=\n[A-Z]|\Z)',
        '', qualitative, flags=re.DOTALL,
    )
    qualitative = re.sub(r'\nThe conduct of Morningstar.*?(?=\n[A-Z]|\Z)', '', qualitative, flags=re.DOTALL)
    qualitative = re.sub(
        r'\n\d{1,2} \w+ \d{4} \d{2}:\d{2}, UTC\nLast Price Fair Value Estimate.*?\n.*?\n',
        '\n', qualitative, flags=re.DOTALL,
    )
    qualitative = re.sub(r'\nCompetitors\n.*?(?=\n[A-Z][a-z]|\Z)', '', qualitative, flags=re.DOTALL)
    for nav in ['Analyst Notes Archive', 'Research Methodology for Valuing Companies',
                'Important Disclosure', '\nFinancials ']:
        qualitative = qualitative.replace(nav, '')
    qualitative = re.sub(r'\n{3,}', '\n\n', qualitative).strip()

    if len(qualitative) < 200:
        return None
    return qualitative[:max_chars]


def get_equity_text(per_report_file):
    path = os.path.join(PER_REPORT_DIR, per_report_file)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    text = data.get("main_note_text", "")
    return extract_qualitative_text(text, max_chars=8000) if text else None


def get_unprocessed_forecasts(prompt_template, model_name, reasoning_effort, equity_mapping, limit=250):
    valid_ids = list(equity_mapping.keys())
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT f.forecast_id, f.selskab_id, s.navn, s.sektor, f.forecast_dato
            FROM forecasts f
            JOIN selskaber s ON f.selskab_id = s.selskab_id
            WHERE f.forecast_id = ANY(%s)
            AND (f.forecast_id, %s, %s, %s) NOT IN (
                SELECT forecast_id, prompt_template, model_name, reasoning_effort
                FROM llm_batch_audit
            )
            ORDER BY f.forecast_id
            LIMIT %s
        """, (valid_ids, prompt_template, model_name, reasoning_effort, limit))
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def extract_historical_data(forecast_id, conn):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT sektion, line_item, årstal, værdi
            FROM regnskabsdata
            WHERE forecast_id = %s
            AND datakilde = 'historisk'
            AND årstal IS NOT NULL
            AND værdi IS NOT NULL
            ORDER BY sektion, årstal
        """, (forecast_id,))
        return [dict(row) for row in cur.fetchall()]


def load_prompt_template(template_name):
    template_path = os.path.join("prompt_templates", f"{template_name}.txt")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def generate_prompt(historical_data, base_prompt, company_name, equity_text):
    grouped = defaultdict(lambda: defaultdict(dict))
    all_years = set()

    for row in historical_data:
        section = row['sektion']
        item = row['line_item']
        year = int(row['årstal'])
        value = float(row['værdi']) if isinstance(row['værdi'], Decimal) else row['værdi']

        if item and not (isinstance(item, float) and math.isnan(item)):
            grouped[section][item][year] = value
            all_years.add(year)

    revenue_by_year = grouped.get('income', {}).get('Revenue', {})
    all_years = sorted(
        y for y in all_years
        if revenue_by_year.get(y) not in (None, 0)
    )
    if not all_years:
        return None

    t0 = max(all_years)
    csv_sections = []
    MIN_YEARS_WITH_DATA = 3

    for section in sorted(grouped.keys()):
        section_header = f"{section.upper()}"
        items = sorted(
            item for item in grouped[section].keys()
            if sum(1 for v in grouped[section][item].values() if v is not None) >= MIN_YEARS_WITH_DATA
        )
        if not items:
            continue

        csv_lines = [section_header]
        csv_lines.append("item," + ",".join([f"t{y-t0}" for y in all_years]))
        for item in items:
            values = grouped[section][item]
            row = [item] + [str(round(values.get(year, 0), 1)) if values.get(year) is not None else "" for year in all_years]
            csv_lines.append(",".join(row))
        csv_sections.append("\n".join(csv_lines))

    csv_block = "\n\n".join(csv_sections)
    driver_block = bygg_driver_block(historical_data)

    clarification = (
        f"Company: {company_name}\n\n"
        "Forecast three years (t+1, t+2, t+3) for ALL line items shown in every "
        "section (income, balance, cashflow). Do not skip any item. Use the exact "
        "line-item names and section keys ('income', 'balance', 'cashflow') as "
        "they appear in the input below. The VALUE_DRIVERS table below contains "
        "pre-computed historical ratios (margins, turnover, NOPAT, ROIC, etc.) "
        "derived from the same statements — use them as the basis for Step 1 of "
        "the value-driver approach instead of re-deriving them. The EQUITY "
        "RESEARCH REPORT that follows provides qualitative context and forward-"
        "looking commentary — incorporate relevant insights where appropriate, "
        "but ground the forecasts in the historical data.\n\n"
        "Return strictly valid JSON in this structure:\n"
        "{\"forecasts\": {\"<section>\": {\"<line_item>\": "
        "{\"t+1\": <number>, \"t+2\": <number>, \"t+3\": <number>}}}}\n\n"
        "All forecasted values must be numeric (no strings, no expressions, no null). "
        "Do not include any explanation or extra text outside the JSON."
    )

    equity_section = f"EQUITY RESEARCH REPORT (qualitative context):\n{equity_text}"

    blocks = [base_prompt, clarification, csv_block]
    if driver_block:
        blocks.append(driver_block)
    blocks.append(equity_section)
    return "\n\n".join(blocks)


def write_jsonl_file(forecasts, base_prompt, equity_mapping):
    conn = psycopg2.connect(**DB_PARAMS)
    written = 0
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        for forecast in forecasts:
            historical_data = extract_historical_data(forecast['forecast_id'], conn)
            if not historical_data:
                logging.warning(f"No historical data for forecast {forecast['forecast_id']}")
                continue

            equity_text = get_equity_text(equity_mapping[forecast['forecast_id']])
            if not equity_text:
                logging.warning(f"No equity text for forecast {forecast['forecast_id']}")
                continue

            prompt_text = generate_prompt(historical_data, base_prompt, forecast['navn'], equity_text)
            if not prompt_text:
                continue

            json.dump({
                "custom_id": f"forecast_{forecast['forecast_id']}__{PROMPT_TEMPLATE_NAME}__{MODEL_NAME}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": "You are a professional equity research analyst. Generate financial forecasts based on historical data and the equity research report provided."},
                        {"role": "user", "content": prompt_text}
                    ],
                    "response_format": {"type": "json_object"},
                    "seed": 42,
                    "reasoning_effort": REASONING_EFFORT,
                    "max_completion_tokens": 60000
                }
            }, f)
            f.write("\n")
            written += 1

    conn.close()
    logging.info(f"Wrote {written} forecasts to {INPUT_FILE}")


def upload_and_submit_batch():
    with open(INPUT_FILE, "rb") as f:
        file_obj = client.files.create(file=f, purpose="batch")
    logging.info(f"Uploaded file ID: {file_obj.id}")

    batch = client.batches.create(
        input_file_id=file_obj.id,
        endpoint="/v1/chat/completions",
        completion_window="24h"
    )
    logging.info(f"Created batch job ID: {batch.id}")
    return batch.id


def update_tracking_table(forecasts, prompt_template, batch_id, model_name, reasoning_effort):
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO llm_batch_audit (forecast_id, prompt_template, batch_id, model_name, reasoning_effort, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (forecast_id, prompt_template, model_name, reasoning_effort) DO NOTHING
        """, [(f['forecast_id'], prompt_template, batch_id, model_name, reasoning_effort) for f in forecasts])
        conn.commit()
    conn.close()
    logging.info(f"Inserted tracking records for batch {batch_id}")


def main():
    logging.info(f"Starting equity batch run: prompt={PROMPT_TEMPLATE_NAME}, model={MODEL_NAME}, effort={REASONING_EFFORT}")

    equity_mapping = load_equity_mapping()
    logging.info(f"Loaded equity mapping: {len(equity_mapping)} forecasts with equity reports")

    forecasts = get_unprocessed_forecasts(PROMPT_TEMPLATE_NAME, MODEL_NAME, REASONING_EFFORT, equity_mapping, limit=LIMIT)
    if not forecasts:
        logging.info("No unprocessed forecasts found.")
        return

    logging.info(f"Found {len(forecasts)} unprocessed forecasts")

    base_prompt = load_prompt_template(PROMPT_TEMPLATE_NAME)
    write_jsonl_file(forecasts, base_prompt, equity_mapping)
    batch_id = upload_and_submit_batch()
    update_tracking_table(forecasts, PROMPT_TEMPLATE_NAME, batch_id, MODEL_NAME, REASONING_EFFORT)

    logging.info("Batch submission complete")


if __name__ == '__main__':
    main()
