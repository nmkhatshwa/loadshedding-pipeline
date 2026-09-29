"""
AWS Lambda handler for the load shedding data pipeline.

Trigger: S3 ObjectCreated event on the raw-data bucket.
Behavior: reads the uploaded CSV, computes total outage hours per
rotation day grouped by stage, writes the result as JSON to the
processed-data bucket, and logs progress to CloudWatch.

Same transform() logic as the locally-tested version --
only the I/O (how we read the file in, how we write the result out)
has changed, from local disk to S3.
"""

import csv
import io
import json
import os
from collections import defaultdict
from datetime import datetime

import boto3

s3 = boto3.client("s3")

# Name of the bucket Lambda should write processed output to.
# Set this as an environment variable in the Lambda console
# (Configuration tab -> Environment variables) rather than hardcoding it,
# so the code doesn't need editing if the bucket name ever changes.
PROCESSED_BUCKET = os.environ["PROCESSED_BUCKET"]


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
    """
    totals = defaultdict(lambda: defaultdict(float))

    for row in rows:
        day = row["date_of_month"]
        stage = row["stage"]

        start_t = parse_time(row["start_time"])
        finish_t = parse_time(row["finsh_time"])

        hours = duration_hours(start_t, finish_t)
        totals[day][stage] += hours

    return {
        day: {stage: round(hrs, 2) for stage, hrs in stages.items()}
        for day, stages in sorted(totals.items(), key=lambda kv: int(kv[0]))
    }


def lambda_handler(event, context):
    # S3 events can technically contain multiple records; in practice
    # a single file upload produces exactly one.
    record = event["Records"][0]
    source_bucket = record["s3"]["bucket"]["name"]
    source_key = record["s3"]["object"]["key"]

    print(f"Triggered by upload: s3://{source_bucket}/{source_key}")

    try:
        # 1. Read the raw CSV directly from S3 into memory
        response = s3.get_object(Bucket=source_bucket, Key=source_key)
        csv_content = response["Body"].read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        print(f"Read {len(rows)} rows from {source_key}")

        # 2. Run the same transformation logic tested locally
        result = transform(rows)

        # 3. Write the processed result to the processed-data bucket
        output_key = source_key.replace("raw/", "processed/").replace(
            ".csv", "_summary.json"
        )
        s3.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=output_key,
            Body=json.dumps(result, indent=2),
            ContentType="application/json",
        )

        print(f"Wrote processed output to s3://{PROCESSED_BUCKET}/{output_key}")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {"message": "Success", "output_location": output_key}
            ),
        }

    except Exception as e:
        # Logged to CloudWatch automatically since Lambda captures stdout/stderr.
        # Step 8 will expand this with a "move failed files" behaviour --
        # for now, a clear log line is enough to prove error handling exists.
        print(f"ERROR processing {source_key}: {str(e)}")
        raise
