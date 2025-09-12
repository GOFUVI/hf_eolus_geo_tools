#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# finalize_geoparquet.sh
# -----------------------------------------------------------------------------
# Description:
#   Consolidate Parquet files produced by aggregate_core.sh and add GeoParquet
#   metadata. Optionally repairs partitions in Athena so the table reflects the
#   updated files.
#
# Usage:
#   ./finalize_geoparquet.sh \
#     --db-name DB_NAME \
#     --bucket-name BUCKET \
#     --output-prefix PREFIX \
#     --output-table TABLE \
#     [--partition-cols COL1,COL2] \
#     [--geometry-column NAME] \
#     [--profile PROFILE] \
#     [--log-dir DIR] \
#     [--help]
#
# Requirements:
#   - bash
#   - AWS CLI
#   - jq
#   - docker
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  sed -n '5,27p' "$0"
}

run_aws() {
  echo "Running: aws $*" >> "$LOG_FILE"
  output=$(aws "$@" 2>&1)
  rc=$?
  echo "$output" >> "$LOG_FILE"
  if [ $rc -ne 0 ]; then
    return $rc
  fi
  echo "$output"
  return 0
}

wait_for_query() {
  local qid="$1"
  log "Waiting for Athena query $qid to complete..."
  while true; do
    output=$(run_aws athena get-query-execution --query-execution-id "$qid" --region $REGION --profile "$PROFILE")
    status=$(echo "$output" | jq -r '.QueryExecution.Status.State')
    if [ "$status" = "SUCCEEDED" ]; then
      log "Athena query $qid succeeded."
      break
    elif [ "$status" = "FAILED" ] || [ "$status" = "CANCELLED" ]; then
      log "Athena query $qid failed with status $status."
      exit 1
    else
      log "Athena query $qid status: $status. Waiting..."
      sleep 5
    fi
  done
}

# Default values
PROFILE="default"
DB_NAME=""
BUCKET_NAME=""
OUTPUT_PREFIX=""
OUTPUT_TABLE=""
PARTITION_COLS=""
GEOMETRY_COLUMN="geometry"

SHORTOPTS=""
LONGOPTS="db-name:,bucket-name:,output-prefix:,output-table:,partition-cols:,geometry-column:,profile:,log-dir:,help"
PARSED=$(getopt --options="$SHORTOPTS" --longoptions="$LONGOPTS" --name "$0" -- "$@") || { usage; exit 2; }
eval set -- "$PARSED"
while true; do
  case "$1" in
    --db-name) DB_NAME="$2"; shift 2;;
    --bucket-name) BUCKET_NAME="$2"; shift 2;;
    --output-prefix) OUTPUT_PREFIX="$2"; shift 2;;
    --output-table) OUTPUT_TABLE="$2"; shift 2;;
    --partition-cols) PARTITION_COLS="$2"; shift 2;;
    --geometry-column) GEOMETRY_COLUMN="$2"; shift 2;;
    --profile) PROFILE="$2"; shift 2;;
    --log-dir) LOG_DIR="$2"; shift 2;;
    --help) usage; exit 0;;
    --) shift; break;;
  esac
done

if [ -z "$DB_NAME" ] || [ -z "$BUCKET_NAME" ] || [ -z "$OUTPUT_PREFIX" ] || [ -z "$OUTPUT_TABLE" ]; then
  echo "Missing required arguments" >&2
  usage
  exit 1
fi

ORIG_PWD="$(pwd)"
if [ -n "${LOG_DIR:-}" ]; then
  mkdir -p "$LOG_DIR"
else
  LOG_DIR="$ORIG_PWD"
fi
LOG_DIR="$(realpath "$LOG_DIR")"
SCRIPT_NAME=$(basename "$0")
SCRIPT_BASE="${SCRIPT_NAME%.*}"
LOG_FILE="${LOG_DIR}/${SCRIPT_BASE}_${OUTPUT_TABLE}.log"
rm -f "$LOG_FILE"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"; }

REGION="eu-west-3"

S3_PATH="s3://${BUCKET_NAME}/${OUTPUT_PREFIX%/}"
DATA_DIR=$(mktemp -d "${LOG_DIR}/geo_meta_XXXXXX")
trap 'rm -rf "$DATA_DIR"' EXIT

log "Syncing dataset from $S3_PATH to $DATA_DIR"
aws s3 sync "$S3_PATH" "$DATA_DIR" --exclude "query_results/*" --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1

DATA_FILES_COUNT=$(find "$DATA_DIR" -type f \
  ! -name '.*' ! -name '_*' ! -name 'SUCCESS' | wc -l | awk '{print $1}')
log "Downloaded files count: ${DATA_FILES_COUNT}"
if [ "$DATA_FILES_COUNT" -eq 0 ]; then
  log "No data files found under $S3_PATH. Aborting to avoid data loss."
  exit 1
fi

log "Merging Parquet files to a single file per partition"
docker run --rm \
  -v "${PWD}":/work \
  -v "${DATA_DIR}":/data:rw \
  -w /work \
  python:3.11-slim bash -lc "pip install --no-cache-dir 'pyarrow==16.1.0' 'shapely==2.0.4' >/tmp/pip.log && python scripts/aggregation/merge_parquet.py --root /data && python scripts/aggregation/add_geoparquet_metadata.py --local-path /data --geometry-column '${GEOMETRY_COLUMN}'" >> "$LOG_FILE" 2>&1

log "Syncing dataset back to $S3_PATH"
aws s3 sync "$DATA_DIR" "$S3_PATH" --exclude "query_results/*" --delete --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1

if [ -n "$PARTITION_COLS" ]; then
  log "Repairing partitions in Athena for ${DB_NAME}.${OUTPUT_TABLE}"
  QID=$(run_aws athena start-query-execution \
    --query-string "MSCK REPAIR TABLE ${DB_NAME}.${OUTPUT_TABLE}" \
    --query-execution-context "Database=${DB_NAME}" \
    --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${OUTPUT_PREFIX%/}_athena_results" \
    --region $REGION --profile "$PROFILE" --output text --query 'QueryExecutionId')
  wait_for_query "$QID"
fi

log "Finalization completed: GeoParquet dataset at ${S3_PATH}"
