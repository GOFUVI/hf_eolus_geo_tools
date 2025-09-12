#!/usr/bin/env bash
#
# create_grid_table.sh
#
# Description:
#   Generate a regular grid within an area defined by a GeoJSON hull and
#   create a GeoParquet-backed Athena table with those grid nodes. The
#   grid spacing can be configured and an optional CSV of manual nodes may
#   be appended.
#
# Usage:
#   $0 --profile PROFILE --database DATABASE --output-table TABLE --table-location S3_PATH --output-location S3_QUERY_OUTPUT \\
#      [--hull-file PATH --node-prefix PREFIX] \\
#      [--grid-spacing-km KM] [--buffer-km KM] [--region REGION] \\
#      [--mode overwrite|append] [--manual-csv PATH] [--help]
#
# Required:
#   --profile PROFILE          AWS CLI profile.
#   --database DATABASE        Athena database name.
#   --output-table TABLE       Name of Athena table to create.
#   --table-location S3_PATH   S3 location for GeoParquet dataset.
#   --output-location S3_PATH  S3 location for Athena query results.
#   --hull-file PATH           GeoJSON hull file defining the grid area (required with --node-prefix).
#   --node-prefix PREFIX       Prefix for grid node identifiers (required with --hull-file).
#   --manual-csv PATH          CSV with node_id,longitude,latitude when no --hull-file is given.
# Optional:
#   --grid-spacing-km KM       Grid node spacing in kilometers (default: 10).
#   --buffer-km KM             Buffer to apply around hull in kilometers (default: 0).
#   --region REGION            AWS region; if omitted, retrieved from the AWS CLI profile.
#   --mode MODE                overwrite or append (default: overwrite).
#   --help                     Show this help message and exit.

set -euo pipefail

usage() {
  sed -n '7,40p' "$0"
}

# Default parameter values
GRID_SPACING_KM=10
BUFFER_KM=0
REGION=""
OUTPUT_LOCATION=""
MODE="overwrite"
MANUAL_CSV=""
PROFILE=""
DATABASE=""
HULL_FILE=""
OUTPUT_TABLE=""
TABLE_LOCATION=""
NODE_PREFIX=""

# Parse command-line options
SHORTOPTS=
LONGOPTS=profile:,database:,output-table:,table-location:,node-prefix:,grid-spacing-km:,buffer-km:,region:,output-location:,mode:,manual-csv:,hull-file:,help
PARSED_OPTS=$(getopt --options="$SHORTOPTS" --longoptions="$LONGOPTS" --name "$0" -- "$@")
if [ $? -ne 0 ]; then
  usage
  exit 2
fi

eval set -- "$PARSED_OPTS"
while true; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2;;
    --database) DATABASE="$2"; shift 2;;
    --output-table) OUTPUT_TABLE="$2"; shift 2;;
    --table-location) TABLE_LOCATION="$2"; shift 2;;
    --node-prefix) NODE_PREFIX="$2"; shift 2;;
    --grid-spacing-km) GRID_SPACING_KM="$2"; shift 2;;
    --buffer-km) BUFFER_KM="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --output-location) OUTPUT_LOCATION="$2"; shift 2;;
    --mode) MODE="$2"; shift 2;;
    --manual-csv) MANUAL_CSV="$2"; shift 2;;
    --hull-file) HULL_FILE="$2"; shift 2;;
    --help) usage; exit 0;;
    --) shift; break;;
    *) echo "Unexpected option: $1"; usage; exit 3;;
  esac
done

# Validate required parameters
if [ -z "$PROFILE" ] || [ -z "$DATABASE" ] || [ -z "$OUTPUT_TABLE" ] || [ -z "$TABLE_LOCATION" ] || [ -z "$OUTPUT_LOCATION" ]; then
  usage
  exit 1
fi

if [ -n "$HULL_FILE" ]; then
  if [ -z "$NODE_PREFIX" ]; then
    echo "--node-prefix required when --hull-file is provided" >&2
    exit 1
  fi
  if [[ ! -f "$HULL_FILE" ]]; then
    echo "Hull file not found: $HULL_FILE" >&2
    exit 1
  fi
else
  if [ -z "$MANUAL_CSV" ]; then
    echo "Either --hull-file with --node-prefix or --manual-csv must be provided" >&2
    usage
    exit 1
  fi
fi

if [[ -n "$MANUAL_CSV" && ! -f "$MANUAL_CSV" ]]; then
  echo "Manual CSV file not found: $MANUAL_CSV" >&2
  exit 1
fi

# Determine AWS region if not specified via --region
if [ -z "$REGION" ]; then
  REGION="$(aws configure get region --profile "${PROFILE}" 2>/dev/null || true)"
  if [ -z "$REGION" ]; then
    echo "[ERROR] AWS region not specified and could not be determined from profile '${PROFILE}'." >&2
    echo "Provide --region or configure a default region for the profile." >&2
    exit 1
  fi
fi

wait_for_query() {
  local qid="$1"
  while true; do
    local status
    status=$(aws athena get-query-execution \
      --region "$REGION" \
      --profile "$PROFILE" \
      --query-execution-id "$qid" \
      --output text \
      --query "QueryExecution.Status.State")
    echo "[ATHENA] Query $qid status: $status" >&2
    case "$status" in
      SUCCEEDED) return 0;;
      FAILED|CANCELLED)
        local reason
        reason=$(aws athena get-query-execution \
          --region "$REGION" \
          --profile "$PROFILE" \
          --query-execution-id "$qid" \
          --output text \
          --query "QueryExecution.Status.StateChangeReason")
        echo "[ATHENA] Query $qid failed or was cancelled: $reason" >&2
        return 1;;
      *) sleep 2;;
    esac
  done
}

LOCAL_PARQUET="grid_nodes_$(date +%s).parquet"

if [ -n "$HULL_FILE" ]; then
  PY_ARGS="--hull $HULL_FILE --spacing-km $GRID_SPACING_KM --prefix $NODE_PREFIX --output $LOCAL_PARQUET"
  if [[ "$BUFFER_KM" != "0" ]]; then
    PY_ARGS+=" --buffer-km $BUFFER_KM"
  fi
  if [[ -n "$MANUAL_CSV" ]]; then
    PY_ARGS+=" --manual-csv $MANUAL_CSV"
  fi
else
  PY_ARGS="--output $LOCAL_PARQUET --manual-csv $MANUAL_CSV"
fi

docker run --rm -v "${PWD}":/work -w /work python:3.11-slim bash -lc \
  "pip install --no-cache-dir shapely pyproj pandas pyarrow >/tmp/pip.log && python scripts/grids/create_grid_table.py $PY_ARGS"

if [[ "$MODE" == "overwrite" ]]; then
  echo "[S3] Removing existing data at $TABLE_LOCATION" >&2
  aws s3 rm --recursive --region "$REGION" --profile "$PROFILE" "$TABLE_LOCATION"
fi

echo "[S3] Uploading $LOCAL_PARQUET to $TABLE_LOCATION" >&2
aws s3 cp --region "$REGION" --profile "$PROFILE" "$LOCAL_PARQUET" "${TABLE_LOCATION%/}/$LOCAL_PARQUET"

DROP_QUERY="DROP TABLE IF EXISTS ${OUTPUT_TABLE}"
echo "[ATHENA] Dropping table if exists: $OUTPUT_TABLE" >&2
DROP_QID=$(aws athena start-query-execution \
  --region "$REGION" \
  --profile "$PROFILE" \
  --query-execution-context "Database=$DATABASE" \
  --result-configuration "OutputLocation=$OUTPUT_LOCATION" \
  --query-string "$DROP_QUERY" \
  --output text \
  --query "QueryExecutionId")
wait_for_query "$DROP_QID"

CREATE_QUERY="CREATE EXTERNAL TABLE ${OUTPUT_TABLE} (node_id STRING, geometry BINARY) STORED AS PARQUET LOCATION '${TABLE_LOCATION%/}'"
echo "[ATHENA] Creating table: $OUTPUT_TABLE" >&2
CREATE_QID=$(aws athena start-query-execution \
  --region "$REGION" \
  --profile "$PROFILE" \
  --query-execution-context "Database=$DATABASE" \
  --result-configuration "OutputLocation=$OUTPUT_LOCATION" \
  --query-string "$CREATE_QUERY" \
  --output text \
  --query "QueryExecutionId")
wait_for_query "$CREATE_QID"

echo "Grid table ${OUTPUT_TABLE} created at ${TABLE_LOCATION}" >&2
