#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# create_half_hour_view.sh
# -----------------------------------------------------------------------------
# Description:
#   Creates or replaces an Athena view that projects all columns from a source
#   table and adds a new column with the source timestamp rounded to the nearest
#   half hour, with tie-breaks rounded down (e.g., :15 -> :00, :45 -> :30).
#   Supports optional identifier quoting and custom region.
#
# Usage:
#   ./create_half_hour_view.sh \
#     --source-db SRC_DB \
#     --source-table SRC_TABLE \
#     --timestamp-col TS_COL \
#     --new-column-name NEW_COL \
#     --view-db VIEW_DB \
#     --view-name VIEW_NAME \
#     [--profile PROFILE] \
#     [--results-s3 s3://bucket/prefix] \
#     [--region REGION] \
#     [--quote-identifiers] \
#     [--log-dir DIR] \
#     [--help]
#
# Requirements:
#   - bash
#   - AWS CLI
#   - jq
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  sed -n '5,33p' "$0"
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
      reason=$(echo "$output" | jq -r '.QueryExecution.Status.StateChangeReason')
      log "Athena query $qid failed with status $status: $reason"
      exit 1
    else
      log "Athena query $qid status: $status. Waiting..."
      sleep 5
    fi
  done
}

# Identifier quoting helpers
quote_ident() {
  local s="$1"
  s="${s//\"/\"\"}"
  printf '"%s"' "$s"
}

maybe_quote() {
  local s="$1"
  if [ "$QUOTE_IDENTIFIERS" = true ]; then
    quote_ident "$s"
  else
    printf '%s' "$s"
  fi
}

# Defaults
PROFILE="default"
SRC_DB=""
SRC_TABLE=""
TS_COL="timestamp"
NEW_COL="ts_half_hour"
VIEW_DB=""
VIEW_NAME=""
RESULTS_S3=""
REGION="eu-west-3"
QUOTE_IDENTIFIERS=false

SHORTOPTS=""
LONGOPTS="source-db:,source-table:,timestamp-col:,new-column-name:,view-db:,view-name:,profile:,results-s3:,region:,quote-identifiers,log-dir:,help"
PARSED=$(getopt --options="$SHORTOPTS" --longoptions="$LONGOPTS" --name "$0" -- "$@") || { usage; exit 2; }
eval set -- "$PARSED"
while true; do
  case "$1" in
    --source-db) SRC_DB="$2"; shift 2;;
    --source-table) SRC_TABLE="$2"; shift 2;;
    --timestamp-col) TS_COL="$2"; shift 2;;
    --new-column-name) NEW_COL="$2"; shift 2;;
    --view-db) VIEW_DB="$2"; shift 2;;
    --view-name) VIEW_NAME="$2"; shift 2;;
    --profile) PROFILE="$2"; shift 2;;
    --results-s3) RESULTS_S3="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --quote-identifiers) QUOTE_IDENTIFIERS=true; shift 1;;
    --log-dir) LOG_DIR="$2"; shift 2;;
    --help) usage; exit 0;;
    --) shift; break;;
  esac
done

if [ -z "$SRC_DB" ] || [ -z "$SRC_TABLE" ] || [ -z "$VIEW_DB" ] || [ -z "$VIEW_NAME" ] || [ -z "$TS_COL" ] || [ -z "$NEW_COL" ]; then
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
LOG_FILE="${LOG_DIR}/${SCRIPT_BASE}_${VIEW_DB}_${VIEW_NAME}.log"
rm -f "$LOG_FILE"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"; }

SQL_PREFIX="${LOG_DIR}/${SCRIPT_BASE}_${VIEW_DB}_${VIEW_NAME}"

log "Validating source table exists in Glue Catalog: ${SRC_DB}.${SRC_TABLE} (region: ${REGION})"
if ! run_aws glue get-table --database-name "$SRC_DB" --name "$SRC_TABLE" --profile "$PROFILE" --region "$REGION" >/dev/null; then
  log "Missing source table: ${SRC_DB}.${SRC_TABLE}. Check --source-db/--source-table."
  exit 1
fi

# Build fully-qualified names with optional quoting for SQL text
SQL_SRC_DB=$(maybe_quote "$SRC_DB")
SQL_SRC_TABLE=$(maybe_quote "$SRC_TABLE")
SQL_VIEW_DB=$(maybe_quote "$VIEW_DB")
SQL_VIEW_NAME=$(maybe_quote "$VIEW_NAME")
SQL_TS_COL=$(maybe_quote "$TS_COL")
SQL_NEW_COL=$(maybe_quote "$NEW_COL")

# Build rounding expression with tie-break down at :15 and :45
# Use t alias; quote column if requested: t."col" vs t.col
if [ "$QUOTE_IDENTIFIERS" = true ]; then
  TS_REF="t.$(quote_ident "$TS_COL")"
else
  TS_REF="t.${TS_COL}"
fi

read -r -d '' ROUND_EXPR << EOF || true
date_trunc('hour', ${TS_REF}) + CASE
  WHEN minute(${TS_REF})*60 + second(${TS_REF}) <= 15*60 THEN INTERVAL '0' minute
  WHEN minute(${TS_REF})*60 + second(${TS_REF}) <= 45*60 THEN INTERVAL '30' minute
  ELSE INTERVAL '60' minute
END
EOF

read -r -d '' QUERY << EOF || true
CREATE OR REPLACE VIEW ${SQL_VIEW_DB}.${SQL_VIEW_NAME} AS
SELECT
  t.*,
  ${ROUND_EXPR} AS ${SQL_NEW_COL}
FROM ${SQL_SRC_DB}.${SQL_SRC_TABLE} t
EOF

printf "%s\n" "$QUERY" > "${SQL_PREFIX}.view.sql"
log "Saved SQL file: ${SQL_PREFIX}.view.sql"

log "Creating/Updating Athena view: ${VIEW_DB}.${VIEW_NAME} (region: ${REGION})"

START_ARGS=(athena start-query-execution --query-string "$QUERY" --query-execution-context "Database=${VIEW_DB}" --region "$REGION" --profile "$PROFILE" --output text --query 'QueryExecutionId')
if [ -n "$RESULTS_S3" ]; then
  START_ARGS+=(--result-configuration "OutputLocation=${RESULTS_S3%/}")
fi

QID=$(run_aws "${START_ARGS[@]}")
wait_for_query "$QID"

log "View created/updated: ${VIEW_DB}.${VIEW_NAME}"

