#!/usr/bin/env python3
import sys
import re

month_map = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
}

# Read all files and parse
lines = []
for line in sys.stdin:
    filepath = line.strip()
    if not filepath:
        continue

    # Extract date from title: "Month Day, Year"
    date_match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})', filepath, re.IGNORECASE)

    # Extract timestamp: NNNN.NN-NNNN.NN.mp4
    ts_match = re.search(r'_(\d+\.\d+)-(\d+\.\d+)\.mp4$', filepath)

    if date_match and ts_match:
        month_name = date_match.group(1).lower()
        day = int(date_match.group(2))
        year = int(date_match.group(3))
        month = month_map.get(month_name, 0)

        start_ts = float(ts_match.group(1))

        # Store as tuple: (date, timestamp, filepath)
        sort_key = f"{year:04d}-{month:02d}-{day:02d}"
        lines.append((sort_key, start_ts, filepath))
    else:
        # Files without date or timestamp go to the end
        lines.append(("9999-99-99", 9999999.99, filepath))

# Sort by date first, then timestamp
lines.sort(key=lambda x: (x[0], x[1]))

# Output just the filepaths
for date, ts, filepath in lines:
    print(filepath)
