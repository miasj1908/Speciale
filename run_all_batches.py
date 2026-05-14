# run_all_batches.py
# Kører alle batches automatisk med pause imellem

import os
import time
import subprocess
import logging
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MODEL = "gpt-5.1"
PROMPT = "baseline"
EFFORT = "medium"
BATCH_SIZE = 250
WAIT_MINUTES = 20  # Vent mellem batches

def count_unprocessed():
    """Check how many forecasts still need processing"""
    import psycopg2
    conn = psycopg2.connect(
        dbname="forecast_studie", user="postgres",
        password="Miamia97!", host="localhost", port=5432
    )
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM forecasts f
            WHERE (f.forecast_id, %s, %s, %s) NOT IN (
                SELECT forecast_id, prompt_template, model_name, reasoning_effort
                FROM llm_batch_audit
            )
        """, (PROMPT, MODEL, EFFORT))
        count = cur.fetchone()[0]
    conn.close()
    return count

def run_batch():
    """Submit one batch"""
    result = subprocess.run(
        ["python", "batch_forecast_runner.py",
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
    remaining = count_unprocessed()
    logging.info(f"Starting. Forecasts remaining: {remaining}")

    batch_num = 1
    while remaining > 0:
        logging.info(f"\n=== BATCH {batch_num} ===")
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
        logging.info(f"Forecasts remaining after batch {batch_num}: {remaining}")
        batch_num += 1

    logging.info("\n✅ ALL DONE! All forecasts processed!")

if __name__ == "__main__":
    main()
