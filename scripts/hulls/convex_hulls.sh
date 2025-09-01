#!/usr/bin/env bash
#
# convex_hulls.sh
#
# Description:
#   Retrieve unique WKB point geometries from one or more Athena tables,
#   compute their convex hulls using Python in Docker, and combine the hulls
#   via union or intersection. The resulting polygon is written to a user
#   supplied GeoJSON file.
#
# Usage:
#   $0 --profile PROFILE [--database DATABASE] --tables TABLE1[,TABLE2,...] \
#      --output-file PATH --output-location S3_PATH \
#      [--operation union|intersection] [--region REGION] [--help]
#
# Required parameters:
#   --profile PROFILE          AWS CLI profile.
#   --database DATABASE        Default Athena database (optional if tables are qualified).
#   --tables LIST              Comma-separated list of source tables. Each item can be
#                              either TABLE or DATABASE.TABLE. If TABLE is unqualified,
#                              --database is required as a default.
#   --output-file PATH         Destination GeoJSON file.
#   --output-location S3_PATH  S3 location for Athena query results.
#
# Optional parameters:
#   --operation MODE           union or intersection (default: union).
#   --region REGION            AWS region; if omitted, uses profile's region.
#   --help                     Show this help message and exit.

set -euo pipefail

usage() {
  sed -n '7,40p' "$0"
}

# Default values
OPERATION="union"
REGION=""
PROFILE=""
DATABASE=""
TABLES=""
OUTPUT_FILE=""
OUTPUT_LOCATION=""

SHORTOPTS=""
LONGOPTS=profile:,database:,tables:,output-file:,output-location:,operation:,region:,help
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
    --tables) TABLES="$2"; shift 2;;
    --output-file) OUTPUT_FILE="$2"; shift 2;;
    --output-location) OUTPUT_LOCATION="$2"; shift 2;;
    --operation) OPERATION="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --help) usage; exit 0;;
    --) shift; break;;
    *) echo "Unexpected option: $1"; usage; exit 3;;
  esac
 done

# Validate required parameters
if [ -z "$PROFILE" ] || [ -z "$TABLES" ] || [ -z "$OUTPUT_FILE" ] || [ -z "$OUTPUT_LOCATION" ]; then
  usage
  exit 1
fi

if [[ "$OPERATION" != "union" && "$OPERATION" != "intersection" ]]; then
  echo "--operation must be 'union' or 'intersection'" >&2
  exit 1
fi

# Determine region if not provided
if [ -z "$REGION" ]; then
  REGION="$(aws configure get region --profile "${PROFILE}" 2>/dev/null || true)"
  if [ -z "$REGION" ]; then
    echo "[ERROR] AWS region not specified and could not be determined from profile '${PROFILE}'." >&2
    echo "Provide --region or configure a default region for the profile." >&2
    exit 1
  fi
fi

# Wait for Athena query to finish
wait_for_query() {
  local qid=$1
  while true; do
    local status
    status=$(aws athena get-query-execution \
      --region "$REGION" \
      --profile "$PROFILE" \
      --query-execution-id "$qid" \
      --output text \
      --query "QueryExecution.Status.State")
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

IFS=',' read -r -a TABLE_ARRAY <<< "$TABLES"
INPUT_FILES=()
for t in "${TABLE_ARRAY[@]}"; do
  # Parse table reference; support DATABASE.TABLE or TABLE with default DATABASE
  if [[ "$t" == *.* ]]; then
    T_DB="${t%%.*}"
    T_NAME="${t#*.}"
  else
    if [ -z "$DATABASE" ]; then
      echo "[ERROR] Table '$t' is unqualified and no --database was provided." >&2
      exit 1
    fi
    T_DB="$DATABASE"
    T_NAME="$t"
  fi

  QUERY="SELECT DISTINCT to_hex(geometry) AS wkb FROM ${T_NAME}"
  echo "[ATHENA] Querying unique geometries from ${T_DB}.${T_NAME}" >&2
  QID=$(aws athena start-query-execution \
    --region "$REGION" \
    --profile "$PROFILE" \
    --query-execution-context "Database=$T_DB" \
    --result-configuration "OutputLocation=$OUTPUT_LOCATION" \
    --query-string "$QUERY" \
    --output text \
    --query "QueryExecutionId")
  wait_for_query "$QID"
  S3_PATH="${OUTPUT_LOCATION%/}/$QID.csv"
  # Sanitize local filename to include database and table
  LOCAL_FILE="${T_DB}_${T_NAME}_wkb.csv"
  echo "[S3] Downloading results to $LOCAL_FILE" >&2
  aws s3 cp --region "$REGION" --profile "$PROFILE" "$S3_PATH" "$LOCAL_FILE"
  INPUT_FILES+=("$LOCAL_FILE")
 done

PY_ARGS="--operation $OPERATION --output $OUTPUT_FILE ${INPUT_FILES[*]}"

docker run --rm -v "${PWD}":/work -w /work python:3.11-slim bash -lc \
  "pip install --no-cache-dir shapely pandas >/tmp/pip.log && python scripts/hulls/convex_hulls.py $PY_ARGS"

echo "GeoJSON written to $OUTPUT_FILE" >&2
