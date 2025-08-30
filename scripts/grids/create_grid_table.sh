#!/usr/bin/env bash
#
# create_grid_table.sh
#
# Description:
#   Generate a regular grid from one or more Athena tables containing
#   WKB point geometries and create a GeoParquet-backed Athena table
#   with those grid nodes. The grid spacing can be configured and an
#   optional CSV of manual nodes may be appended.
#
# Usage:
#   $0 --profile PROFILE --database DATABASE --output-table TABLE --table-location S3_PATH --output-location S3_QUERY_OUTPUT \\
#      [--input-tables TABLE1[,TABLE2,...] --node-prefix PREFIX] \\
#      [--grid-spacing-km KM] [--region REGION] \\
#      [--mode overwrite|append] [--bounds-mode union|intersection] \\
#      [--manual-csv PATH] [--help]
#
# Required:
#   --profile PROFILE          AWS CLI profile.
#   --database DATABASE        Athena database name.
#   --output-table TABLE       Name of Athena table to create.
#   --table-location S3_PATH   S3 location for GeoParquet dataset.
#   --output-location S3_PATH  S3 location for Athena query results.
#   --input-tables LIST        Comma-separated list of source tables (required with --node-prefix).
#   --node-prefix PREFIX       Prefix for grid node identifiers (required with --input-tables).
#   --manual-csv PATH          CSV with node_id,longitude,latitude when no --input-tables are given.
# Optional:
#   --grid-spacing-km KM       Grid node spacing in kilometers (default: 10).
#   --region REGION            AWS region; if omitted, retrieved from the AWS CLI profile.
#   --mode MODE                overwrite or append (default: overwrite).
#   --bounds-mode MODE         union or intersection of input table areas (default: union).
#   --help                     Show this help message and exit.

set -euo pipefail

usage() {
  sed -n '7,40p' "$0"
}

# Default parameter values
GRID_SPACING_KM=10
REGION=""
OUTPUT_LOCATION=""
MODE="overwrite"
BOUNDS_MODE="union"
MANUAL_CSV=""
PROFILE=""
DATABASE=""
INPUT_TABLES=""
OUTPUT_TABLE=""
TABLE_LOCATION=""
NODE_PREFIX=""

# Parse command-line options
SHORTOPTS=
LONGOPTS=profile:,database:,input-tables:,output-table:,table-location:,node-prefix:,grid-spacing-km:,region:,output-location:,mode:,bounds-mode:,manual-csv:,help
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
    --input-tables) INPUT_TABLES="$2"; shift 2;;
    --output-table) OUTPUT_TABLE="$2"; shift 2;;
    --table-location) TABLE_LOCATION="$2"; shift 2;;
    --node-prefix) NODE_PREFIX="$2"; shift 2;;
    --grid-spacing-km) GRID_SPACING_KM="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --output-location) OUTPUT_LOCATION="$2"; shift 2;;
    --mode) MODE="$2"; shift 2;;
    --bounds-mode) BOUNDS_MODE="$2"; shift 2;;
    --manual-csv) MANUAL_CSV="$2"; shift 2;;
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

if [ -n "$INPUT_TABLES" ]; then
  if [ -z "$NODE_PREFIX" ]; then
    echo "--node-prefix required when --input-tables is provided" >&2
    exit 1
  fi
else
  if [ -z "$MANUAL_CSV" ]; then
    echo "Either --input-tables with --node-prefix or --manual-csv must be provided" >&2
    usage
    exit 1
  fi
fi

if [[ "$BOUNDS_MODE" != "union" && "$BOUNDS_MODE" != "intersection" ]]; then
  echo "--bounds-mode must be 'union' or 'intersection'" >&2
  exit 1
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

if [ -n "$INPUT_TABLES" ]; then
  # Build bounds query from input tables
  IFS=',' read -r -a TABLE_ARRAY <<< "$INPUT_TABLES"
  if [[ "$BOUNDS_MODE" == "union" ]]; then
    SUB_QUERIES=()
    for t in "${TABLE_ARRAY[@]}"; do
      SUB_QUERIES+=("SELECT ST_GeomFromBinary(geometry) AS geom FROM ${t}")
    done
    UNION_QUERY=$(printf "%s UNION ALL " "${SUB_QUERIES[@]}")
    UNION_QUERY=${UNION_QUERY% UNION ALL }
    BOUNDS_QUERY="SELECT MIN(ST_X(geom)) AS min_lon, MIN(ST_Y(geom)) AS min_lat, MAX(ST_X(geom)) AS max_lon, MAX(ST_Y(geom)) AS max_lat FROM (${UNION_QUERY}) as all_geom"
  else
    SUB_QUERIES=()
    for t in "${TABLE_ARRAY[@]}"; do
      SUB_QUERIES+=("SELECT MIN(ST_X(geom)) AS min_lon, MIN(ST_Y(geom)) AS min_lat, MAX(ST_X(geom)) AS max_lon, MAX(ST_Y(geom)) AS max_lat FROM (SELECT ST_GeomFromBinary(geometry) AS geom FROM ${t}) as g")
    done
    UNION_QUERY=$(printf "%s UNION ALL " "${SUB_QUERIES[@]}")
    UNION_QUERY=${UNION_QUERY% UNION ALL }
    BOUNDS_QUERY="SELECT MAX(min_lon) AS min_lon, MAX(min_lat) AS min_lat, MIN(max_lon) AS max_lon, MIN(max_lat) AS max_lat FROM (${UNION_QUERY}) as bounds"
  fi

  echo "[ATHENA] Running bounds query" >&2
  BOUNDS_QID=$(aws athena start-query-execution \
    --region "$REGION" \
    --profile "$PROFILE" \
    --query-execution-context "Database=$DATABASE" \
    --result-configuration "OutputLocation=$OUTPUT_LOCATION" \
    --query-string "$BOUNDS_QUERY" \
    --output text \
    --query "QueryExecutionId")
  wait_for_query "$BOUNDS_QID"

  RESULT_JSON=$(aws athena get-query-results \
    --region "$REGION" \
    --profile "$PROFILE" \
    --query-execution-id "$BOUNDS_QID")
  MIN_LON=$(echo "$RESULT_JSON" | jq -r '.ResultSet.Rows[1].Data[0].VarCharValue')
  MIN_LAT=$(echo "$RESULT_JSON" | jq -r '.ResultSet.Rows[1].Data[1].VarCharValue')
  MAX_LON=$(echo "$RESULT_JSON" | jq -r '.ResultSet.Rows[1].Data[2].VarCharValue')
  MAX_LAT=$(echo "$RESULT_JSON" | jq -r '.ResultSet.Rows[1].Data[3].VarCharValue')

  if [[ "$BOUNDS_MODE" == "intersection" ]]; then
    if ! awk -v min_lon="$MIN_LON" -v min_lat="$MIN_LAT" -v max_lon="$MAX_LON" -v max_lat="$MAX_LAT" 'BEGIN{exit(min_lon < max_lon && min_lat < max_lat ? 0 : 1)}'; then
      echo "[GRID] No overlapping area across input tables" >&2
      exit 1
    fi
  fi

  echo "[GRID] Bounding box: $MIN_LON,$MIN_LAT to $MAX_LON,$MAX_LAT" >&2

  PY_ARGS="--min-lon $MIN_LON --min-lat $MIN_LAT --max-lon $MAX_LON --max-lat $MAX_LAT --spacing-km $GRID_SPACING_KM --prefix $NODE_PREFIX --output $LOCAL_PARQUET"
  if [[ -n "$MANUAL_CSV" ]]; then
    PY_ARGS+=" --manual-csv $MANUAL_CSV"
  fi
else
  PY_ARGS="--output $LOCAL_PARQUET --manual-csv $MANUAL_CSV"
fi

docker run --rm -v "${PWD}":/work -w /work python:3.11-slim bash -lc \
  "pip install --no-cache-dir shapely pyproj pandas pyarrow >/tmp/pip.log && python scripts/geo_join/create_grid_table.py $PY_ARGS"

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
