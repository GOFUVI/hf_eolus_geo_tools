# Mapping Scripts

## Overview

The `scripts/mapping` directory provides `geo_mapping.sh`, a Bash utility that builds an Athena table mapping data rows to grid nodes when geometries fall within a specified radius. The resulting links are written as Parquet files to S3.

## Requirements

- Bash shell
- AWS CLI with credentials
- jq for JSON parsing

## `geo_mapping.sh`

This script drops any existing output table, removes previous data at the destination prefix, then runs a CTAS query that finds grid nodes within a configurable distance from each data point and stores the row-to-node pairs in an S3-backed Parquet table.

### Options

`--db-name DB_NAME`
: Athena database for the output table. Also used as the default database for input tables unless overridden. *(required)*

`--data-db-name DATA_DB`
: Athena database for the data input table. *(optional)*

`--grid-db-name GRID_DB`
: Athena database for the grid input table. *(optional)*

`--data-table DATA_TABLE`
: Source table with `rowid` and WKB `geometry` columns. *(required)*

`--grid-table GRID_TABLE`
: Grid table with `node_id` and WKB `geometry` columns. *(required)*

`--bucket-name BUCKET`
: S3 bucket for the output Parquet table. *(required)*

`--output-prefix PREFIX`
: S3 prefix for Parquet files. *(required)*

`--output-table TABLE`
: Name of the destination table to create. *(required)*

`--distance-km KM` or `--distance KM`
: Search radius in kilometers. Default: `10`.

`--profile PROFILE`
: AWS CLI profile to use. Default: `default`.

`--log-dir DIR`
: Directory for logs. Default: current directory.

`--help`
: Display usage information.

### Example

```bash
./scripts/mapping/geo_mapping.sh \
  --db-name geodata \
  --data-table sensor_points \
  --grid-table grid_nodes \
  --bucket-name my-bucket \
  --output-prefix mappings/sensor_points/ \
  --output-table sensor_node_links \
  --distance-km 5 \
  --profile my-aws
```

### Cross-Database Inputs

You can place the data and grid tables in different Athena databases while writing the output to a third database. Use `--data-db-name` and `--grid-db-name` to override the defaults:

```bash
./scripts/mapping/geo_mapping.sh \
  --db-name mappings_db \            # output table database
  --data-db-name raw_db \             # data table database
  --grid-db-name reference_db \       # grid table database
  --data-table sensor_points \        # table in raw_db
  --grid-table grid_nodes \           # table in reference_db
  --bucket-name my-bucket \
  --output-prefix mappings/sensor_points/ \
  --output-table sensor_node_links \
  --distance-km 5 \
  --profile my-aws
```

## SQL Logic

The script issues two key Athena statements.

### 1. Drop any existing mapping table

```sql
DROP TABLE IF EXISTS <db_name>.<output_table>;
```

This ensures the output table does not contain stale data before a new run.

### 2. Create the mapping table

```sql
CREATE TABLE <db_name>.<output_table> WITH (
    external_location = 's3://<bucket>/<prefix>/',
    format = 'PARQUET'
) AS
WITH params AS (
    SELECT <distance_km> AS r_km
),
data_pts AS (
    SELECT rowid,
           ST_Y(ST_GeomFromBinary(geometry)) AS lat,
           ST_X(ST_GeomFromBinary(geometry)) AS lon,
           geometry
    FROM <data_db>.<data_table>
),
grid_pts AS (
    SELECT node_id,
           ST_Y(ST_GeomFromBinary(geometry)) AS lat,
           ST_X(ST_GeomFromBinary(geometry)) AS lon,
           geometry
    FROM <grid_db>.<grid_table>
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
      ) <= c.r_km * 1000;
```

**Query breakdown**

- `params` defines the search radius (`r_km`) used throughout the query.
- `data_pts` extracts latitude and longitude from the input table while retaining the WKB geometry.
- `grid_pts` does the same for the grid node table.
- `deltas` computes latitude and longitude offsets that approximate a bounding box around each data point.
- `candidates` uses the bounding box to pre-select grid nodes near each data point, reducing distance calculations.
- The final `SELECT` applies `ST_Distance` on spherical geographies to keep only rows within the requested radius.

### Latitude/Longitude prefilter window

To limit the number of distance computations, the query first bounds candidate nodes inside a lat/lon rectangle around each data point. For a radius `r_km` and data latitude `φ` (in degrees), the window half‑sizes are:

- `delta_lat = r_km / 110.574`
- `delta_lon = r_km / (111.320 * cos(radians(φ)))`

These constants approximate kilometers per degree on the WGS84 ellipsoid; the longitude scale shrinks with latitude. The rectangle is intentionally generous so that true neighbors aren’t excluded; any false positives are removed by the final geodesic filter.

Notes and edge cases:
- Near the poles (|φ| → 90°), `delta_lon` grows; for very large radii and high latitudes, the window can span many degrees.
- The BETWEEN predicates do not wrap the antimeridian; if the search area straddles ±180°, consider smaller radii or pre‑filtering by longitude ranges.
- The final filter uses `ST_Distance` over spherical geography (great‑circle distance), which scales to large datasets; use an ellipsoidal library offline if you need sub‑meter geodesics.

## Details

1. The script runs in region `eu-west-3` and logs all AWS CLI calls.
2. A bounding box pre-filter reduces the number of distance calculations before computing geodesic distances via `ST_Distance`.
3. Results are stored in an Athena table backed by Parquet files in the specified S3 location.

## Additional Notes

- Ensure `aws` and `jq` are available in the PATH.
- The log file is written to `geo_mapping.sh.log` in the specified log directory.
