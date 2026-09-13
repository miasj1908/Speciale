"""
Matcher rapport JSON-filer (per_report_json zip) med observationer i
Equity reports needed.csv ved at matche på source_file + selskabsnavn.

Krav:
    pip install pandas
"""

import zipfile
import json
import pandas as pd

# ── INDSTILLINGER ─────────────────────────────────────────────────────────────

CSV_STI         = r"C:\Users\miasj\OneDrive\Skrivebord\Speciale Download\Equity reports needed.csv"
ZIP_STI         = r"C:\Users\miasj\OneDrive\Skrivebord\Speciale Download\per_report_json (1).zip"
OUTPUT_STI      = r"C:\Users\miasj\PythonProjects\matchede_rapport_json.csv"
INGEN_MATCH_STI = r"C:\Users\miasj\PythonProjects\rapport_json_ingen_match.csv"

# ─────────────────────────────────────────────────────────────────────────────


def normaliser(tekst):
    if not tekst:
        return ''
    return str(tekst).strip().lower()


def udtræk_selskabsnavn(full_path):
    """Udtrækker selskabsmappenavn fra full_path, fx 'Ball Corporation (BLL)'."""
    sti = full_path.replace('\\', '/')
    dele = sti.split('/')
    if len(dele) >= 2:
        return dele[-2].strip()
    return ''


def indlaes_json_index(zip_sti):
    """Læser alle JSON-filer fra zip og bygger et opslags-dict:
       (source_file_norm, selskab_norm) → json_filnavn
    """
    index = {}
    source_only = {}  # fallback: kun source_file
    z = zipfile.ZipFile(zip_sti)
    navne = [n for n in z.namelist() if n.endswith('.json')]
    print(f"Fandt {len(navne)} JSON-filer i zip")

    for navn in navne:
        data = json.loads(z.read(navn))
        source = normaliser(data.get('source_file', ''))
        selskab = normaliser(udtræk_selskabsnavn(data.get('full_path', '')))
        json_fil = navn.split('/')[-1]

        if source:
            index[(source, selskab)] = json_fil
            # Gem også uden selskab som fallback
            if source not in source_only:
                source_only[source] = []
            source_only[source].append(json_fil)

    z.close()
    return index, source_only


def koer_matching():
    # Indlæs CSV
    print("Indlæser CSV...")
    df_csv = pd.read_csv(CSV_STI, sep=';')
    print(f"CSV: {len(df_csv)} observationer, {df_csv['Company'].nunique()} selskaber")

    # Indlæs JSON-index fra zip
    print("\nIndlæser JSON-filer fra zip...")
    index, source_only = indlaes_json_index(ZIP_STI)

    # Match
    print("\nMatcher...")
    matches = []
    ingen_match = []

    for _, row in df_csv.iterrows():
        source_norm  = normaliser(row['Equity Report file name'])
        selskab_norm = normaliser(row['Company'])

        # Forsøg 1: match på source_file + selskabsnavn
        json_fil = index.get((source_norm, selskab_norm))

        # Forsøg 2: match kun på source_file (hvis ét entydigt resultat)
        if not json_fil:
            kandidater = source_only.get(source_norm, [])
            if len(kandidater) == 1:
                json_fil = kandidater[0]

        if json_fil:
            matches.append({
                'json_name':        row['json name for cash low'],
                'company':          row['Company'],
                'sector':           row['Sector'],
                'year':             row['Year'],
                'equity_fil':       row['Equity Report file name'],
                'rapport_json_fil': json_fil,
                'days_diff':        row['Difference in days'],
            })
        else:
            ingen_match.append({
                'json_name':  row['json name for cash low'],
                'company':    row['Company'],
                'equity_fil': row['Equity Report file name'],
                'aarsag':     'Ingen JSON med samme source_file',
            })

    df_matches = pd.DataFrame(matches)
    df_ingen   = pd.DataFrame(ingen_match)

    # Gem
    df_matches.to_csv(OUTPUT_STI, index=False, sep=';')
    df_ingen.to_csv(INGEN_MATCH_STI, index=False, sep=';')

    # Rapport
    print("\n" + "="*50)
    print("RESULTAT")
    print("="*50)
    print(f"Matchede observationer:    {len(df_matches)} / {len(df_csv)}")
    print(f"Ingen match:               {len(df_ingen)}")
    print(f"\nOutput gemt til:           {OUTPUT_STI}")
    print(f"Ingen-match gemt til:      {INGEN_MATCH_STI}")

    if len(df_ingen) > 0:
        print(f"\nFørste 10 uden match:")
        print(df_ingen[['equity_fil', 'company']].head(10).to_string(index=False))

    return df_matches, df_ingen


if __name__ == "__main__":
    koer_matching()
