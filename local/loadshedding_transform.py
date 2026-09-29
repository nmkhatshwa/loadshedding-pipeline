"""
Local test of the load shedding transformation logic.
Before this ever touches Lambda, we prove it works on a real CSV file
sitting on disk. Same function signature will be reused inside Lambda later,
just swapping local file read for an S3 GetObject call.

CSV columns (Bethal, Mpumalanga rotation schedule):
    date_of_month, start_time, finsh_time, stage

Note: date_of_month (1-31) is the area's position in the load shedding
ROTATION cycle, not a calendar date -- Eskom/municipalities publish
schedules this way because the actual calendar date that maps to
"rotation day 14" shifts over time. start_time/finsh_time are HH:MM
with no date attached, and some windows cross midnight
(e.g. 23:00 -> 01:30), so duration must account for that wraparound.
"""

import csv
from collections import defaultdict
from datetime import datetime
import json


def parse_time(value):
    """Parse HH:MM strings into a time object."""
    return datetime.strptime(value, "%H:%M").time()


def duration_hours(start_t, finish_t):
    """
    Hours between two HH:MM times, handling overnight wraparound
    (e.g. 23:00 -> 01:30 is 2.5 hours, not a negative number).
    """
    start_minutes = start_t.hour * 60 + start_t.minute
    finish_minutes = finish_t.hour * 60 + finish_t.minute
    if finish_minutes <= start_minutes:
        finish_minutes += 24 * 60  # crossed midnight
    return (finish_minutes - start_minutes) / 60


def transform(rows):
    """
    Given a list of dict rows (date_of_month, start_time, finsh_time, stage),
    return total outage hours per rotation day, grouped by stage.

    Output shape:
    {
        "1": {
            "5": 2.5,
            "6": 2.5,
            ...
        },
        ...
    }
    """
    # date_of_month -> stage -> total_hours
    totals = defaultdict(lambda: defaultdict(float))

    for row in rows:
        day = row["date_of_month"]
        stage = row["stage"]

        start_t = parse_time(row["start_time"])
        finish_t = parse_time(row["finsh_time"])

        hours = duration_hours(start_t, finish_t)
        totals[day][stage] += hours

    # Convert nested defaultdicts to plain dicts, round cleanly, sort by day number
    return {
        day: {stage: round(hrs, 2) for stage, hrs in stages.items()}
        for day, stages in sorted(totals.items(), key=lambda kv: int(kv[0]))
    }


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


if __name__ == "__main__":
    # --- Small embedded sample so this runs with zero setup ---
    sample_rows = [
        {"date_of_month": "1", "start_time": "01:00", "finsh_time": "03:30", "stage": "5"},
        {"date_of_month": "1", "start_time": "01:00", "finsh_time": "03:30", "stage": "6"},
        {"date_of_month": "1", "start_time": "23:00", "finsh_time": "01:30", "stage": "7"},
        {"date_of_month": "2", "start_time": "05:00", "finsh_time": "09:30", "stage": "8"},
    ]

    print("=== Test with embedded sample data ===")
    result = transform(sample_rows)
    print(json.dumps(result, indent=2))

    # --- Test against the REAL uploaded CSV ---
    rows = load_csv("mpumalanga-bethal.csv")
    result = transform(rows)
    print("\n=== Full transform on mpumalanga-bethal.csv ===")
    print(json.dumps(result, indent=2))

    # Save output locally so you can inspect it before it ever touches AWS
    with open("processed_output.json", "w") as f:
        json.dump(result, f, indent=2)
    print("\nSaved to processed_output.json")
