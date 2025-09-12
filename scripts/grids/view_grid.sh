#!/usr/bin/env bash
#
# view_grid.sh
#
# Description:
#   Launch a Dockerized Python script to render an interactive HTML map of
#   grid nodes stored in a GeoParquet file (as produced by create_grid_table.sh).
#   The map auto-zooms to the dataset extent.
#
# Usage:
#   $0 --input PATH.parquet [--output grid_map.html] [--tiles "OpenStreetMap"] \
#      [--title "My Grid"] [--no-cluster] [--marker-size 3] [--sample 0]

set -euo pipefail

usage() {
  sed -n '7,999p' "$0"
}

INPUTS=()
OUTPUT="grid_map.html"
TILES="OpenStreetMap"
TITLE=""
CLUSTER=1
MARKER_SIZE=3
SAMPLE=0

LONGOPTS=input:,output:,tiles:,title:,no-cluster,marker-size:,sample:,help
PARSED_OPTS=$(getopt --options="" --longoptions="$LONGOPTS" --name "$0" -- "$@")
if [ $? -ne 0 ]; then
  usage
  exit 2
fi

eval set -- "$PARSED_OPTS"
while true; do
  case "$1" in
    --input) INPUTS+=("$2"); shift 2;;
    --output) OUTPUT="$2"; shift 2;;
    --tiles) TILES="$2"; shift 2;;
    --title) TITLE="$2"; shift 2;;
    --no-cluster) CLUSTER=0; shift 1;;
    --marker-size) MARKER_SIZE="$2"; shift 2;;
    --sample) SAMPLE="$2"; shift 2;;
    --help) usage; exit 0;;
    --) shift; break;;
    *) echo "Unexpected option: $1"; usage; exit 3;;
  esac
done

if [ ${#INPUTS[@]} -eq 0 ]; then
  echo "[ERROR] --input PATH.parquet is required (repeatable)" >&2
  usage
  exit 1
fi
for f in "${INPUTS[@]}"; do
  if [ ! -f "$f" ]; then
    echo "[ERROR] Input file not found: $f" >&2
    exit 1
  fi
done

echo "[INFO] Rendering grid map from: ${INPUTS[*]}" >&2
echo "[INFO] Output HTML: $OUTPUT" >&2
echo "[INFO] Tiles: $TILES" >&2
echo "[INFO] Clustering: $([ "$CLUSTER" -eq 1 ] && echo on || echo off)" >&2
echo "[INFO] Marker size: $MARKER_SIZE (when not clustering)" >&2
if [ "$SAMPLE" -gt 0 ]; then
  echo "[INFO] Sampling first $SAMPLE points" >&2
fi
if [ -n "$TITLE" ]; then
  echo "[INFO] Title: $TITLE" >&2
fi

INNER_CMD="pip install --no-cache-dir shapely folium pyarrow >/tmp/pip.log && python scripts/grids/visualize_grid.py"

# Append all inputs
for f in "${INPUTS[@]}"; do
  INNER_CMD+=" --input \"$f\""
done

INNER_CMD+=" --output \"$OUTPUT\" --tiles \"$TILES\""

if [ "$CLUSTER" -eq 0 ]; then
  INNER_CMD+=" --no-cluster --marker-size $MARKER_SIZE"
fi
if [ "$SAMPLE" -gt 0 ]; then
  INNER_CMD+=" --sample $SAMPLE"
fi
if [ -n "$TITLE" ]; then
  INNER_CMD+=" --title \"$TITLE\""
fi

docker run --rm -v "${PWD}":/work -w /work python:3.11-slim bash -lc "$INNER_CMD"

echo "[OK] Map saved to $OUTPUT" >&2
