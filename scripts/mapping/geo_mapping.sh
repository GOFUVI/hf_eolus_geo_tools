#!/bin/bash
# -----------------------------------------------------------------------------
# geo_mapping.sh
# -----------------------------------------------------------------------------
# Description:
#   Create an Athena table linking data rows to grid nodes when their
#   geometries are within a user-defined distance. The result table is stored
#   as Parquet in S3.
#
# Usage:
#   ./geo_mapping.sh \
#     --db-name DB_NAME \
#     --data-table DATA_TABLE \
#     --grid-table GRID_TABLE \
#     --bucket-name BUCKET_NAME \
#     --output-prefix OUTPUT_PREFIX \
#     --output-table OUTPUT_TABLE \
#     [--distance-km KM] \
#     [--profile PROFILE] \
#     [--log-dir LOG_DIR] \
#     [--help]
#
# Options:
#   --db-name DB_NAME         Athena database containing the tables.
#   --data-table DATA_TABLE   Source table with `rowid` and `geometry` columns.
#   --grid-table GRID_TABLE   Grid table with `node_id` and `geometry` columns.
#   --bucket-name BUCKET      S3 bucket for the output Parquet table.
#   --output-prefix PREFIX    S3 prefix for Parquet files.
#   --output-table TABLE      Name of the destination table to create.
#   --distance-km KM          Search radius in kilometers (default: 10). Alias: --distance.
#   --profile PROFILE         AWS CLI profile (default: default).
#   --log-dir LOG_DIR         Directory for logs (default: current directory).
#   --help                    Display this help and exit.
#
# Requirements:
#   - bash shell
#   - AWS CLI
#   - jq
# -----------------------------------------------------------------------------

usage() {
    cat <<EOF
Usage: $0 --db-name DB_NAME --data-table DATA_TABLE --grid-table GRID_TABLE \\
           --bucket-name BUCKET_NAME --output-prefix OUTPUT_PREFIX \\
           --output-table OUTPUT_TABLE [--distance-km KM] [--profile PROFILE] \\
           [--log-dir LOG_DIR] [--help]

Options:
  --db-name DB_NAME         Athena database containing the tables.
  --data-table DATA_TABLE   Source table with rowid and geometry.
  --grid-table GRID_TABLE   Grid table with node_id and geometry.
  --bucket-name BUCKET      S3 bucket for the output Parquet table.
  --output-prefix PREFIX    S3 prefix for Parquet files.
  --output-table TABLE      Destination table name to create.
  --distance-km KM          Search radius in kilometers (default: 10). Alias: --distance.
  --profile PROFILE         AWS CLI profile (default: default).
  --log-dir LOG_DIR         Directory for logs (default: current directory).
  --help                    Display this help and exit.
EOF
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
    qid="$1"
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

ORIG_PWD="$(pwd)"
PROFILE="default"
DB_NAME=""
DATA_TABLE=""
GRID_TABLE=""
BUCKET_NAME=""
OUTPUT_PREFIX=""
OUTPUT_TABLE=""
DISTANCE_KM="10"

SHORTOPTS=""
LONGOPTS="db-name:,data-table:,grid-table:,bucket-name:,output-prefix:,output-table:,distance-km:,distance:,profile:,log-dir:,help"
PARSED_OPTS=$(getopt --options="$SHORTOPTS" --longoptions="$LONGOPTS" --name "$0" -- "$@") || { usage; exit 2; }
eval set -- "$PARSED_OPTS"
while true; do
    case "$1" in
        --db-name)
            DB_NAME="$2"; shift 2;;
        --data-table)
            DATA_TABLE="$2"; shift 2;;
        --grid-table)
            GRID_TABLE="$2"; shift 2;;
        --bucket-name)
            BUCKET_NAME="$2"; shift 2;;
        --output-prefix)
            OUTPUT_PREFIX="$2"; shift 2;;
        --output-table)
            OUTPUT_TABLE="$2"; shift 2;;
        --distance-km|--distance)
            DISTANCE_KM="$2"; shift 2;;
        --profile)
            PROFILE="$2"; shift 2;;
        --log-dir)
            LOG_DIR="$2"; shift 2;;
        --help)
            usage; exit 0;;
        --)
            shift; break;;
    esac
done

if [ -z "$DB_NAME" ] || [ -z "$DATA_TABLE" ] || [ -z "$GRID_TABLE" ] || [ -z "$BUCKET_NAME" ] || [ -z "$OUTPUT_PREFIX" ] || [ -z "$OUTPUT_TABLE" ]; then
    echo "Error: missing required arguments" >&2
    usage
    exit 1
fi

if [ -n "$LOG_DIR" ]; then
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
log "Using database: $DB_NAME"

log "Dropping existing table if present"
output=$(run_aws athena start-query-execution \
    --region $REGION \
    --profile "$PROFILE" \
    --query-execution-context Database="$DB_NAME" \
    --result-configuration "OutputLocation=s3://$BUCKET_NAME/athena/query-results/" \
    --query-string "DROP TABLE IF EXISTS $DB_NAME.$OUTPUT_TABLE" )
qid=$(echo "$output" | jq -r '.QueryExecutionId')
wait_for_query "$qid"

log "Removing any existing data at s3://$BUCKET_NAME/$OUTPUT_PREFIX/"
run_aws s3 rm "s3://$BUCKET_NAME/$OUTPUT_PREFIX/" --recursive --profile "$PROFILE" || \
    log "No existing data to remove"

sql=$(cat <<EOF
CREATE TABLE $DB_NAME.$OUTPUT_TABLE WITH (
    external_location = 's3://$BUCKET_NAME/$OUTPUT_PREFIX/',
    format = 'PARQUET'
) AS
WITH params AS (
    SELECT $DISTANCE_KM AS r_km
),
data_pts AS (
    SELECT rowid,
           ST_Y(ST_GeomFromBinary(geometry)) AS lat,
           ST_X(ST_GeomFromBinary(geometry)) AS lon,
           geometry
    FROM $DB_NAME.$DATA_TABLE
),
grid_pts AS (
    SELECT node_id,
           ST_Y(ST_GeomFromBinary(geometry)) AS lat,
           ST_X(ST_GeomFromBinary(geometry)) AS lon,
           geometry
    FROM $DB_NAME.$GRID_TABLE
),
deltas AS (
    SELECT d.rowid, d.lat, d.lon, d.geometry AS d_geom, p.r_km,
           p.r_km / 110.574 AS delta_lat,
           p.r_km / (111.320 * COS(RADIANS(d.lat))) AS delta_lon
    FROM data_pts d CROSS JOIN params p
),
candidates AS (
    SELECT de.rowid, g.node_id, de.d_geom, g.geometry AS g_geom, de.r_km
    FROM deltas de
    JOIN grid_pts g
      ON g.lat BETWEEN de.lat - de.delta_lat AND de.lat + de.delta_lat
     AND g.lon BETWEEN de.lon - de.delta_lon AND de.lon + de.delta_lon
)
SELECT rowid, node_id
FROM candidates c
WHERE ST_Distance(
        to_spherical_geography(ST_GeomFromBinary(c.d_geom)),
        to_spherical_geography(ST_GeomFromBinary(c.g_geom))
      ) <= c.r_km * 1000
EOF
)

output=$(run_aws athena start-query-execution \
    --region $REGION \
    --profile "$PROFILE" \
    --query-execution-context Database="$DB_NAME" \
    --result-configuration "OutputLocation=s3://$BUCKET_NAME/athena/query-results/" \
    --query-string "$sql" )
qid=$(echo "$output" | jq -r '.QueryExecutionId')
wait_for_query "$qid"

log "Query completed. Checking first 10 rows from $OUTPUT_TABLE"
output=$(run_aws athena start-query-execution \
    --region $REGION \
    --profile "$PROFILE" \
    --query-execution-context Database="$DB_NAME" \
    --result-configuration "OutputLocation=s3://$BUCKET_NAME/athena/query-results/" \
    --query-string "SELECT * FROM $DB_NAME.$OUTPUT_TABLE LIMIT 10" )
qid=$(echo "$output" | jq -r '.QueryExecutionId')
wait_for_query "$qid"

log "geo_mapping completed successfully."
