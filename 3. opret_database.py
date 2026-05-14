"""
Opretter PostgreSQL database og tabeller til finansielt forecast-studie.

Kør dette script én gang for at sætte databasen op.

Skift DB_KODEORD til det kodeord du satte under PostgreSQL-installationen.
"""

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# ── INDSTILLINGER — skift kodeord her ────────────────────────────────────────
DB_HOST     = "localhost"
DB_PORT     = 5432
DB_BRUGER   = "postgres"
DB_KODEORD  = "Miamia97!"   # <-- skift dette
DB_NAVN     = "forecast_studie"
# ─────────────────────────────────────────────────────────────────────────────


def opret_database():
    """Opretter selve databasen hvis den ikke allerede eksisterer."""
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_BRUGER, password=DB_KODEORD,
        dbname="postgres"  # forbind til standard-db først
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAVN,))
    if cur.fetchone():
        print(f"Database '{DB_NAVN}' eksisterer allerede.")
    else:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAVN)))
        print(f"Database '{DB_NAVN}' oprettet.")

    cur.close()
    conn.close()


def opret_tabeller():
    """Opretter alle tabeller i databasen."""
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_BRUGER, password=DB_KODEORD,
        dbname=DB_NAVN
    )
    cur = conn.cursor()

    tabeller = """

    -- ── SELSKABER ────────────────────────────────────────────────────────────
    -- Ét selskab per række — grundlæggende metadata
    CREATE TABLE IF NOT EXISTS selskaber (
        selskab_id      SERIAL PRIMARY KEY,
        ticker          VARCHAR(20)  NOT NULL,
        navn            TEXT,
        sektor          VARCHAR(100),
        boers           VARCHAR(100),
        valuta          VARCHAR(10),
        oprettet        TIMESTAMP DEFAULT NOW(),
        UNIQUE(ticker)
    );

    -- ── FORECASTS ────────────────────────────────────────────────────────────
    -- Ét forecast-par per række (én Excel-model = ét par af hist + prognose)
    -- Linker til selskab og indeholder metadata om modellen
    CREATE TABLE IF NOT EXISTS forecasts (
        forecast_id         SERIAL PRIMARY KEY,
        selskab_id          INTEGER REFERENCES selskaber(selskab_id),
        forecast_dato       DATE,           -- dato på Excel-modellen
        første_prognose_år  INTEGER,        -- t+1
        sidste_prognose_år  INTEGER,        -- t+5
        første_hist_år      INTEGER,        -- første historiske år
        har_equity_report   BOOLEAN DEFAULT FALSE,
        filnavn             TEXT,
        oprettet            TIMESTAMP DEFAULT NOW()
    );

    -- ── REGNSKABSDATA ────────────────────────────────────────────────────────
    -- Ét tal per række: én post, ét år, ét forecast
    -- Fungerer for både historiske og forecast-tal
    CREATE TABLE IF NOT EXISTS regnskabsdata (
        data_id         SERIAL PRIMARY KEY,
        forecast_id     INTEGER REFERENCES forecasts(forecast_id),
        sektion         VARCHAR(30) NOT NULL,   -- 'income', 'balance', 'cashflow'
        datakilde       VARCHAR(20) NOT NULL,   -- 'historisk' eller 'analytiker'
        line_item       VARCHAR(100) NOT NULL,  -- f.eks. 'Revenue', 'EPS_Adjusted'
        årstal          INTEGER NOT NULL,
        værdi           NUMERIC(20, 4)
    );

    -- ── EQUITY REPORTS ───────────────────────────────────────────────────────
    -- Indeholder den udtrukne tekst fra PDF equity reports
    CREATE TABLE IF NOT EXISTS equity_reports (
        report_id       SERIAL PRIMARY KEY,
        forecast_id     INTEGER REFERENCES forecasts(forecast_id),
        filnavn         TEXT,
        tekst_fuld      TEXT,           -- hele PDF-teksten
        antal_tegn      INTEGER,
        oprettet        TIMESTAMP DEFAULT NOW()
    );

    -- ── LLM FORECASTS ────────────────────────────────────────────────────────
    -- Gemmer LLM-genererede prognoser
    -- Én række per line item per år per betingelse
    CREATE TABLE IF NOT EXISTS llm_forecasts (
        llm_id          SERIAL PRIMARY KEY,
        forecast_id     INTEGER REFERENCES forecasts(forecast_id),
        betingelse      VARCHAR(10) NOT NULL,   -- 'A-P1', 'A-P3', 'B-P1', 'B-P3'
        line_item       VARCHAR(100) NOT NULL,
        årstal          INTEGER NOT NULL,
        værdi           NUMERIC(20, 4),
        oprettet        TIMESTAMP DEFAULT NOW()
    );

    -- ── EVALUERING ───────────────────────────────────────────────────────────
    -- Gemmer beregnede forecast-fejl til analyse
    CREATE TABLE IF NOT EXISTS evaluering (
        eval_id         SERIAL PRIMARY KEY,
        forecast_id     INTEGER REFERENCES forecasts(forecast_id),
        betingelse      VARCHAR(10),            -- 'A-P1', 'A-P3', 'B-P1', 'B-P3', 'analytiker'
        metrik          VARCHAR(50),            -- 'EPS', 'Revenue_Growth', 'EBIT_Margin', 'Asset_Turnover'
        horisont        INTEGER,                -- 1-5 (t+1 til t+5)
        forecast_værdi  NUMERIC(20, 4),
        realiseret      NUMERIC(20, 4),
        fejl            NUMERIC(20, 4),         -- forecast - realiseret
        abs_fejl        NUMERIC(20, 4),         -- |forecast - realiseret|
        oprettet        TIMESTAMP DEFAULT NOW()
    );

    -- ── LOG ──────────────────────────────────────────────────────────────────
    -- Logger hændelser og fejl under dataindlæsning og API-kald
    CREATE TABLE IF NOT EXISTS log (
        log_id          SERIAL PRIMARY KEY,
        tidspunkt       TIMESTAMP DEFAULT NOW(),
        handling        VARCHAR(100),   -- f.eks. 'excel_udtrækning', 'llm_api_kald'
        filnavn         TEXT,
        status          VARCHAR(20),    -- 'ok', 'fejl', 'advarsel'
        besked          TEXT
    );

    """

    cur.execute(tabeller)
    conn.commit()
    print("Alle tabeller oprettet.")

    # Opret indekser for hurtigere søgning
    indekser = """
    CREATE INDEX IF NOT EXISTS idx_regnskab_forecast
        ON regnskabsdata(forecast_id);
    CREATE INDEX IF NOT EXISTS idx_regnskab_item
        ON regnskabsdata(line_item, årstal);
    CREATE INDEX IF NOT EXISTS idx_llm_forecast
        ON llm_forecasts(forecast_id, betingelse);
    CREATE INDEX IF NOT EXISTS idx_eval_betingelse
        ON evaluering(betingelse, metrik, horisont);
    """
    cur.execute(indekser)
    conn.commit()
    print("Indekser oprettet.")

    cur.close()
    conn.close()


def test_forbindelse():
    """Tester at databasen virker og viser tabel-oversigt."""
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_BRUGER, password=DB_KODEORD,
        dbname=DB_NAVN
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tabeller = cur.fetchall()
    print(f"\nDatabasen '{DB_NAVN}' indeholder {len(tabeller)} tabeller:")
    for t in tabeller:
        print(f"  - {t[0]}")
    cur.close()
    conn.close()


# ── KØR ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Opretter database...")
    opret_database()

    print("\nOpretter tabeller...")
    opret_tabeller()

    print("\nTester forbindelse...")
    test_forbindelse()

    print("\nFærdig! Databasen er klar til brug.")