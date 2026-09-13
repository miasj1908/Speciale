# pipeline/process_batch_results.py
import os
import json
import logging
import math
import psycopg2
import psycopg2.extras
from openai import OpenAI

# Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DB_PARAMS = {
    "dbname": "earnings_forecast",
    "user": "postgres",
    "host": "localhost",
    "port": "5432",
}

OUTPUT_DIR = "batch_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def safe_float(value):
    try:
        if value is None:
            return 0.0
        if isinstance(value, float) and math.isnan(value):
            return 0.0
        if isinstance(value, str) and value.strip().lower() == "nan":
            return 0.0
        return round(float(value), 3)
    except Exception:
        return 0.0

def fetch_pending_batches():
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT batch_id
            FROM llm_batch_audit
            WHERE status = 'pending'
        """)
        rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]

def check_batch_status(batch_id):
    try:
        batch = client.batches.retrieve(batch_id)
        return batch.status, batch.output_file_id
    except Exception as e:
        logging.error(f"Failed to retrieve batch {batch_id}: {e}")
        return None, None

def download_and_store_output(output_file_id, batch_id):
    try:
        response = client.files.content(output_file_id)
        output_path = os.path.join(OUTPUT_DIR, f"batch_output_{batch_id}.jsonl")
        with open(output_path, "wb") as f:
            f.write(response.read())
        logging.info(f"Saved output for batch {batch_id} to {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Failed to download output file for batch {batch_id}: {e}")
        return None

def parse_response_and_insert_to_db(batch_id, output_path):
    conn = psycopg2.connect(**DB_PARAMS)
    nan_count = 0
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        with open(output_path, "r", encoding="utf-8") as infile:
            for line in infile:
                try:
                    record = json.loads(line)
                    custom_id = record.get("custom_id")
                    parts = custom_id.split("__")
                    if len(parts) != 4 or not parts[0].startswith("obs_"):
                        logging.error(f"Invalid custom_id format: {custom_id}")
                        continue

                    obs_id = int(parts[0].replace("obs_", ""))
                    prompt_template = parts[1]
                    model_name = parts[2]
                    reasoning_effort = parts[3]

                    response = record.get("response", {}).get("body", {})
                    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

                    if content.strip().startswith("```json"):
                        content = content.strip()[7:-3].strip()

                    parsed = json.loads(content)
                    nan_count += insert_forecast(obs_id, prompt_template, model_name, reasoning_effort, parsed, batch_id, cur)
                except Exception as e:
                    logging.error(f"Error processing record: {e}")
        conn.commit()
    conn.close()
    logging.info(f"Finished parsing. Total null/NaN values replaced: {nan_count}")

def insert_forecast(obs_id, prompt_template, model_name, reasoning_effort, forecast_data, batch_id, cur):
    cur.execute("""
        SELECT company_id, file_name, forecast_date
        FROM observations
        WHERE observation_id = %s
    """, (obs_id,))
    row = cur.fetchone()
    if not row:
        logging.warning(f"Observation ID {obs_id} not found.")
        return 0

    company_id = row["company_id"]
    file_name = row["file_name"]
    forecast_date = row["forecast_date"]

    cur.execute("""
        SELECT MAX(year)
        FROM financial_statements_normalized
        WHERE observation_id = %s AND time_lag = 0
    """, (obs_id,))
    t_row = cur.fetchone()
    if not t_row or not t_row[0]:
        logging.warning(f"Could not determine t0 year for observation {obs_id}")
        return 0
    t = t_row[0]

    company_guess = forecast_data.get("company_guess")

    rows_to_insert = []
    nan_count = 0
    for stype, line_items in forecast_data.get("forecasts", {}).items():
        for item, forecasts in line_items.items():
            for horizon, value in forecasts.items():
                try:
                    offset = int(horizon.replace("t+", "").strip())
                    period_year = t + offset
                    final_value = safe_float(value)
                    if final_value == 0.0:
                        nan_count += 1
                    rows_to_insert.append({
                        "observation_id": obs_id,
                        "company_id": company_id,
                        "file_name": file_name,
                        "forecast_date": forecast_date,
                        "period_year": period_year,
                        "period_type": "llm_forecast",
                        "time_lag": offset,
                        "statement_type": stype,
                        "line_item": item,
                        "forecast_value": final_value,
                        "raw_response": None,
                        "prompt_template": prompt_template,
                        "model_name": model_name,
                        "reasoning_effort": reasoning_effort,
                        "company_guess": company_guess,
                        "batch_id": batch_id
                    })
                except Exception as e:
                    logging.warning(f"Skipping bad forecast value for obs {obs_id}, item '{item}', value '{value}': {e}")

    insert_query = """
    INSERT INTO llm_forecasts (
        observation_id, company_id, file_name, forecast_date,
        period_year, period_type, time_lag,
        statement_type, line_item, forecast_value,
        raw_response, prompt_template, model_name, reasoning_effort, company_guess, batch_id
    ) VALUES (
        %(observation_id)s, %(company_id)s, %(file_name)s, %(forecast_date)s,
        %(period_year)s, %(period_type)s, %(time_lag)s,
        %(statement_type)s, %(line_item)s, %(forecast_value)s,
        %(raw_response)s, %(prompt_template)s, %(model_name)s, %(reasoning_effort)s, %(company_guess)s, %(batch_id)s
    )
    ON CONFLICT (
        observation_id,
        period_year,
        statement_type,
        line_item,
        prompt_template
    ) DO NOTHING;
    """

    if rows_to_insert:
        psycopg2.extras.execute_batch(cur, insert_query, rows_to_insert)
        logging.info(f"Inserted {len(rows_to_insert)} forecasts for obs {obs_id}")
    else:
        logging.warning(f"No valid forecasts to insert for obs {obs_id}")
    return nan_count

def mark_batch_completed(batch_id):
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE llm_batch_audit
            SET status = 'completed'
            WHERE batch_id = %s
        """, (batch_id,))
        conn.commit()
    conn.close()
    logging.info(f"Marked batch {batch_id} as completed")

def main():
    pending_batches = fetch_pending_batches()
    if not pending_batches:
        logging.info("No pending batches to process.")
        return

    for batch_id in pending_batches:
        logging.info(f"Processing batch {batch_id}...")
        status, output_file_id = check_batch_status(batch_id)

        if status != "completed":
            logging.info(f"Batch {batch_id} status is '{status}', skipping.")
            continue

        output_path = download_and_store_output(output_file_id, batch_id)
        if output_path:
            parse_response_and_insert_to_db(batch_id, output_path)
            mark_batch_completed(batch_id)

if __name__ == "__main__":
    main()
