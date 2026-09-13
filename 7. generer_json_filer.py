"""
Genererer JSON filer fra PostgreSQL databasen.
En JSON fil per observation med historiske tal, analytikerprognoser og realiserede tal.

Krav:
    pip install psycopg2-binary
"""

import psycopg2
import json
import os
from datetime import datetime, date

DB_HOST    = "localhost"
DB_PORT    = 5432
DB_BRUGER  = "postgres"
DB_KODEORD = "Miamia97!"
DB_NAVN    = "forecast_studie"

# Mappe hvor JSON filer gemmes
OUTPUT_MAPPE = r"C:\Users\miasj\PythonProjects\json_filer"


def opret_forbindelse():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_BRUGER, password=DB_KODEORD,
        dbname=DB_NAVN
    )


def hent_alle_forecasts(cur):
    """Henter alle forecasts med selskabsinfo."""
    cur.execute("""
        SELECT
            f.forecast_id,
            f.forecast_dato,
            f.første_prognose_år,
            f.sidste_prognose_år,
            f.første_hist_år,
            f.filnavn,
            s.ticker,
            s.navn,
            s.sektor
        FROM forecasts f
        JOIN selskaber s ON f.selskab_id = s.selskab_id
        ORDER BY s.ticker, f.forecast_dato
    """)
    return cur.fetchall()


def hent_regnskabsdata(cur, forecast_id, datakilde):
    """Henter alle regnskabstal for én forecast og datakilde."""
    cur.execute("""
        SELECT sektion, line_item, årstal, værdi
        FROM regnskabsdata
        WHERE forecast_id = %s AND datakilde = %s
        ORDER BY sektion, line_item, årstal
    """, (forecast_id, datakilde))
    rows = cur.fetchall()

    # Strukturer som nested dict: {sektion: {line_item: {årstal: værdi}}}
    data = {}
    for sektion, line_item, aarstal, vaerdi in rows:
        if sektion not in data:
            data[sektion] = {}
        if line_item not in data[sektion]:
            data[sektion][line_item] = {}
        data[sektion][line_item][str(aarstal)] = float(vaerdi) if vaerdi is not None else None

    return data


def json_serializer(obj):
    """Håndterer datetime objekter i JSON serialisering."""
    if isinstance(obj, (datetime, date)):
        return str(obj)
    raise TypeError(f"Type {type(obj)} ikke serialiserbar")


def generer_json_filer():
    """Hovedfunktion der genererer alle JSON filer."""

    # Opret output mappe
    os.makedirs(OUTPUT_MAPPE, exist_ok=True)

    print("Forbinder til database...")
    conn = opret_forbindelse()
    cur = conn.cursor()

    print("Henter forecasts...")
    forecasts = hent_alle_forecasts(cur)
    print(f"Antal forecasts: {len(forecasts)}")

    succes = 0
    fejl = 0

    for idx, forecast in enumerate(forecasts):
        forecast_id, forecast_dato, foerste_prog, sidst_prog, foerste_hist, filnavn, ticker, navn, sektor = forecast

        if idx % 100 == 0:
            print(f"Behandler {idx}/{len(forecasts)}: {ticker} ({forecast_dato})")

        try:
            # Hent data for alle tre datakilder
            historisk  = hent_regnskabsdata(cur, forecast_id, 'historisk')
            analytiker = hent_regnskabsdata(cur, forecast_id, 'analytiker')
            realiseret = hent_regnskabsdata(cur, forecast_id, 'realiseret')

            # Byg JSON struktur
            json_data = {
                "forecast_id":        forecast_id,
                "ticker":             ticker,
                "selskab":            navn,
                "sektor":             sektor,
                "forecast_dato":      str(forecast_dato),
                "første_prognose_år": foerste_prog,
                "sidste_prognose_år": sidst_prog,
                "første_hist_år":     foerste_hist,
                "cf_model_fil":       filnavn,
                "historisk":          historisk,
                "analytiker":         analytiker,
                "realiseret":         realiseret,
            }

            # Gem JSON fil
            fil_navn = f"{ticker}_{str(forecast_dato).replace('-', '')}.json"
            fil_sti = os.path.join(OUTPUT_MAPPE, fil_navn)

            with open(fil_sti, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2,
                         default=json_serializer)

            succes += 1

        except Exception as e:
            print(f"  FEJL: {ticker} ({forecast_dato}): {e}")
            fejl += 1

    cur.close()
    conn.close()

    print("\n" + "="*50)
    print("FAERDIG")
    print("="*50)
    print(f"JSON filer genereret: {succes}")
    print(f"Fejl:                 {fejl}")
    print(f"Gemt i:               {OUTPUT_MAPPE}")

    # Vis eksempel på første fil
    if succes > 0:
        print("\n=== EKSEMPEL PAA FOERSTE JSON FIL ===")
        første_fil = os.listdir(OUTPUT_MAPPE)[0]
        with open(os.path.join(OUTPUT_MAPPE, første_fil), 'r') as f:
            data = json.load(f)
        print(f"Fil: {første_fil}")
        print(f"Ticker: {data['ticker']}")
        print(f"Selskab: {data['selskab']}")
        print(f"Forecast dato: {data['forecast_dato']}")
        print(f"Prognose år: {data['første_prognose_år']} - {data['sidste_prognose_år']}")
        print(f"Historiske sektioner: {list(data['historisk'].keys())}")
        if 'income' in data['historisk']:
            print(f"Antal income line items: {len(data['historisk']['income'])}")
        if 'income' in data['realiseret']:
            print(f"Realiserede income items: {list(data['realiseret']['income'].keys())[:5]}")


if __name__ == "__main__":
    start = datetime.now()
    generer_json_filer()
    slut = datetime.now()
    minutter = (slut - start).seconds // 60
    sekunder = (slut - start).seconds % 60
    print(f"\nTid brugt: {minutter} minutter og {sekunder} sekunder")
