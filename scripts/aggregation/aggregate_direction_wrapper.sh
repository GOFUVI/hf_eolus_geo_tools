#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# aggregate_direction_wrapper.sh
# -----------------------------------------------------------------------------
# Wrapper around aggregate_core.sh adding support for directional columns.
# Directional columns are converted into sine and cosine components before
# aggregation and transformed back into angles after aggregation using circular
# statistics for dispersion (stddev and MAD).
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 [options] --direction-cols COL1,COL2

Options are the same as for aggregate_core.sh. The new option
  --direction-cols  Comma-separated list of directional columns to treat
                    using circular statistics.
USAGE
}

log(){
  if [ -n "${LOG_FILE:-}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >&2
  fi
}

on_err(){
  code=$?
  msg="Wrapper failed (exit=$code). See log: ${LOG_FILE:-<no log file>}"
  echo "$msg" >&2
}
trap on_err ERR

run_aws(){
  echo "Running: aws $*" >> "$LOG_FILE"
  out=$(aws "$@" 2>&1) || { echo "$out" >> "$LOG_FILE"; return 1; }
  echo "$out" >> "$LOG_FILE"
  echo "$out"
}

wait_for_query(){
  local qid="$1"
  while true; do
    out=$(run_aws athena get-query-execution --query-execution-id "$qid" --region "$REGION" --profile "$PROFILE")
    state=$(echo "$out" | jq -r '.QueryExecution.Status.State')
    if [ "$state" = "SUCCEEDED" ]; then
      break
    elif [ "$state" = "FAILED" ] || [ "$state" = "CANCELLED" ]; then
      log "Athena query $qid failed with status $state"
      exit 1
    fi
    sleep 5
  done
}

die() { echo "$*" >&2; usage >&2; exit 2; }

# Defaults
DIRECTION_COLS=""
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
PROFILE="default"
LOG_DIR="logs"

# Simple, portable long-option parser (no GNU getopt dependency)
while [ $# -gt 0 ]; do
  case "$1" in
    --help) usage; exit 0;;
    --direction-cols) [ $# -ge 2 ] || die "--direction-cols requires a value"; DIRECTION_COLS="$2"; shift 2;;
    --db-name) [ $# -ge 2 ] || die "--db-name requires a value"; DB_NAME="$2"; shift 2;;
    --data-db-name) [ $# -ge 2 ] || die "--data-db-name requires a value"; DATA_DB_NAME="$2"; shift 2;;
    --grid-db-name) [ $# -ge 2 ] || die "--grid-db-name requires a value"; GRID_DB_NAME="$2"; shift 2;;
    --mapping-db-name) [ $# -ge 2 ] || die "--mapping-db-name requires a value"; MAP_DB_NAME="$2"; shift 2;;
    --data-table) [ $# -ge 2 ] || die "--data-table requires a value"; DATA_TABLE="$2"; shift 2;;
    --grid-table) [ $# -ge 2 ] || die "--grid-table requires a value"; GRID_TABLE="$2"; shift 2;;
    --mapping-table) [ $# -ge 2 ] || die "--mapping-table requires a value"; MAP_TABLE="$2"; shift 2;;
    --columns) [ $# -ge 2 ] || die "--columns requires a value"; COLUMNS="$2"; shift 2;;
    --bucket-name) [ $# -ge 2 ] || die "--bucket-name requires a value"; BUCKET_NAME="$2"; shift 2;;
    --output-prefix) [ $# -ge 2 ] || die "--output-prefix requires a value"; OUTPUT_PREFIX="$2"; shift 2;;
    --output-table) [ $# -ge 2 ] || die "--output-table requires a value"; OUTPUT_TABLE="$2"; shift 2;;
    --timestamp-col) [ $# -ge 2 ] || die "--timestamp-col requires a value"; TIMESTAMP_COL="$2"; shift 2;;
    --partition-cols) [ $# -ge 2 ] || die "--partition-cols requires a value"; PARTITION_COLS="$2"; shift 2;;
    --profile) [ $# -ge 2 ] || die "--profile requires a value"; PROFILE="$2"; shift 2;;
    --log-dir) [ $# -ge 2 ] || die "--log-dir requires a value"; LOG_DIR="$2"; shift 2;;
    --) shift; break;;
    *) die "Unknown option: $1";;
  esac
done

command -v aws >/dev/null 2>&1 || { echo "aws CLI no encontrado en PATH" >&2; exit 127; }
command -v jq >/dev/null 2>&1 || { echo "jq no encontrado en PATH" >&2; exit 127; }

REGION=$(aws configure get region || true)
if [ -z "${REGION:-}" ]; then
  REGION="eu-west-3"
fi
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/aggregate_direction_wrapper.log"
# Truncate log on each run
: > "$LOG_FILE"
log "Starting aggregate_direction_wrapper"
log "Profile=$PROFILE Region=$REGION LogDir=$LOG_DIR"
log "DB=$DB_NAME DataDB=${DATA_DB_NAME:-$DB_NAME} GridDB=${GRID_DB_NAME:-$DB_NAME} MapDB=${MAP_DB_NAME:-$DB_NAME}"
log "DataTable=$DATA_TABLE GridTable=$GRID_TABLE MapTable=$MAP_TABLE"
log "Columns(original)=$COLUMNS DirCols=$DIRECTION_COLS Timestamp=$TIMESTAMP_COL Partitions=${PARTITION_COLS:-<none>}"
log "Bucket=$BUCKET_NAME OutPrefix=$OUTPUT_PREFIX OutTable=$OUTPUT_TABLE"

ORIG_COLUMNS="$COLUMNS"
ORIG_OUTPUT_PREFIX="$OUTPUT_PREFIX"
ORIG_OUTPUT_TABLE="$OUTPUT_TABLE"

DIR_ARRAY=()
if [ -n "$DIRECTION_COLS" ]; then
  IFS=',' read -r -a DIR_ARRAY <<< "$DIRECTION_COLS"
fi

NON_DIR_COLS=()
if [ -n "$ORIG_COLUMNS" ]; then
  IFS=',' read -r -a ALL_COLS <<< "$ORIG_COLUMNS"
  for col in "${ALL_COLS[@]}"; do
    skip=false
    for d in "${DIR_ARRAY[@]}"; do
      if [ "$col" = "$d" ]; then
        skip=true
        break
      fi
    done
    if [ "$skip" = false ]; then
      NON_DIR_COLS+=("$col")
    fi
  done
fi

TMP_DATA_TABLE="$DATA_TABLE"
TMP_OUTPUT_PREFIX="$OUTPUT_PREFIX"
TMP_OUTPUT_TABLE="$OUTPUT_TABLE"

if [ -n "$DIRECTION_COLS" ]; then
  # Validate minimal required args for pre-CTAS
  if [ -z "$DB_NAME" ] || [ -z "$DATA_TABLE" ] || [ -z "$BUCKET_NAME" ] || [ -z "$OUTPUT_PREFIX" ]; then
    log "Faltan argumentos requeridos para preprocesado direccional (--db-name, --data-table, --bucket-name, --output-prefix)"
    exit 2
  fi

  TMP_DATA_TABLE="${DATA_TABLE}_trig_tmp"
  TMP_DATA_DB="${DATA_DB_NAME:-$DB_NAME}"
  TMP_OUTPUT_PREFIX="${OUTPUT_PREFIX%/}_raw"
  TMP_OUTPUT_TABLE="${OUTPUT_TABLE}_raw"

  SELECT_LIST="*"
  for col in "${DIR_ARRAY[@]}"; do
    SELECT_LIST="${SELECT_LIST}, sin(${col} * pi()/180) AS ${col}_sin, cos(${col} * pi()/180) AS ${col}_cos"
    # remove from columns and add sin/cos
    COLUMNS=${COLUMNS/,${col}/}
    COLUMNS=${COLUMNS/${col},/}
    COLUMNS=${COLUMNS/${col}/}
    if [ -n "$COLUMNS" ]; then
      COLUMNS="${COLUMNS},${col}_sin,${col}_cos"
    else
      COLUMNS="${col}_sin,${col}_cos"
    fi
  done

  TRIG_PREFIX="${OUTPUT_PREFIX%/}_trig_tmp"
  # Ensure clean temp table and S3 prefix to avoid HIVE_PATH_ALREADY_EXISTS
  log "Pre-cleaning temp trig table and prefix"
  run_aws athena start-query-execution \
    --query-string "DROP TABLE IF EXISTS ${TMP_DATA_DB}.${TMP_DATA_TABLE}" \
    --query-execution-context "Database=${TMP_DATA_DB}" \
    --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TRIG_PREFIX}_athena" \
    --region "$REGION" --profile "$PROFILE" >/dev/null || true
  if [ -z "${TRIG_PREFIX}" ] || [ "${TRIG_PREFIX}" = "/" ]; then
    log "Refusing to delete empty/root S3 prefix for trig tmp"
    exit 1
  fi
  aws s3 rm "s3://${BUCKET_NAME}/${TRIG_PREFIX}" --recursive --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1 || true

  PRE_CTAS="CREATE TABLE ${TMP_DATA_DB}.${TMP_DATA_TABLE} WITH (format='PARQUET', external_location='s3://${BUCKET_NAME}/${TRIG_PREFIX}') AS SELECT ${SELECT_LIST} FROM ${DATA_DB_NAME:-$DB_NAME}.${DATA_TABLE}"
  log "Creating temporary table with trig components: ${TMP_DATA_TABLE}"
  QID=$(run_aws athena start-query-execution --query-string "$PRE_CTAS" --query-execution-context "Database=${TMP_DATA_DB}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TRIG_PREFIX}_athena" --region "$REGION" --profile "$PROFILE" --output text --query 'QueryExecutionId')
  wait_for_query "$QID"
fi

log "Running core aggregation"
CORE_ARGS=(
  --db-name "$DB_NAME"
  --data-table "$TMP_DATA_TABLE"
  --grid-table "$GRID_TABLE"
  --mapping-table "$MAP_TABLE"
  --columns "$COLUMNS"
  --bucket-name "$BUCKET_NAME"
  --output-prefix "$TMP_OUTPUT_PREFIX"
  --output-table "$TMP_OUTPUT_TABLE"
  --timestamp-col "$TIMESTAMP_COL"
  --profile "$PROFILE"
  --log-dir "$LOG_DIR"
)
if [ -n "$DIRECTION_COLS" ]; then
  CORE_ARGS+=(--data-db-name "${TMP_DATA_DB}")
elif [ -n "$DATA_DB_NAME" ]; then
  CORE_ARGS+=(--data-db-name "$DATA_DB_NAME")
fi
[[ -n "$GRID_DB_NAME" ]] && CORE_ARGS+=(--grid-db-name "$GRID_DB_NAME")
[[ -n "$MAP_DB_NAME" ]] && CORE_ARGS+=(--mapping-db-name "$MAP_DB_NAME")
[[ -n "$PARTITION_COLS" ]] && CORE_ARGS+=(--partition-cols "$PARTITION_COLS")
./scripts/aggregation/aggregate_core.sh "${CORE_ARGS[@]}"

if [ -n "$DIRECTION_COLS" ]; then
  # Build base projection considering partition columns like core
  TS_IN_PART=false
  if [ -n "$PARTITION_COLS" ]; then
    IFS=',' read -r -a PC_ARR <<< "$PARTITION_COLS"
    for pc in "${PC_ARR[@]}"; do
      if [ "$(echo "$pc" | xargs)" = "$TIMESTAMP_COL" ]; then TS_IN_PART=true; fi
    done
  fi
  if [ "$TS_IN_PART" = true ]; then
    POST_SELECT="node_id, geometry, n"
  else
    POST_SELECT="${TIMESTAMP_COL}, node_id, geometry, n"
  fi
  # Add non-timestamp partition cols to projection to preserve schema
  if [ -n "$PARTITION_COLS" ]; then
    IFS=',' read -r -a PC_ARR2 <<< "$PARTITION_COLS"
    for pc in "${PC_ARR2[@]}"; do
      pc_trim=$(echo "$pc" | xargs)
      [ "$pc_trim" = "$TIMESTAMP_COL" ] && continue
      POST_SELECT="${POST_SELECT}, ${pc_trim}"
    done
  fi
  for col in "${NON_DIR_COLS[@]}"; do
    POST_SELECT="${POST_SELECT}, ${col}_mean, ${col}_median, ${col}_stddev, ${col}_min, ${col}_max, ${col}_n, ${col}_mad"
  done
  for col in "${DIR_ARRAY[@]}"; do
    POST_SELECT="${POST_SELECT}, atan2(${col}_sin_mean, ${col}_cos_mean) * 180 / pi() AS ${col}_mean, atan2(${col}_sin_median, ${col}_cos_median) * 180 / pi() AS ${col}_median, sqrt(-2 * ln(GREATEST(sqrt(${col}_sin_mean*${col}_sin_mean + ${col}_cos_mean*${col}_cos_mean), 1e-9))) AS ${col}_stddev, sqrt(-2 * ln(GREATEST(sqrt(${col}_sin_mad*${col}_sin_mad + ${col}_cos_mad*${col}_cos_mad), 1e-9))) AS ${col}_mad, ${col}_sin_n AS ${col}_n"
  done
  POST_QUERY="SELECT ${POST_SELECT} FROM ${DB_NAME}.${TMP_OUTPUT_TABLE}"
  log "Post-processing directional columns"
  # Drop destination table and pre-clean final S3 prefix to avoid CTAS conflicts
  log "Dropping existing final table if present: ${DB_NAME}.${ORIG_OUTPUT_TABLE}"
  run_aws athena start-query-execution \
    --query-string "DROP TABLE IF EXISTS ${DB_NAME}.${ORIG_OUTPUT_TABLE}" \
    --query-execution-context "Database=${DB_NAME}" \
    --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${ORIG_OUTPUT_PREFIX%/}_athena" \
    --region "$REGION" --profile "$PROFILE" >/dev/null || true

  FINAL_S3_PATH="s3://${BUCKET_NAME}/${ORIG_OUTPUT_PREFIX%/}"
  log "Pre-cleaning final S3 prefix: ${FINAL_S3_PATH}"
  if [ -z "${ORIG_OUTPUT_PREFIX}" ] || [ "${ORIG_OUTPUT_PREFIX}" = "/" ]; then
    log "Refusing to delete empty/root final S3 prefix. Check --output-prefix."
    exit 1
  fi
  aws s3 rm "${FINAL_S3_PATH}" --recursive --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1 || true

  QID=$(run_aws athena start-query-execution --query-string "CREATE TABLE ${DB_NAME}.${ORIG_OUTPUT_TABLE} WITH (format='PARQUET', external_location='s3://${BUCKET_NAME}/${ORIG_OUTPUT_PREFIX}') AS ${POST_QUERY}" --query-execution-context "Database=${DB_NAME}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${ORIG_OUTPUT_PREFIX%/}_athena" --region "$REGION" --profile "$PROFILE" --output text --query 'QueryExecutionId')
  wait_for_query "$QID"

  log "Cleaning intermediate tables"
  run_aws athena start-query-execution --query-string "DROP TABLE IF EXISTS ${DB_NAME}.${TMP_DATA_TABLE}" --query-execution-context "Database=${DB_NAME}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TRIG_PREFIX}_athena" --region "$REGION" --profile "$PROFILE" >/dev/null || true
  run_aws athena start-query-execution --query-string "DROP TABLE IF EXISTS ${DB_NAME}.${TMP_OUTPUT_TABLE}" --query-execution-context "Database=${DB_NAME}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TMP_OUTPUT_PREFIX%/}_athena" --region "$REGION" --profile "$PROFILE" >/dev/null || true
  aws s3 rm "s3://${BUCKET_NAME}/${TRIG_PREFIX}" --recursive --profile "$PROFILE" --region "$REGION" >/dev/null 2>&1 || true
  aws s3 rm "s3://${BUCKET_NAME}/${TMP_OUTPUT_PREFIX}" --recursive --profile "$PROFILE" --region "$REGION" >/dev/null 2>&1 || true
fi

log "Done"
