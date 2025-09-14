# Athena Utilities

## Overview

The `scripts/athena_utils` folder contains lightweight helpers that interact with Amazon Athena/Glue via the AWS CLI. It currently includes a script to create a view that adds a timestamp column rounded to the nearest half hour (with tie-breaks rounding down).

---

## `create_half_hour_view.sh`

Creates or replaces an Athena view that selects all columns from a source table and adds a new column with the timestamp rounded to the nearest half hour. Tie cases round down:

- `HH:00:00–HH:15:00` → `HH:00:00`
- `HH:15:01–HH:45:00` → `HH:30:00`
- `HH:45:01–(HH+1):00:00` → `(HH+1):00:00`

Examples: `00:06:45 → 00:00:00`, `00:18:45 → 00:30:00`, `23:46:42 → 00:00:00 (next day)`, `00:37:54 → 00:30:00`, `00:15:00 → 00:00:00`, `00:45:00 → 00:30:00`.

Path: `scripts/athena_utils/create_half_hour_view.sh`

### Requirements

- Bash
- AWS CLI configured (`aws configure` or profiles)
- `jq` for CLI JSON parsing

### Usage

```bash
./scripts/athena_utils/create_half_hour_view.sh \
  --source-db <SOURCE_DB> \
  --source-table <SOURCE_TABLE> \
  --timestamp-col <TIMESTAMP_COL> \
  --new-column-name <NEW_COLUMN> \
  --view-db <VIEW_DB> \
  --view-name <VIEW_NAME> \
  [--profile <AWS_PROFILE>] \
  [--results-s3 s3://bucket/prefix] \
  [--region <REGION>] \
  [--quote-identifiers] \
  [--log-dir <LOG_DIR>]
```

### Parameters

`--source-db`
: Athena database containing the source table. Required.

`--source-table`
: Source table name. Required.

`--timestamp-col`
: Name of the timestamp column to round. Default: `timestamp`.

`--new-column-name`
: Name of the new rounded-timestamp column. Default: `ts_half_hour`.

`--view-db`
: Athena database where the view will be created. Required.

`--view-name`
: Name of the view to create/replace. Required.

`--profile`
: AWS CLI profile. Default: `default`.

`--results-s3`
: S3 location for Athena query results. If omitted, the workgroup’s default is used.

`--region`
: AWS region. Default: `eu-west-3`.

`--quote-identifiers`
: When set, quotes identifiers (DB, table, view, columns) with double quotes in the SQL. Useful for reserved words or special characters.

`--log-dir`
: Directory to write logs and the generated SQL (`*.view.sql`). Default: current directory.

### Examples

- Basic:

```bash
./scripts/athena_utils/create_half_hour_view.sh \
  --source-db raw_db \
  --source-table events \
  --timestamp-col ts \
  --new-column-name ts_rounded \
  --view-db analytics \
  --view-name events_halfhour
```

- With region, profile, results and quoting:

```bash
./scripts/athena_utils/create_half_hour_view.sh \
  --source-db raw_db \
  --source-table events \
  --timestamp-col ts \
  --new-column-name ts_rounded \
  --view-db analytics \
  --view-name events_halfhour \
  --region eu-central-1 \
  --profile myprofile \
  --results-s3 s3://my-bucket/athena_results/ \
  --quote-identifiers \
  --log-dir ./logs
```

### SQL Logic

The script generates and executes:

```sql
CREATE OR REPLACE VIEW <view_db>.<view_name> AS
SELECT
  t.*,
  date_trunc('hour', t.<ts_col>) + CASE
    WHEN minute(t.<ts_col>)*60 + second(t.<ts_col>) <= 15*60 THEN INTERVAL '0' minute
    WHEN minute(t.<ts_col>)*60 + second(t.<ts_col>) <= 45*60 THEN INTERVAL '30' minute
    ELSE INTERVAL '60' minute
  END AS <new_col>
FROM <source_db>.<source_table> t;
```

- Correctly handles hour/day rollovers.
- If `--quote-identifiers` is set, identifiers are quoted in the SQL (for example, `"events"."ts"`).

### Notes

- The script validates the existence of the source table in Glue before executing the query.
- The final SQL is saved as `<log_dir>/create_half_hour_view_<db>_<view>.view.sql`.
- View creation does not write data to S3; `--results-s3` only controls the Athena query’s output location.
- If you need a materialized column (not just a view), create a CTAS table using the same rounding expression.

