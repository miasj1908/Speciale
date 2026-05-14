import psycopg2

conn = psycopg2.connect(
    dbname="forecast_studie",
    user="postgres",
    password="Miamia97!",
    host="localhost"
)
cur = conn.cursor()

# Read and execute SQL
with open('setup_tables.sql', 'r') as f:
    sql = f.read()
    cur.execute(sql)

conn.commit()
cur.close()
conn.close()

print("✅ Database tables created successfully!")
