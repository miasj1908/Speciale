# pipeline/batch_forecast_runner.py
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

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Parse CLI arguments
parser = argparse.ArgumentParser()
parser.add_argument("--prompt", required=True, help="Prompt template name (e.g., test_equity_analyst)")
parser.add_argument("--model", required=True, help="Model name (e.g., o3-mini)")
parser.add_argument("--effort", default="high", help="Reasoning effort level (e.g., high, medium, low)")
args = parser.parse_args()

PROMPT_TEMPLATE_NAME = args.prompt
MODEL_NAME = args.model
REASONING_EFFORT = args.effort

# OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# DB connection
DB_PARAMS = {
    "dbname": "earnings_forecast",
    "user": "postgres",
    "host": "localhost",
    "port": "5432",
}

# Paths
BATCH_INPUT_DIR = "batch_inputs"
os.makedirs(BATCH_INPUT_DIR, exist_ok=True)
INPUT_FILE = os.path.join(BATCH_INPUT_DIR, f"batch_input_{PROMPT_TEMPLATE_NAME}_{MODEL_NAME}.jsonl")

def get_unprocessed_observations(prompt_template, model_name, reasoning_effort, limit=300):
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT o.observation_id, o.company_id, o.file_name, o.forecast_date
            FROM observations o
            JOIN (
                SELECT observation_id
                FROM earliest_complete_observations eco
                JOIN consolidated_observation_data cod USING (observation_id)
                GROUP BY observation_id, forecast_realized_count
                HAVING MAX(forecast_realized_count) > 0
            ) valid_obs USING (observation_id)
            WHERE (o.observation_id, %s, %s, %s) NOT IN (
                SELECT observation_id, prompt_template, model_name, reasoning_effort
                FROM llm_batch_audit
            )
            ORDER BY o.observation_id
            LIMIT %s
        """, (prompt_template, model_name, reasoning_effort, limit))
        rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def extract_historical_data(observation_id, conn):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT time_lag, statement_type, line_item, normalized_value AS value, line_item_order
            FROM financial_statements_normalized
            WHERE observation_id = %s AND time_lag BETWEEN -4 AND 0
            ORDER BY statement_type, line_item_order
        """, (observation_id,))
        return [
            dict(row) for row in cur.fetchall()
            if row["line_item"] and not (isinstance(row["line_item"], float) and math.isnan(row["line_item"]))
        ]

def load_prompt_template(template_name):
    template_path = os.path.join("prompt_templates", f"{template_name}.txt")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

def generate_prompt(historical_data, base_prompt):
    grouped = defaultdict(lambda: defaultdict(dict))
    ordering = defaultdict(list)
    all_lags = set()

    for row in historical_data:
        statement = row['statement_type']
        item = row['line_item']
        if item is None or (isinstance(item, float) and math.isnan(item)):
            continue
        grouped[statement][item][int(row['time_lag'])] = float(row['value']) if isinstance(row['value'], Decimal) else row['value']
        ordering[statement].append((row['line_item_order'], item))
        all_lags.add(int(row['time_lag']))

    all_lags = sorted(all_lags)
    lag_headers = [f"t{lag}" for lag in all_lags]
    csv_sections = []

    for stype in sorted(grouped):
        section = [stype, f"line_item,{','.join(lag_headers)}"]
        ordered_items = sorted(set(ordering[stype]))
        for _, item in ordered_items:
            vals = grouped[stype].get(item, {})
            row = [item] + [str(vals.get(lag, "")) for lag in all_lags]
            section.append(",".join(row))
        csv_sections.append("\n".join(section))

    csv_block = "\n\n".join(csv_sections)

    clarification = (
        "\n\nNote: In the tables below, 't0' represents the most recent historical year. "
        "You should forecast 5 future years as t+1 to t+5, starting after t0. "
        "Do not repeat the t0 year in your forecasts."
    )

    company_guess_instruction = (
        "\n\nAlso, based on the data, guess which company this might be. "
        "Return your guess inside the JSON using this format:\n"
        "{\n  \"company_guess\": \"<company name>\"\n}\n"
    )

    return f"{base_prompt}{clarification}\n\n{csv_block}{company_guess_instruction}"

def write_jsonl_file(observations, base_prompt):
    conn = psycopg2.connect(**DB_PARAMS)
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        for obs in observations:
            historical_data = extract_historical_data(obs['observation_id'], conn)
            prompt_text = generate_prompt(historical_data, base_prompt)
            json.dump({
                "custom_id": f"obs_{obs['observation_id']}__{PROMPT_TEMPLATE_NAME}__{MODEL_NAME}__{REASONING_EFFORT}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": "You are a professional equity research analyst."},
                        {"role": "user", "content": prompt_text}
                    ],
                    "reasoning_effort": REASONING_EFFORT
                }
            }, f)
            f.write("\n")
    conn.close()
    logging.info(f"Wrote batch input to {INPUT_FILE}")

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

def update_tracking_table(observations, prompt_template, batch_id, model_name, reasoning_effort):
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO llm_batch_audit (observation_id, prompt_template, batch_id, model_name, reasoning_effort)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, [(obs['observation_id'], prompt_template, batch_id, model_name, reasoning_effort) for obs in observations])
        conn.commit()
    conn.close()
    logging.info(f"Inserted tracking records for batch {batch_id}")

def main():
    observations = get_unprocessed_observations(PROMPT_TEMPLATE_NAME, MODEL_NAME, REASONING_EFFORT, limit=300)
    if not observations:
        logging.info("No unprocessed observations found.")
        return

    base_prompt = load_prompt_template(PROMPT_TEMPLATE_NAME)
    write_jsonl_file(observations, base_prompt)
    batch_id = upload_and_submit_batch()
    update_tracking_table(observations, PROMPT_TEMPLATE_NAME, batch_id, MODEL_NAME, REASONING_EFFORT)

if __name__ == '__main__':
    main()

