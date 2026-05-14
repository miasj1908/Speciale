# process_batch_results.py
# Processes completed OpenAI batch jobs and stores LLM forecasts in database

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
    "dbname": "forecast_studie",
    "user": "postgres",
    "password": "Miamia97!",
    "host": "localhost",
    "port": 5432,
}

OUTPUT_DIR = "batch_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def safe_float(value):
    """Convert value to float, handling NaN and None"""
    try:
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        if isinstance(value, str) and value.strip().lower() in ["nan", "n/a", ""]:
            return None
        return round(float(value), 2)
    except Exception:
        return None

def fetch_pending_batches():
    """Get all pending batch jobs from audit table"""
    conn = psycopg2.connect(**DB_PARAMS)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT batch_id, prompt_template, model_name, reasoning_effort
            FROM llm_batch_audit
            WHERE status = 'pending'
        """)
        rows = cur.fetchall()
    conn.close()
    return [{"batch_id": r[0], "prompt_template": r[1], "model_name": r[2], "reasoning_effort": r[3]} for r in rows]

def check_batch_status(batch_id):
    """Check if batch is completed and get output file ID"""
    try:
        batch = client.batches.retrieve(batch_id)
        return batch.status, batch.output_file_id
    except Exception as e:
        logging.error(f"Failed to retrieve batch {batch_id}: {e}")
        return None, None

def download_and_store_output(output_file_id, batch_id):
    """Download batch results from OpenAI"""
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

def parse_response_and_insert_to_db(batch_id, output_path, prompt_template, model_name, reasoning_effort):
    """Parse LLM responses and insert into llm_forecasts table"""
    conn = psycopg2.connect(**DB_PARAMS)
    inserted_count = 0
    skipped_count = 0

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        with open(output_path, "r", encoding="utf-8") as infile:
            for line_num, line in enumerate(infile, 1):
                try:
                    record = json.loads(line)
                    custom_id = record.get("custom_id")

                    # Parse custom_id: forecast_123__baseline__gpt-4-turbo
                    parts = custom_id.split("__")
                    if len(parts) < 3 or not parts[0].startswith("forecast_"):
                        logging.error(f"Invalid custom_id format: {custom_id}")
                        skipped_count += 1
                        continue

                    forecast_id = int(parts[0].replace("forecast_", ""))

                    # Extract LLM response
                    response_body = record.get("response", {}).get("body", {})
                    content = response_body.get("choices", [{}])[0].get("message", {}).get("content", "")

                    if not content:
                        logging.warning(f"Empty response for forecast {forecast_id}")
                        skipped_count += 1
                        continue

                    # Parse JSON (handle markdown code blocks)
                    if content.strip().startswith("```json"):
                        content = content.strip()[7:-3].strip()
                    elif content.strip().startswith("```"):
                        content = content.strip()[3:-3].strip()

                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        logging.error(f"Failed to parse JSON for forecast {forecast_id}: {content[:100]}")
                        skipped_count += 1
                        continue

                    # Insert forecasts
                    count = insert_forecast(forecast_id, prompt_template, model_name, reasoning_effort,
                                          parsed, batch_id, cur)
                    inserted_count += count

                except Exception as e:
                    logging.error(f"Error processing line {line_num}: {e}")
                    skipped_count += 1

        conn.commit()
    conn.close()
    logging.info(f"Finished parsing. Inserted: {inserted_count}, Skipped: {skipped_count}")

def insert_forecast(forecast_id, prompt_template, model_name, reasoning_effort, forecast_data, batch_id, cur):
    """Insert individual forecast into database"""
    try:
        # Get forecast metadata
        cur.execute("""
            SELECT selskab_id, forecast_dato, første_hist_år, sidste_prognose_år
            FROM forecasts
            WHERE forecast_id = %s
        """, (forecast_id,))
        row = cur.fetchone()
        if not row:
            logging.warning(f"Forecast ID {forecast_id} not found in database")
            return 0

        selskab_id = row["selskab_id"]
        forecast_dato = row["forecast_dato"]
        t0_year = row["sidste_prognose_år"]  # Most recent year in historical data

        # Get t0 year from actual data
        cur.execute("""
            SELECT MAX(årstal)
            FROM regnskabsdata
            WHERE forecast_id = %s AND datakilde = 'historisk'
        """, (forecast_id,))
        t0_result = cur.fetchone()
        if t0_result and t0_result[0]:
            t0_year = t0_result[0]
        else:
            logging.warning(f"Could not determine t0 year for forecast {forecast_id}")
            return 0

        rows_to_insert = []
        inserted = 0

        # Parse forecasts from LLM response
        forecasts = forecast_data.get("forecasts", {})
        for sektion, line_items in forecasts.items():
            if not isinstance(line_items, dict):
                continue

            for item, horizons in line_items.items():
                if not isinstance(horizons, dict):
                    continue

                for horizon_str, value in horizons.items():
                    try:
                        # Parse horizon (t+1, t+2, t+3)
                        if not horizon_str.startswith("t+"):
                            continue
                        offset = int(horizon_str.replace("t+", "").strip())
                        period_year = t0_year + offset

                        final_value = safe_float(value)
                        if final_value is None:
                            continue

                        rows_to_insert.append({
                            "forecast_id": forecast_id,
                            "sektion": sektion,
                            "line_item": item,
                            "årstal": period_year,
                            "værdi": final_value,
                            "datakilde": "llm",
                            "prompt_template": prompt_template,
                            "model_name": model_name,
                            "reasoning_effort": reasoning_effort,
                            "batch_id": batch_id
                        })
                    except Exception as e:
                        logging.debug(f"Skipping value for {item} horizon {horizon_str}: {e}")

        # Insert into database
        if rows_to_insert:
            insert_query = """
            INSERT INTO regnskabsdata
                (forecast_id, sektion, line_item, årstal, værdi, datakilde, prompt_template, model_name, reasoning_effort, batch_id)
            VALUES
                (%(forecast_id)s, %(sektion)s, %(line_item)s, %(årstal)s, %(værdi)s, %(datakilde)s, %(prompt_template)s, %(model_name)s, %(reasoning_effort)s, %(batch_id)s)
            ON CONFLICT DO NOTHING
            """
            psycopg2.extras.execute_batch(cur, insert_query, rows_to_insert)
            logging.info(f"Inserted {len(rows_to_insert)} forecast values for forecast {forecast_id}")
            inserted = len(rows_to_insert)

        return inserted

    except Exception as e:
        logging.error(f"Error inserting forecast {forecast_id}: {e}")
        return 0

def mark_batch_completed(batch_id):
    """Mark batch as completed in audit table"""
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
    """Main processing loop"""
    pending_batches = fetch_pending_batches()
    if not pending_batches:
        logging.info("No pending batches to process.")
        return

    for batch_info in pending_batches:
        batch_id = batch_info["batch_id"]
        logging.info(f"Processing batch {batch_id}...")

        status, output_file_id = check_batch_status(batch_id)

        if status != "completed":
            logging.info(f"Batch {batch_id} status is '{status}', skipping.")
            continue

        if not output_file_id:
            logging.error(f"No output file ID for batch {batch_id}")
            continue

        output_path = download_and_store_output(output_file_id, batch_id)
        if output_path:
            parse_response_and_insert_to_db(
                batch_id,
                output_path,
                batch_info["prompt_template"],
                batch_info["model_name"],
                batch_info["reasoning_effort"]
            )
            mark_batch_completed(batch_id)

if __name__ == "__main__":
    main()
