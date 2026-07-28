#!/usr/bin/env python3
import csv
import json
import sys

src, dst = sys.argv[1:3]
with open(src, encoding="utf-8") as handle:
    rows = json.load(handle)
fields = [
    "timestamp", "run_id", "scenario", "node", "pod", "upf", "profile", "cycle",
    "residuals", "dr", "predicted_residuals", "rai", "ttc", "decision",
    "gc_percent", "completed",
]
with open(dst, "w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
