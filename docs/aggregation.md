# Aggregation Scripts

## Overview

The `scripts/aggregation` directory contains tools to aggregate time‑series data by grid node, finalize the resulting Parquet dataset as GeoParquet, and optionally build a portable STAC catalog. The typical flow is:

1. Run an aggregation (core or via a wrapper) to create an Athena table backed by Parquet files in S3.
2. Finalize the dataset by merging files per partition and writing GeoParquet metadata.
3. Optionally package the dataset as a self‑contained STAC catalog.

Key components:

- `aggregate_core.sh`: Runs the core Athena CTAS query to compute stats per timestamp and `node_id` using a precomputed mapping from rows to grid nodes.
- `aggregate_direction_wrapper.sh`: Adds circular statistics for directional columns by aggregating sine/cosine components and projecting results back to angles.
- `aggregate_projection_wrapper.sh`: Pre‑projects one selected column onto the line from each grid node to a fixed point (lat/lon) before aggregation.
- `finalize_geoparquet.sh`: Consolidates Parquet files and writes GeoParquet metadata; can also repair Glue partitions.
- Utilities: `merge_parquet.py`, `add_geoparquet_metadata.py`, `build_geo_catalog.py`, `build_stac_catalog.sh`.

## Requirements

- Bash with standard Unix utilities
- AWS CLI configured with credentials; region defaults to profile or `eu-west-3`
- jq for CLI JSON parsing
- Docker (used by finalization and STAC catalog build steps)
- Python packages (installed inside containers as needed): `pyarrow`, `shapely`, `pystac`

## `aggregate_core.sh`

Aggregates selected numeric columns by timestamp and grid `node_id`, joining:

- data table: provides `rowid`, `<columns>`, optional partition columns, and timestamp column
- mapping table: provides `rowid -> node_id`
- grid table: provides `node_id` and `geometry` (WKB)

Outputs a Parquet‑backed Athena table at the requested S3 prefix, with columns:

- keys: `timestamp` (configurable), `node_id`, `geometry` (from grid), partition columns (if requested)
- for each input column `X`: `X_mean`, `X_median`, `X_stddev`, `X_min`, `X_max`, `X_n`, `X_mad`

### Options

`--db-name DB_NAME`
: Destination Athena database for the output table. Required.

`--data-db-name DATA_DB`
: Athena database for the input data table. Defaults to `--db-name`.

`--grid-db-name GRID_DB`
: Athena database for the grid table. Defaults to `--db-name`.

`--mapping-db-name MAP_DB`
: Athena database for the mapping table. Defaults to `--db-name`.

`--data-table DATA_TABLE`
: Source data table with `rowid`, timestamp column, and the columns to aggregate. Required.

`--grid-table GRID_TABLE`
: Grid table with `node_id` and WKB `geometry`. Required.

`--mapping-table MAP_TABLE`
: Mapping table with `rowid` and `node_id`. Required.

`--columns COL1,COL2,...`
: Comma‑separated list of columns to aggregate. Required.

`--bucket-name BUCKET`
: S3 bucket for the output dataset. Required.

`--output-prefix PREFIX`
: S3 prefix for Parquet files and query results. Required.

`--output-table TABLE`
: Name of the destination Athena table to create. Required.

`--timestamp-col COL`
: Timestamp column name in the data table. Default: `timestamp`.

`--partition-cols COL1,COL2`
: Optional partition columns (must exist as Glue partitions in the data table). If includes the timestamp column, the final projection omits the timestamp from non‑partition fields.

`--profile PROFILE`
: AWS CLI profile. Default: `default`.

`--log-dir DIR`
: Directory to write logs and generated SQLs. Default: current directory.

`--help`
: Show usage.

### Example

```bash
./scripts/aggregation/aggregate_core.sh \
  --db-name geodata \
  --data-table sensor_points \
  --grid-table grid_nodes \
  --mapping-table sensor_node_links \
  --columns value1,value2 \
  --bucket-name my-bucket \
  --output-prefix aggregates/sensors/value12/ \
  --output-table sensors_value12_by_node \
  --partition-cols date \
  --profile my-aws
```

Notes:

- The script drops the destination table (if present) and pre‑cleans the S3 prefix before CTAS.
- Saves generated SQL under `--log-dir` as `aggregate_core_<table>.{query,ctas,drop}.sql`.

## `aggregate_direction_wrapper.sh`

Adds support for directional (circular) variables like wind direction:

1. Creates a temporary data table adding per‑row sine/cosine components for each directional column (optionally magnitude‑weighted). If `--magnitude-cols` is provided, each entry pairs with the corresponding `--direction-cols` entry; use the literal `skip` to treat that direction as unit magnitude.
2. Runs `aggregate_core.sh` on the transformed data to aggregate `*_sin` and `*_cos` components (and magnitudes, if present) using the usual scalar stats.
3. Projects aggregated components back to angles (`atan2`) and computes circular dispersion for `stddev`/`mad` using the resultant vector length.
4. Writes the final table to the requested output location; cleans temporary resources.

### Additional Options

`--direction-cols COL1,COL2`
: Comma‑separated directional columns to treat as angles in degrees. Required for circular processing.

`--magnitude-cols MAG1,MAG2`
: Optional magnitudes paired 1:1 with `--direction-cols`. Use a column name for weighting or `skip` for unit vectors.

All other options are the same as `aggregate_core.sh`.

### Example

```bash
./scripts/aggregation/aggregate_direction_wrapper.sh \
  --db-name geodata \
  --data-table meteo_points \
  --grid-table grid_nodes \
  --mapping-table meteo_node_links \
  --columns wind_speed,temperature,wind_dir_deg \
  --direction-cols wind_dir_deg \
  --magnitude-cols wind_speed \
  --bucket-name my-bucket \
  --output-prefix aggregates/meteo/wind/ \
  --output-table meteo_wind_by_node \
  --partition-cols date \
  --profile my-aws
```

## `aggregate_projection_wrapper.sh`

Projects a single column onto the line from each grid node to a fixed geodetic point, then aggregates the projected values. Useful for along‑track/line‑of‑sight projections.

### Additional Options

`--projection-col COL`
: Column to project (replaces the original column values in the temp data table). Required.

`--point-lat LAT` and `--point-lon LON`
: Latitude/longitude (degrees) of the reference point. Required.

All other options are the same as `aggregate_core.sh`.

### Example

```bash
./scripts/aggregation/aggregate_projection_wrapper.sh \
  --db-name geodata \
  --data-table radar_points \
  --grid-table grid_nodes \
  --mapping-table radar_node_links \
  --columns backscatter \
  --projection-col backscatter \
  --point-lat 40.4168 --point-lon -3.7038 \
  --bucket-name my-bucket \
  --output-prefix aggregates/radar/proj_madrid/ \
  --output-table radar_proj_by_node \
  --profile my-aws
```

## `finalize_geoparquet.sh`

Consolidates the output dataset into one Parquet file per partition directory and writes GeoParquet metadata. Optionally repairs Glue partitions.

### Options

`--db-name DB_NAME`
: Athena database of the aggregated table. Required.

`--bucket-name BUCKET`
: S3 bucket where the dataset resides. Required.

`--output-prefix PREFIX`
: S3 prefix of the dataset (same used during aggregation). Required.

`--output-table TABLE`
: Name of the aggregated Athena table. Required.

`--partition-cols COL1,COL2`
: Partition columns (if the dataset was partitioned) to trigger `MSCK REPAIR TABLE` after sync.

`--geometry-column NAME`
: Geometry column name for GeoParquet metadata. Default: `geometry`.

`--profile PROFILE`, `--log-dir DIR`, `--help`
: Standard options.

### Example

```bash
./scripts/aggregation/finalize_geoparquet.sh \
  --db-name geodata \
  --bucket-name my-bucket \
  --output-prefix aggregates/sensors/value12/ \
  --output-table sensors_value12_by_node \
  --partition-cols date \
  --profile my-aws
```

Behavior:

- Downloads the dataset from S3 (excluding Athena result folders) to a temp dir.
- Runs `merge_parquet.py` to ensure one file per partition directory and `add_geoparquet_metadata.py` to add GeoParquet metadata (encoding auto‑detected: WKB/WKT).
- Syncs back to S3 and, if `--partition-cols` is set, runs `MSCK REPAIR TABLE`.

## Utilities

### `merge_parquet.py`

- Ensures a single Parquet file per directory: emits `value.parquet` for partition directories of the form `key=value/`, or `data.parquet` otherwise.
- Detects Parquet files by probing schema (not by extension); ignores hidden/marker files.

Run: `python scripts/aggregation/merge_parquet.py --root /path/to/dataset`.

### `add_geoparquet_metadata.py`

- Adds the GeoParquet `geo` metadata (CRS84, primary column, bbox, geometry types) to each file.
- Auto‑detects geometry encoding (WKB vs WKT) from Arrow schema/sample; computes bbox robustly.
- Modes: `--local-path DIR` or `--s3-path s3://bucket/prefix` (requires `boto3`).

Example: `python scripts/aggregation/add_geoparquet_metadata.py --local-path /data --geometry-column geometry`.

### `build_stac_catalog.sh` and `build_geo_catalog.py`

- Builds a self‑contained STAC catalog for the finalized GeoParquet dataset. Assets are staged under `assets/`, items under `items/`, and a root `collection.json` is written. Partition sub‑catalogs are created automatically.
- `build_stac_catalog.sh` stages data (from S3 or local), builds a small Docker image using `Dockerfile.catalog`, and runs `build_geo_catalog.py` inside the container.

Examples:

```bash
# From S3
./scripts/aggregation/build_stac_catalog.sh \
  --collection demo-collection \
  --s3-uri s3://my-bucket/aggregates/sensors/value12/ \
  --profile my-aws \
  --output-dir /tmp/catalog_out

# From local directory
./scripts/aggregation/build_stac_catalog.sh \
  --collection demo-collection \
  --local-source-dir /tmp/local_dataset \
  --output-dir /tmp/catalog_out
```

Direct use: `python scripts/aggregation/build_geo_catalog.py <output_root> --collection-id ID --source-dir <dataset_dir> [--copy] [--item-properties file.json] [--collection-properties file.json]`.

## Typical Workflow

1. Aggregate data (core or wrappers):

```bash
./scripts/aggregation/aggregate_core.sh \
  --db-name geodata \
  --data-table sensor_points \
  --grid-table grid_nodes \
  --mapping-table sensor_node_links \
  --columns value1,value2 \
  --bucket-name my-bucket \
  --output-prefix aggregates/sensors/value12/ \
  --output-table sensors_value12_by_node
```

2. Finalize GeoParquet:

```bash
./scripts/aggregation/finalize_geoparquet.sh \
  --db-name geodata \
  --bucket-name my-bucket \
  --output-prefix aggregates/sensors/value12/ \
  --output-table sensors_value12_by_node \
  --partition-cols date
```

3. Package as STAC (optional):

```bash
./scripts/aggregation/build_stac_catalog.sh \
  --collection sensors-value12 \
  --s3-uri s3://my-bucket/aggregates/sensors/value12/ \
  --profile my-aws \
  --output-dir /tmp/sensors_value12_catalog
```

## Additional Notes

- All aggregation scripts write a log file under `--log-dir` and will emit the generated SQL queries for inspection.
- Safety checks prevent accidental deletion of bucket roots when pre‑cleaning S3 prefixes.
- Ensure the data table has a `rowid` column and the mapping table provides `rowid -> node_id` pairs.
- The grid table must contain `node_id` and a WKB `geometry` column; `finalize_geoparquet.sh` will add GeoParquet metadata so GIS tools recognize spatial extents/CRS.

