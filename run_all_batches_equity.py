# run_all_batches_equity.py
# Kører alle equity treatment batches automatisk med pause imellem

import os
import json
import time
import subprocess
import logging
import psycopg2
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MODEL = "gpt-5.1"
PROMPT = "equity"
EFFORT = "medium"
BATCH_SIZE = 250
WAIT_MINUTES = 20

EQUITY_MAPPING_PATH = "equity_report_mapping.json"

def load_valid_forecast_ids():
    """Load forecast IDs that have equity reports"""
    with open(EQUITY_MAPPING_PATH, encoding="utf-8") as f:
        mapping_list = json.load(f)
    return [int(entry["forecast_id"]) for entry in mapping_list if entry.get("forecast_id") is not None]

def count_unprocessed():
    """Check how many equity forecasts still need processing"""
    valid_ids = load_valid_forecast_ids()
    conn = psycopg2.connect(
        dbname="forecast_studie", user="postgres",
        password="Miamia97!", host="localhost", port=5432
    )
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM forecasts f
            WHERE f.forecast_id = ANY(%s)
            AND (f.forecast_id, %s, %s, %s) NOT IN (
                SELECT forecast_id, prompt_template, model_name, reasoning_effort
                FROM llm_batch_audit
            )
        """, (valid_ids, PROMPT, MODEL, EFFORT))
        count = cur.fetchone()[0]
    conn.close()
    return count

def run_batch():
    """Submit one equity batch"""
    result = subprocess.run(
        ["python", "batch_forecast_runner_equity.py",
         "--prompt", PROMPT, "--model", MODEL, "--effort", EFFORT],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print("ERROR:", result.stderr)
        return False
    return True

def process_results():
    """Process completed batches"""
    result = subprocess.run(
        ["python", "process_batch_results.py"],
        capture_output=True, text=True
    )
    print(result.stdout)

def main():
    valid_ids = load_valid_forecast_ids()
    logging.info(f"Total forecasts with equity reports: {len(valid_ids)}")

    remaining = count_unprocessed()
    logging.info(f"Starting. Equity forecasts remaining: {remaining}")

    batch_num = 1
    while remaining > 0:
        logging.info(f"\n=== EQUITY BATCH {batch_num} ===")
        logging.info(f"Remaining forecasts: {remaining}")

        # Submit batch
        success = run_batch()
        if not success:
            logging.error("Batch submission failed!")
            break

        # Wait for batch to complete
        logging.info(f"Waiting {WAIT_MINUTES} minutes for batch to complete...")
        for i in range(WAIT_MINUTES):
            time.sleep(60)
            logging.info(f"  {i+1}/{WAIT_MINUTES} minutes elapsed...")

        # Process results
        logging.info("Processing results...")
        process_results()

        # Check remaining
        remaining = count_unprocessed()
        logging.info(f"Equity forecasts remaining after batch {batch_num}: {remaining}")
        batch_num += 1

    logging.info("\nALL DONE! All equity forecasts processed!")

if __name__ == "__main__":
    main()
