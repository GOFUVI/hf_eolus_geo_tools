#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# aggregate_projection_wrapper.sh
# -----------------------------------------------------------------------------
# Wrapper around aggregate_core.sh adding a preprocessing step to project the
# values of a user-selected column onto the line that joins each grid node with
# a user-specified geographic point. The projected values replace the original
# column before aggregation. No post-processing is performed.
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 [options] --projection-col COL --point-lat LAT --point-lon LON

Options are the same as for aggregate_core.sh. The new options are
  --projection-col  Name of the column whose values will be projected.
  --point-lat       Latitude of the reference point in decimal degrees.
  --point-lon       Longitude of the reference point in decimal degrees.
USAGE
}

log(){
  if [ -n "${LOG_FILE:-}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >&2
  fi
}

save_sql(){
  # save_sql "name" "SQL STRING"
  local name="$1"
  local sql="$2"
  SQL_IDX=$((SQL_IDX+1))
  local idx
  idx=$(printf "%02d" "$SQL_IDX")
  local file="${SQL_DIR}/${SCRIPT_TAG}_${idx}_${name}.sql"
  printf "%s\n" "$sql" > "$file"
  echo "Saved SQL -> $file" >> "$LOG_FILE"
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

die(){ echo "$*" >&2; usage >&2; exit 2; }

# Defaults
PROJECTION_COL=""
POINT_LAT=""
POINT_LON=""
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

# Option parsing
while [ $# -gt 0 ]; do
  case "$1" in
    --help) usage; exit 0;;
    --projection-col) [ $# -ge 2 ] || die "--projection-col requires a value"; PROJECTION_COL="$2"; shift 2;;
    --point-lat) [ $# -ge 2 ] || die "--point-lat requires a value"; POINT_LAT="$2"; shift 2;;
    --point-lon) [ $# -ge 2 ] || die "--point-lon requires a value"; POINT_LON="$2"; shift 2;;
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

command -v aws >/dev/null 2>&1 || { echo "aws CLI not found" >&2; exit 127; }
command -v jq >/dev/null 2>&1 || { echo "jq not found" >&2; exit 127; }

REGION=$(aws configure get region || true)
if [ -z "${REGION:-}" ]; then
  REGION="eu-west-3"
fi
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/aggregate_projection_wrapper_${OUTPUT_TABLE}.log"
: > "$LOG_FILE"

SQL_IDX=0
SCRIPT_TAG="aggregate_projection_wrapper_${OUTPUT_TABLE}"
# Persist SQLs directly under LOG_DIR with a namespaced prefix to avoid collisions
SQL_DIR="$LOG_DIR"

log "Starting aggregate_projection_wrapper"
log "Profile=$PROFILE Region=$REGION LogDir=$LOG_DIR"
log "SQL files directory: $SQL_DIR (prefix: ${SCRIPT_TAG}_*)"
log "DB=$DB_NAME DataDB=${DATA_DB_NAME:-$DB_NAME} GridDB=${GRID_DB_NAME:-$DB_NAME} MapDB=${MAP_DB_NAME:-$DB_NAME}"
log "DataTable=$DATA_TABLE GridTable=$GRID_TABLE MapTable=$MAP_TABLE"
log "Columns=$COLUMNS ProjectionCol=$PROJECTION_COL Timestamp=$TIMESTAMP_COL Partitions=${PARTITION_COLS:-<none>}"
log "Point=($POINT_LAT,$POINT_LON) Bucket=$BUCKET_NAME OutPrefix=$OUTPUT_PREFIX OutTable=$OUTPUT_TABLE"

if [ -z "$PROJECTION_COL" ] || [ -z "$POINT_LAT" ] || [ -z "$POINT_LON" ]; then
  die "--projection-col, --point-lat and --point-lon are required"
fi

# Temporary table names and prefixes
TMP_DATA_TABLE="${DATA_TABLE}_proj_tmp"
TMP_MAP_TABLE="${MAP_TABLE}_proj_tmp"
TMP_DATA_PREFIX="${OUTPUT_PREFIX%/}_proj_data_tmp"
TMP_MAP_PREFIX="${OUTPUT_PREFIX%/}_proj_map_tmp"

DATA_DB=${DATA_DB_NAME:-$DB_NAME}
GRID_DB=${GRID_DB_NAME:-$DB_NAME}
MAP_DB=${MAP_DB_NAME:-$DB_NAME}

# Pre-clean temp tables and prefixes
log "Cleaning temporary tables and prefixes"
SQL_DROP_TMP_DATA="DROP TABLE IF EXISTS ${DB_NAME}.${TMP_DATA_TABLE}"
SQL_DROP_TMP_MAP="DROP TABLE IF EXISTS ${DB_NAME}.${TMP_MAP_TABLE}"
save_sql "01_drop_tmp_data_table" "$SQL_DROP_TMP_DATA"
save_sql "02_drop_tmp_map_table" "$SQL_DROP_TMP_MAP"
run_aws athena start-query-execution \
  --query-string "$SQL_DROP_TMP_DATA" \
  --query-execution-context "Database=${DB_NAME}" \
  --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TMP_DATA_PREFIX}_athena" \
  --region "$REGION" --profile "$PROFILE" >/dev/null || true
run_aws athena start-query-execution \
  --query-string "$SQL_DROP_TMP_MAP" \
  --query-execution-context "Database=${DB_NAME}" \
  --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TMP_MAP_PREFIX}_athena" \
  --region "$REGION" --profile "$PROFILE" >/dev/null || true
aws s3 rm "s3://${BUCKET_NAME}/${TMP_DATA_PREFIX}" --recursive --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1 || true
aws s3 rm "s3://${BUCKET_NAME}/${TMP_MAP_PREFIX}" --recursive --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1 || true

# Build column lists
IFS=',' read -r -a COL_ARR <<< "$COLUMNS"
IFS=',' read -r -a PART_ARR <<< "$PARTITION_COLS"

PART_SELECT=""
PART_LIST=""
# NOTE: Avoid bash 4 associative arrays for macOS compatibility.
# Build a space-delimited list of unique partition columns preserving order.
UNIQUE_PARTS=""
for pc in "${PART_ARR[@]}"; do
  pc_trim=$(echo "$pc" | xargs)
  [ -z "$pc_trim" ] && continue
  case " $UNIQUE_PARTS " in
    *" $pc_trim "*) ;; # already seen
    *) UNIQUE_PARTS="$UNIQUE_PARTS $pc_trim" ;;
  esac
done
# Now append to SELECT/LIST once per unique partition column
for pc_trim in $UNIQUE_PARTS; do
  PART_SELECT+=" , d.${pc_trim} AS ${pc_trim}"
  PART_LIST+=" , ${pc_trim}"
done

DATA_SELECT=""
for col in "${COL_ARR[@]}"; do
  col_trim=$(echo "$col" | xargs)
  [ -z "$col_trim" ] && continue
  DATA_SELECT+=" , d.${col_trim} AS ${col_trim}"

done

BASE_QUERY=$(cat <<SQL
SELECT concat(cast(d.rowid AS varchar), '_', cast(m.node_id AS varchar)) AS rowid,
       d.${TIMESTAMP_COL} AS ${TIMESTAMP_COL}${PART_SELECT}${DATA_SELECT},
       m.node_id,
       d.geometry AS d_geom,
       g.geometry AS g_geom
FROM ${DATA_DB}.${DATA_TABLE} d
JOIN ${MAP_DB}.${MAP_TABLE} m ON d.rowid = m.rowid
JOIN ${GRID_DB}.${GRID_TABLE} g ON m.node_id = g.node_id
SQL
)

save_sql "03_base_query" "$BASE_QUERY"

SELECT_WITH_PROJ=""
for col in "${COL_ARR[@]}"; do
  col_trim=$(echo "$col" | xargs)
  [ -z "$col_trim" ] && continue
  if [ "$col_trim" = "$PROJECTION_COL" ]; then
    # Project value onto the line from grid node to given point P(lat,lon)
    # cos(theta) = ((P-D)·(P-G)) / (|P-D|*|P-G|)
    # projected = value * cos(theta)
    SELECT_WITH_PROJ+=", ( ${col_trim} * (((${POINT_LON} - ST_X(ST_GeomFromBinary(d_geom))) * (${POINT_LON} - ST_X(ST_GeomFromBinary(g_geom))) + (${POINT_LAT} - ST_Y(ST_GeomFromBinary(d_geom))) * (${POINT_LAT} - ST_Y(ST_GeomFromBinary(g_geom)))) / (sqrt(power(${POINT_LON} - ST_X(ST_GeomFromBinary(d_geom)), 2) + power(${POINT_LAT} - ST_Y(ST_GeomFromBinary(d_geom)), 2)) * sqrt(power(${POINT_LON} - ST_X(ST_GeomFromBinary(g_geom)), 2) + power(${POINT_LAT} - ST_Y(ST_GeomFromBinary(g_geom)), 2)))) ) AS ${col_trim}"
  else
    SELECT_WITH_PROJ+=" , ${col_trim}"
  fi
done

DATA_CTAS=$(cat <<SQL
CREATE TABLE ${DB_NAME}.${TMP_DATA_TABLE} WITH (
  format='PARQUET',
  external_location='s3://${BUCKET_NAME}/${TMP_DATA_PREFIX}'$(
    # Add partitioning if requested
    if [ -n "${UNIQUE_PARTS// /}" ]; then 
      printf ", partitioned_by = ARRAY["
      first=true
      for pc in $UNIQUE_PARTS; do
        if $first; then first=false; else printf ", "; fi
        printf "'%s'" "$pc"
      done
      printf "]"
    fi
  )
) AS
-- Partition columns must be last in SELECT for CTAS
SELECT rowid, ${TIMESTAMP_COL}${SELECT_WITH_PROJ}${PART_LIST}
FROM ( ${BASE_QUERY} )
SQL
)

MAP_CTAS=$(cat <<SQL
CREATE TABLE ${DB_NAME}.${TMP_MAP_TABLE} WITH (format='PARQUET', external_location='s3://${BUCKET_NAME}/${TMP_MAP_PREFIX}') AS
SELECT rowid, node_id FROM ( ${BASE_QUERY} )
SQL
)

save_sql "04_create_tmp_projected_data" "$DATA_CTAS"
save_sql "05_create_tmp_mapping" "$MAP_CTAS"

log "Creating temporary projected data table"
QID=$(run_aws athena start-query-execution --query-string "$DATA_CTAS" --query-execution-context "Database=${DB_NAME}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TMP_DATA_PREFIX}_athena" --region "$REGION" --profile "$PROFILE" --output text --query 'QueryExecutionId')
wait_for_query "$QID"

log "Creating temporary mapping table"
QID=$(run_aws athena start-query-execution --query-string "$MAP_CTAS" --query-execution-context "Database=${DB_NAME}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TMP_MAP_PREFIX}_athena" --region "$REGION" --profile "$PROFILE" --output text --query 'QueryExecutionId')
wait_for_query "$QID"

log "Running core aggregation"
CORE_ARGS=(
  --db-name "$DB_NAME"
  --data-table "$TMP_DATA_TABLE"
  --grid-table "$GRID_TABLE"
  --mapping-table "$TMP_MAP_TABLE"
  --columns "$COLUMNS"
  --bucket-name "$BUCKET_NAME"
  --output-prefix "$OUTPUT_PREFIX"
  --output-table "$OUTPUT_TABLE"
  --timestamp-col "$TIMESTAMP_COL"
  --profile "$PROFILE"
  --log-dir "$LOG_DIR"
)
[[ -n "$GRID_DB_NAME" ]] && CORE_ARGS+=(--grid-db-name "$GRID_DB_NAME")
[[ -n "$PARTITION_COLS" ]] && CORE_ARGS+=(--partition-cols "$PARTITION_COLS")
./scripts/aggregation/aggregate_core.sh "${CORE_ARGS[@]}"

log "Cleaning intermediate resources"
save_sql "99_drop_tmp_data_table_end" "$SQL_DROP_TMP_DATA"
save_sql "99_drop_tmp_map_table_end" "$SQL_DROP_TMP_MAP"
run_aws athena start-query-execution --query-string "$SQL_DROP_TMP_DATA" --query-execution-context "Database=${DB_NAME}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TMP_DATA_PREFIX}_athena" --region "$REGION" --profile "$PROFILE" >/dev/null || true
run_aws athena start-query-execution --query-string "$SQL_DROP_TMP_MAP" --query-execution-context "Database=${DB_NAME}" --result-configuration "OutputLocation=s3://${BUCKET_NAME}/${TMP_MAP_PREFIX}_athena" --region "$REGION" --profile "$PROFILE" >/dev/null || true
aws s3 rm "s3://${BUCKET_NAME}/${TMP_DATA_PREFIX}" --recursive --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1 || true
aws s3 rm "s3://${BUCKET_NAME}/${TMP_MAP_PREFIX}" --recursive --profile "$PROFILE" --region "$REGION" >> "$LOG_FILE" 2>&1 || true

log "Done"
