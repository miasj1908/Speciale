# batch_forecast_runner.py
# LLM batch forecast pipeline for thesis evaluation
# Generates 3-year financial forecasts from historical data

import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
from openai import OpenAI
from collections import defaultdict
from decimal import Decimal
import logging
import argparse
import math

from beregn_value_drivers import bygg_driver_block

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Parse CLI arguments
parser = argparse.ArgumentParser()
parser.add_argument("--prompt", required=True, help="Prompt template name (e.g., baseline)")
parser.add_argument("--model", required=True, help="Model name (e.g., gpt-4)")
parser.add_argument("--effort", default="high", help="Reasoning effort level (e.g., high, medium, low)")
parser.add_argument("--limit", type=int, default=250, help="Max antal forecasts pr. kørsel (brug fx 50 til pilot)")
args = parser.parse_args()

PROMPT_TEMPLATE_NAME = args.prompt
MODEL_NAME = args.model
REASONING_EFFORT = args.effort
LIMIT = args.limit

# OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# DB connection - forecast_studie database
DB_PARAMS = {
    "dbname": "forecast_studie",
    "user": "postgres",
    "password": "Miamia97!",
    "host": "localhost",
    "port": 5432,
}

# Paths
BATCH_INPUT_DIR = "batch_inputs"
os.makedirs(BATCH_INPUT_DIR, exist_ok=True)
INPUT_FILE = os.path.join(BATCH_INPUT_DIR, f"batch_input_{PROMPT_TEMPLATE_NAME}_{MODEL_NAME}.jsonl")

def get_unprocessed_forecasts(prompt_template, model_name, reasoning_effort, limit=100):
    """Fetch unprocessed forecasts from database"""
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT f.forecast_id, f.selskab_id, s.navn, s.sektor, f.forecast_dato
            FROM forecasts f
            JOIN selskaber s ON f.selskab_id = s.selskab_id
            WHERE (f.forecast_id, %s, %s, %s) NOT IN (
                SELECT forecast_id, prompt_template, model_name, reasoning_effort
                FROM llm_batch_audit
            )
            ORDER BY f.forecast_id
            LIMIT %s
        """, (prompt_template, model_name, reasoning_effort, limit))
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def extract_historical_data(forecast_id, conn):
    """Extract 5 years of historical financial data for a forecast"""
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
    """Load prompt template from file"""
    template_path = os.path.join("prompt_templates", f"{template_name}.txt")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

def generate_prompt(historical_data, base_prompt, company_name):
    """
    Generate LLM prompt with historical data formatted as CSV.
    Creates 3-year forecast horizons (t+1, t+2, t+3).
    """
    # Group data by section and year
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

    # Drop years where Revenue is missing or zero — Morningstar leaves the
    # first column empty for some companies, which otherwise pollutes the CSV
    # with all-zero rows that the model reads as real data.
    revenue_by_year = grouped.get('income', {}).get('Revenue', {})
    all_years = sorted(
        y for y in all_years
        if revenue_by_year.get(y) not in (None, 0)
    )
    if not all_years:
        return None

    # Calculate time lags relative to most recent year (t0)
    t0 = max(all_years)
    csv_sections = []

    MIN_YEARS_WITH_DATA = 3

    for section in sorted(grouped.keys()):
        section_header = f"{section.upper()}"
        # Only include items with at least MIN_YEARS_WITH_DATA non-null values
        # to reduce noise from sparse line items. Forecast observations are not
        # lost — this only affects WHICH items the model sees within each forecast.
        items = sorted(
            item for item in grouped[section].keys()
            if sum(1 for v in grouped[section][item].values() if v is not None) >= MIN_YEARS_WITH_DATA
        )

        if not items:
            continue

        # Create CSV with time lags
        csv_lines = [section_header]
        csv_lines.append("item," + ",".join([f"t{y-t0}" for y in all_years]))

        for item in items:
            values = grouped[section][item]
            row = [item] + [str(round(values.get(year, 0), 1)) if values.get(year) is not None else "" for year in all_years]
            csv_lines.append(",".join(row))

        csv_sections.append("\n".join(csv_lines))

    csv_block = "\n\n".join(csv_sections)

    # Pre-computed historical value drivers (margins, turnover, ROIC, NOPAT…)
    # The LLM sees these in addition to the raw statements so Step 1 of the
    # value-driver approach is grounded in deterministic calculations.
    driver_block = bygg_driver_block(historical_data)

    # Output instructions: all items across all three statements, 3-year horizon
    clarification = (
        f"Company: {company_name}\n\n"
        "Forecast three years (t+1, t+2, t+3) for ALL line items shown in every "
        "section (income, balance, cashflow). Do not skip any item. Use the exact "
        "line-item names and section keys ('income', 'balance', 'cashflow') as "
        "they appear in the input below. The VALUE_DRIVERS table below contains "
        "pre-computed historical ratios (margins, turnover, NOPAT, ROIC, etc.) "
        "derived from the same statements — use them as the basis for Step 1 of "
        "the value-driver approach instead of re-deriving them.\n\n"
        "Return strictly valid JSON in this structure:\n"
        "{\"forecasts\": {\"<section>\": {\"<line_item>\": "
        "{\"t+1\": <number>, \"t+2\": <number>, \"t+3\": <number>}}}}\n\n"
        "All forecasted values must be numeric (no strings, no expressions, no null). "
        "Do not include any explanation or extra text outside the JSON."
    )

    blocks = [base_prompt, clarification, csv_block]
    if driver_block:
        blocks.append(driver_block)
    return "\n\n".join(blocks)

def write_jsonl_file(forecasts, base_prompt):
    """Write batch JSONL file for OpenAI API"""
    conn = psycopg2.connect(**DB_PARAMS)
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        for forecast in forecasts:
            historical_data = extract_historical_data(forecast['forecast_id'], conn)

            if not historical_data:
                logging.warning(f"No historical data for forecast {forecast['forecast_id']}")
                continue

            prompt_text = generate_prompt(
                historical_data,
                base_prompt,
                forecast['navn']
            )

            if not prompt_text:
                logging.warning(f"Could not generate prompt for forecast {forecast['forecast_id']}")
                continue

            json.dump({
                "custom_id": f"forecast_{forecast['forecast_id']}__{PROMPT_TEMPLATE_NAME}__{MODEL_NAME}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": "You are a professional equity research analyst. Generate financial forecasts based on historical data provided."},
                        {"role": "user", "content": prompt_text}
                    ],
                    "response_format": {"type": "json_object"},
                    "seed": 42,
                    "reasoning_effort": REASONING_EFFORT,
                    "max_completion_tokens": 60000
                }
            }, f)
            f.write("\n")
    conn.close()
    logging.info(f"Wrote batch input to {INPUT_FILE} with {len(forecasts)} forecasts")

def upload_and_submit_batch():
    """Upload batch file to OpenAI and submit batch job"""
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
    """Track which forecasts were submitted in which batch"""
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
    logging.info(f"Starting batch run: prompt={PROMPT_TEMPLATE_NAME}, model={MODEL_NAME}, effort={REASONING_EFFORT}")

    forecasts = get_unprocessed_forecasts(PROMPT_TEMPLATE_NAME, MODEL_NAME, REASONING_EFFORT, limit=LIMIT)
    if not forecasts:
        logging.info("No unprocessed forecasts found.")
        return

    logging.info(f"Found {len(forecasts)} unprocessed forecasts")

    base_prompt = load_prompt_template(PROMPT_TEMPLATE_NAME)
    write_jsonl_file(forecasts, base_prompt)
    batch_id = upload_and_submit_batch()
    update_tracking_table(forecasts, PROMPT_TEMPLATE_NAME, batch_id, MODEL_NAME, REASONING_EFFORT)

    logging.info("Batch submission complete")

if __name__ == '__main__':
    main()
