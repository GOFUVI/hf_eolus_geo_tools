#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# aggregate_core.sh
# -----------------------------------------------------------------------------
# Description:
#   Aggregate selected columns by timestamp and grid node using a precomputed
#   mapping table. Runs the aggregation CTAS in Athena and leaves the raw
#   Parquet files in S3 along with an Athena table optionally partitioned by
#   selected columns. Consolidation and GeoParquet metadata should be handled in
#   a separate step.
#
# Usage:
#   ./aggregate_core.sh \
#     --db-name DB_NAME \
#     --data-table DATA_TABLE \
#     --grid-table GRID_TABLE \
#     --mapping-table MAP_TABLE \
#     --columns COL1,COL2 \
#     --bucket-name BUCKET \
#     --output-prefix PREFIX \
#     --output-table TABLE \
#     [--data-db-name DATA_DB] \
#     [--grid-db-name GRID_DB] \
#     [--mapping-db-name MAP_DB] \
#     [--timestamp-col COL] \
#     [--partition-cols COL1,COL2] \
#     [--profile PROFILE] \
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
DATA_DB_NAME=""
GRID_DB_NAME=""
MAP_DB_NAME=""
DATA_TABLE=""
GRID_TABLE=""
MAP_TABLE=""
COLUMNS=""
BUCKET_NAME=""
OUTPUT_PREFIX=""
OUTPUT_TABLE=""
TIMESTAMP_COL="timestamp"
PARTITION_COLS=""
# Ensure arrays exist even when no options provided
declare -a PART_COLS
declare -a SRC_PART_COLS
declare -a PARTITION_BY

SHORTOPTS=""
LONGOPTS="db-name:,data-db-name:,grid-db-name:,mapping-db-name:,data-table:,grid-table:,mapping-table:,columns:,bucket-name:,output-prefix:,output-table:,timestamp-col:,partition-cols:,profile:,log-dir:,help"
PARSED=$(getopt --options="$SHORTOPTS" --longoptions="$LONGOPTS" --name "$0" -- "$@") || { usage; exit 2; }
eval set -- "$PARSED"
while true; do
  case "$1" in
    --db-name) DB_NAME="$2"; shift 2;;
    --data-db-name) DATA_DB_NAME="$2"; shift 2;;
    --grid-db-name) GRID_DB_NAME="$2"; shift 2;;
    --mapping-db-name) MAP_DB_NAME="$2"; shift 2;;
    --data-table) DATA_TABLE="$2"; shift 2;;
    --grid-table) GRID_TABLE="$2"; shift 2;;
    --mapping-table) MAP_TABLE="$2"; shift 2;;
    --columns) COLUMNS="$2"; shift 2;;
    --bucket-name) BUCKET_NAME="$2"; shift 2;;
    --output-prefix) OUTPUT_PREFIX="$2"; shift 2;;
    --output-table) OUTPUT_TABLE="$2"; shift 2;;
    --timestamp-col) TIMESTAMP_COL="$2"; shift 2;;
    --partition-cols) PARTITION_COLS="$2"; shift 2;;
    --profile) PROFILE="$2"; shift 2;;
    --log-dir) LOG_DIR="$2"; shift 2;;
    --help) usage; exit 0;;
    --) shift; break;;
  esac
done

if [ -z "$DB_NAME" ] || [ -z "$DATA_TABLE" ] || [ -z "$GRID_TABLE" ] || [ -z "$MAP_TABLE" ] || [ -z "$COLUMNS" ] || [ -z "$BUCKET_NAME" ] || [ -z "$OUTPUT_PREFIX" ] || [ -z "$OUTPUT_TABLE" ]; then
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
LOG_FILE="${LOG_DIR}/${SCRIPT_NAME}.log"
rm -f "$LOG_FILE"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"; }

REGION="eu-west-3"

DATA_DB="${DATA_DB_NAME:-$DB_NAME}"
GRID_DB="${GRID_DB_NAME:-$DB_NAME}"
MAP_DB="${MAP_DB_NAME:-$DB_NAME}"

# Validate required source tables exist (data, grid, mapping)
log "Validating input tables exist in Glue Catalog"
if ! run_aws glue get-table --database-name "$DATA_DB" --name "$DATA_TABLE" --profile "$PROFILE" --region "$REGION" >/dev/null; then
  log "Missing data table: ${DATA_DB}.${DATA_TABLE}. Check --data-db-name/--data-table."
  exit 1
fi
if ! run_aws glue get-table --database-name "$GRID_DB" --name "$GRID_TABLE" --profile "$PROFILE" --region "$REGION" >/dev/null; then
  log "Missing grid table: ${GRID_DB}.${GRID_TABLE}. Check --grid-db-name/--grid-table."
  exit 1
fi
if ! run_aws glue get-table --database-name "$MAP_DB" --name "$MAP_TABLE" --profile "$PROFILE" --region "$REGION" >/dev/null; then
  log "Missing mapping table: ${MAP_DB}.${MAP_TABLE}. Check --mapping-db-name/--mapping-table."
  exit 1
fi

# Retrieve partition columns from source data table
log "Retrieving partition columns for ${DATA_DB}.${DATA_TABLE}"
SRC_PART_COLS=($(run_aws glue get-table --database-name "$DATA_DB" --name "$DATA_TABLE" --profile "$PROFILE" --region "$REGION" | jq -r '.Table.PartitionKeys[].Name'))

PART_COLS=()
if [ -n "$PARTITION_COLS" ]; then
  IFS=',' read -r -a PC_INPUT <<< "$PARTITION_COLS"
  for pc in "${PC_INPUT[@]}"; do
    pc_trim=$(echo "$pc" | xargs)
    if [[ " ${SRC_PART_COLS[*]} " != *" ${pc_trim} "* ]]; then
      log "Unknown partition column: ${pc_trim}"
      exit 1
    fi
    PART_COLS+=("$pc_trim")
  done
fi

PARTITION_BY=()
SELECT_PARTS=""
GROUP_PARTS=""
for pc in ${PART_COLS[@]+"${PART_COLS[@]}"}; do
  if [ "$pc" = "$TIMESTAMP_COL" ]; then
    PARTITION_BY+=("'${pc}'")
    continue
  fi
  PARTITION_BY+=("'${pc}'")
  SELECT_PARTS+="d.${pc},"
  GROUP_PARTS+="d.${pc},"
done
SELECT_PARTS=${SELECT_PARTS:-}
GROUP_PARTS=${GROUP_PARTS:-}

# Build join conditions per partition key for CTE joins
JOIN_BA_EXTRA=""
JOIN_AM_EXTRA=""
for pc in ${PART_COLS[@]+"${PART_COLS[@]}"}; do
  [ "$pc" = "$TIMESTAMP_COL" ] && continue
  JOIN_BA_EXTRA+=" AND b.${pc} = a.${pc}"
  JOIN_AM_EXTRA+=" AND a.${pc} = m.${pc}"
done

IFS=',' read -r -a COL_ARRAY <<< "$COLUMNS"

# Build dynamic SQL parts avoiding nested aggregates by using CTEs
BASE_COLS=()
AGG_STATS=()
MAD_STATS=()
SELECT_OUTPUT=()
for col in "${COL_ARRAY[@]}"; do
  col_trim=$(echo "$col" | xargs)
  # Pass through original columns in base
  BASE_COLS+=("d.${col_trim} AS ${col_trim}")
  # Aggregated stats (excluding MAD) computed in agg CTE
  AGG_STATS+=("avg(b.${col_trim}) AS ${col_trim}_mean")
  AGG_STATS+=("approx_percentile(b.${col_trim}, 0.5) AS ${col_trim}_median")
  AGG_STATS+=("stddev(b.${col_trim}) AS ${col_trim}_stddev")
  AGG_STATS+=("min(b.${col_trim}) AS ${col_trim}_min")
  AGG_STATS+=("max(b.${col_trim}) AS ${col_trim}_max")
  AGG_STATS+=("count(b.${col_trim}) AS ${col_trim}_n")
  # MAD computed in separate mad CTE using medians from agg
  MAD_STATS+=("approx_percentile(abs(b.${col_trim} - a.${col_trim}_median), 0.5) AS ${col_trim}_mad")
  # Collect output column names in final SELECT from agg; MADs joined later
  SELECT_OUTPUT+=("a.${col_trim}_mean")
  SELECT_OUTPUT+=("a.${col_trim}_median")
  SELECT_OUTPUT+=("a.${col_trim}_stddev")
  SELECT_OUTPUT+=("a.${col_trim}_min")
  SELECT_OUTPUT+=("a.${col_trim}_max")
  SELECT_OUTPUT+=("a.${col_trim}_n")
  SELECT_OUTPUT+=("m.${col_trim}_mad")
done
BASE_COLS_SQL=$(printf ', %s' "${BASE_COLS[@]}"); BASE_COLS_SQL=${BASE_COLS_SQL:2}
AGG_STATS_SQL=$(printf ', %s' "${AGG_STATS[@]}"); AGG_STATS_SQL=${AGG_STATS_SQL:2}
MAD_STATS_SQL=$(printf ', %s' "${MAD_STATS[@]}"); MAD_STATS_SQL=${MAD_STATS_SQL:2}
SELECT_OUTPUT_SQL=$(printf ', %s' "${SELECT_OUTPUT[@]}"); SELECT_OUTPUT_SQL=${SELECT_OUTPUT_SQL:2}

# Build partition array safely when empty
if (( ${#PARTITION_BY[@]} )); then
  PARTITION_ARRAY=$(printf ', %s' "${PARTITION_BY[@]}")
  PARTITION_ARRAY=${PARTITION_ARRAY:2}
else
  PARTITION_ARRAY=""
fi

SELECT_PARTS=${SELECT_PARTS%,}
GROUP_PARTS=${GROUP_PARTS%,}

KEYS_SELECT="d.${TIMESTAMP_COL} AS ${TIMESTAMP_COL}, m.node_id, g.geometry${SELECT_PARTS:+, ${SELECT_PARTS}}"
KEYS_GROUP="d.${TIMESTAMP_COL}, m.node_id, g.geometry${GROUP_PARTS:+, ${GROUP_PARTS}}"

SELECT_PARTS_B=${SELECT_PARTS//d./b.}
SELECT_PARTS_A=${SELECT_PARTS//d./a.}

# Build partition select lists for final projection
PARTITION_SELECT_A_LIST=()
PARTITION_SELECT_A_NO_TS_LIST=()
TIMESTAMP_IN_PARTITION=false
for pc in ${PART_COLS[@]+"${PART_COLS[@]}"}; do
  PARTITION_SELECT_A_LIST+=("a.${pc}")
  if [ "$pc" = "$TIMESTAMP_COL" ]; then
    TIMESTAMP_IN_PARTITION=true
  else
    PARTITION_SELECT_A_NO_TS_LIST+=("a.${pc}")
  fi
done
if (( ${#PARTITION_SELECT_A_LIST[@]} )); then
  PARTITION_SELECT_A=$(printf ', %s' "${PARTITION_SELECT_A_LIST[@]}")
  PARTITION_SELECT_A=${PARTITION_SELECT_A:2}
else
  PARTITION_SELECT_A=""
fi
if (( ${#PARTITION_SELECT_A_NO_TS_LIST[@]} )); then
  PARTITION_SELECT_A_NO_TS=$(printf ', %s' "${PARTITION_SELECT_A_NO_TS_LIST[@]}")
  PARTITION_SELECT_A_NO_TS=${PARTITION_SELECT_A_NO_TS:2}
else
  PARTITION_SELECT_A_NO_TS=""
fi

if [ "$TIMESTAMP_IN_PARTITION" = true ]; then
  FINAL_SELECT="SELECT a.node_id, a.geometry, a.n${SELECT_OUTPUT_SQL:+, ${SELECT_OUTPUT_SQL}}${PARTITION_SELECT_A:+, ${PARTITION_SELECT_A}}"
else
  FINAL_SELECT="SELECT a.${TIMESTAMP_COL}, a.node_id, a.geometry, a.n${SELECT_OUTPUT_SQL:+, ${SELECT_OUTPUT_SQL}}${PARTITION_SELECT_A_NO_TS:+, ${PARTITION_SELECT_A_NO_TS}}"
fi

QUERY="WITH base AS (
  SELECT ${KEYS_SELECT}${BASE_COLS_SQL:+, ${BASE_COLS_SQL}}
  FROM ${DATA_DB}.${DATA_TABLE} d
  JOIN ${MAP_DB}.${MAP_TABLE} m ON d.rowid = m.rowid
  JOIN ${GRID_DB}.${GRID_TABLE} g ON m.node_id = g.node_id
),
agg AS (
  SELECT ${TIMESTAMP_COL}, node_id, geometry${SELECT_PARTS_B:+, ${SELECT_PARTS_B}}, COUNT(*) AS n${AGG_STATS_SQL:+, ${AGG_STATS_SQL}}
  FROM base b
  GROUP BY ${TIMESTAMP_COL}, node_id, geometry${GROUP_PARTS:+, ${GROUP_PARTS//d./b.}}
),
mad AS (
  SELECT b.${TIMESTAMP_COL}, b.node_id, b.geometry${SELECT_PARTS_B:+, ${SELECT_PARTS_B}}${MAD_STATS_SQL:+, ${MAD_STATS_SQL}}
  FROM base b
  JOIN agg a ON b.${TIMESTAMP_COL} = a.${TIMESTAMP_COL} AND b.node_id = a.node_id AND b.geometry = a.geometry${JOIN_BA_EXTRA}
  GROUP BY b.${TIMESTAMP_COL}, b.node_id, b.geometry${GROUP_PARTS:+, ${GROUP_PARTS//d./b.}}
)
${FINAL_SELECT}
FROM agg a
JOIN mad m ON a.${TIMESTAMP_COL} = m.${TIMESTAMP_COL} AND a.node_id = m.node_id AND a.geometry = m.geometry${JOIN_AM_EXTRA}"

CTAS="CREATE TABLE ${DB_NAME}.${OUTPUT_TABLE} WITH (external_location='s3://${BUCKET_NAME}/${OUTPUT_PREFIX}', format='PARQUET'${PARTITION_ARRAY:+, partitioned_by=ARRAY[${PARTITION_ARRAY}]}) AS ${QUERY}"
 
# Ensure clean target (drop table and clear target S3 prefix) to avoid CTAS partials
log "Dropping existing table if present: ${DB_NAME}.${OUTPUT_TABLE}"
run_aws athena start-query-execution \
  --query-string "DROP TABLE IF EXISTS ${DB_NAME}.${OUTPUT_TABLE}" \
  --query-execution-context "Database=${DB_NAME}" \
  --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${OUTPUT_PREFIX%/}_athena_results" \
  --region $REGION --profile "$PROFILE" >/dev/null || true

## Pre-clean target S3 prefix to ensure a fresh run
S3_PATH="s3://${BUCKET_NAME}/${OUTPUT_PREFIX%/}"
log "Pre-cleaning target S3 prefix: ${S3_PATH}"
# Safety guards: refuse to delete if prefix is empty or root
if [ -z "${OUTPUT_PREFIX}" ] || [ "${OUTPUT_PREFIX}" = "/" ]; then
  log "Refusing to delete empty/root S3 prefix. Check --output-prefix."
  exit 1
fi
# Proceed with recursive delete of target prefix (including any prior query_results)
aws s3 rm "${S3_PATH}" --recursive --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1 || true

log "Running aggregation query"
QID=$(run_aws athena start-query-execution --query-string "$CTAS" --query-execution-context "Database=$DB_NAME" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${OUTPUT_PREFIX%/}_athena_results" --region $REGION --profile "$PROFILE" --output text --query 'QueryExecutionId')
wait_for_query "$QID"

log "Aggregation query completed: table ${DB_NAME}.${OUTPUT_TABLE} at ${S3_PATH}"
