import zipfile

zip_path = r"C:\Users\miasj\OneDrive\Skrivebord\Speciale Download\per_report_json (1).zip"

with zipfile.ZipFile(zip_path, 'r') as z:
    json_files = [f for f in z.namelist() if f.endswith('.json')]
    print(f"Antal JSON-filer: {len(json_files)}")
