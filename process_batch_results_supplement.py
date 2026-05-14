# process_batch_results_supplement.py
#
# Henter supplementær-batch output fra OpenAI og skriver til regnskabsdata.
# Forventer kalenderår-keys i JSON output (fx "2016": 295000.0).
# ON CONFLICT DO NOTHING sikrer at eksisterende LLM-rækker IKKE overskrives.

import os
import json
import logging
import math

import psycopg2
import psycopg2.extras
from openai import OpenAI

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
SUPPLEMENT_AUDIT = os.path.join(OUTPUT_DIR, "supplement_batches.jsonl")


def safe_float(v):
    try:
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        if isinstance(v, str) and v.strip().lower() in ("nan", "n/a", ""):
            return None
        return round(float(v), 2)
    except Exception:
        return None


def load_audit():
    if not os.path.exists(SUPPLEMENT_AUDIT):
        return []
    with open(SUPPLEMENT_AUDIT, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def rewrite_audit(entries):
    with open(SUPPLEMENT_AUDIT, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def parse_and_insert(out_path, entry):
    prompt_t = entry["prompt_template"]
    model = entry["model"]
    effort = entry["effort"]
    batch_id = entry["batch_id"]
    allowed_years = entry.get("missing_years_per_forecast", {})

    conn = psycopg2.connect(**DB_PARAMS)
    n_inserted_total = 0
    n_bad = 0
    with conn.cursor() as cur:
        with open(out_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                custom_id = rec.get("custom_id", "")
                try:
                    fid = int(custom_id.split("__")[0].replace("supplement_", ""))
                except Exception:
                    continue

                body = rec.get("response", {}).get("body", {})
                choices = body.get("choices") or [{}]
                content = choices[0].get("message", {}).get("content", "") or ""
                content = content.strip()
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

                try:
                    parsed = json.loads(content)
                except Exception as e:
                    logging.error(f"JSON parse fail for forecast {fid}: {e}")
                    n_bad += 1
                    continue

                forecasts = parsed.get("forecasts", {})
                # Valider at kun forventede år er returneret
                expected = set(int(y) for y in allowed_years.get(str(fid), []))

                rows = []
                unexpected_years = set()
                for sek, items in forecasts.items():
                    if not isinstance(items, dict):
                        continue
                    for item, year_map in items.items():
                        if not isinstance(year_map, dict):
                            continue
                        for yk, val in year_map.items():
                            try:
                                yr = int(str(yk).strip())
                            except ValueError:
                                continue
                            if expected and yr not in expected:
                                unexpected_years.add(yr)
                                continue
                            fv = safe_float(val)
                            if fv is None:
                                continue
                            rows.append(
                                (
                                    fid, sek, item, yr, fv,
                                    "llm", prompt_t, model, effort, batch_id,
                                )
                            )

                if unexpected_years:
                    logging.warning(
                        f"forecast {fid}: LLM returnerede uventede år {sorted(unexpected_years)}, ignoreret"
                    )
                if rows:
                    psycopg2.extras.execute_batch(
                        cur,
                        """
                        INSERT INTO regnskabsdata
                            (forecast_id, sektion, line_item, årstal, værdi, datakilde,
                             prompt_template, model_name, reasoning_effort, batch_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING
                        """,
                        rows,
                    )
                    n_inserted_total += len(rows)
                    logging.info(f"forecast {fid}: inserted {len(rows)} rows")
        conn.commit()
    conn.close()
    if n_bad:
        logging.warning(f"{n_bad} forecasts havde parse-fejl")
    return n_inserted_total


def process_pending():
    entries = load_audit()
    if not entries:
        logging.info(f"Ingen audit-entries i {SUPPLEMENT_AUDIT}")
        return
    changed = False
    for entry in entries:
        if entry.get("status") == "completed":
            continue
        batch_id = entry["batch_id"]
        logging.info(f"--- Checker batch {batch_id} ---")
        try:
            b = client.batches.retrieve(batch_id)
        except Exception as e:
            logging.error(f"  retrieve fail: {e}")
            continue
        logging.info(f"  status={b.status}, completed={b.request_counts.completed}/{b.request_counts.total}")
        if b.status != "completed":
            continue
        if not b.output_file_id:
            logging.warning("  ingen output_file_id")
            continue

        out_path = os.path.join(OUTPUT_DIR, f"supplement_output_{batch_id}.jsonl")
        with open(out_path, "wb") as f:
            f.write(client.files.content(b.output_file_id).read())
        logging.info(f"  downloaded til {out_path}")

        n = parse_and_insert(out_path, entry)
        logging.info(f"  {n} rows inserted i regnskabsdata")
        entry["status"] = "completed"
        changed = True
    if changed:
        rewrite_audit(entries)


if __name__ == "__main__":
    process_pending()
