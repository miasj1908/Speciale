import json
import os

filepath = "batch_inputs/batch_input_baseline_gpt-4o.jsonl"

print(f"File size: {os.path.getsize(filepath) / 1024 / 1024:.1f} MB")

errors = 0
with open(filepath, "r", encoding="utf-8") as f:
    for i, line in enumerate(f):
        try:
            data = json.loads(line)
            # Check required fields
            if "custom_id" not in data:
                print(f"Line {i}: Missing custom_id")
                errors += 1
            if "method" not in data:
                print(f"Line {i}: Missing method")
                errors += 1
            if "url" not in data:
                print(f"Line {i}: Missing url")
                errors += 1
            if "body" not in data:
                print(f"Line {i}: Missing body")
                errors += 1
        except json.JSONDecodeError as e:
            print(f"Line {i}: JSON error: {e}")
            errors += 1

        if i == 0:
            print(f"\nFirst line preview:")
            print(f"  custom_id: {data.get('custom_id')}")
            print(f"  model: {data.get('body', {}).get('model')}")
            print(f"  prompt length: {len(data.get('body', {}).get('messages', [{}])[-1].get('content', ''))}")

print(f"\nTotal lines: {i+1}")
print(f"Total errors: {errors}")
