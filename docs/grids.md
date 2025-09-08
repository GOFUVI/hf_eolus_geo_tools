# Grid Table Scripts

## Overview

The `scripts/grids` directory contains utilities for generating regular grids of
geographic points and publishing them as GeoParquet datasets in Amazon S3. The
primary entry point is `create_grid_table.sh`, which orchestrates grid
construction, uploads the resulting file, and creates an AWS Athena table. Grid
nodes can be derived from a GeoJSON hull file or supplied manually via CSV.

## Requirements

- Bash with standard Unix utilities
- AWS CLI configured with credentials and an optional default region
- Docker to run a temporary Python environment
- Python packages (installed inside the container): `shapely`, `pyproj`,
  `pandas`, `pyarrow`
- Optional: `jq` for processing Athena results (included in the Docker image)

## `create_grid_table.sh`

This Bash script generates a grid and registers it as a GeoParquet-backed Athena
table.

### Options

`--profile PROFILE`  
: AWS CLI profile to use. *(required)*

`--database DATABASE`  
: Athena database name. *(required)*

`--output-table TABLE`
: Name of the Athena table to create. *(required)*

`--table-location S3_PATH`
: S3 destination for the GeoParquet dataset. *(required)*

`--hull-file PATH`
: GeoJSON file describing the area to grid. Required with
`--node-prefix`.

`--node-prefix PREFIX`
: Prefix for generated grid node identifiers. Required with
`--hull-file`.

`--grid-spacing-km KM`
: Spacing between grid nodes in kilometers. Default: `10`.

`--buffer-km KM`
: Buffer to apply around the hull in kilometers. Default: `0`.

`--region REGION`
: AWS region. If omitted, the region from the AWS profile is used.

`--output-location S3_PATH`  
: S3 location for Athena query results. *(required)*

`--mode overwrite|append`  
: If `overwrite`, any existing data at the table location is deleted before
uploading the new grid. Default: `overwrite`.

`--manual-csv PATH`
: CSV file providing `node_id,longitude,latitude`. Required when no
`--hull-file` is supplied. The grid generated from the hull can
also be augmented with these nodes.

`--help`  
: Display usage information.

### Example

```bash
./scripts/grids/create_grid_table.sh \
  --profile my-aws \
  --database geodata \
  --hull-file hull.geojson \
  --node-prefix G \
  --output-table grid_nodes \
  --table-location s3://my-bucket/grids/grid_nodes/ \
  --output-location s3://my-bucket/athena-results/
```

## `create_grid_table.py`

A helper Python script invoked by `create_grid_table.sh` to build the grid and
write it as a GeoParquet file. It can also be run directly for advanced use
cases.

### Options

`--min-lon, --min-lat, --max-lon, --max-lat`
: Bounding box coordinates in degrees.

`--spacing-km`
: Grid spacing in kilometers.

`--prefix`
: Prefix for generated node identifiers.

`--output PATH`
: Output GeoParquet file. *(required)*

`--manual-csv PATH`
: Optional CSV of additional nodes to append. When used without bounding
box parameters, the script writes the CSV nodes directly to GeoParquet.

`--hull PATH`
: GeoJSON file describing the area to grid. When provided, `--spacing-km`
and `--prefix` are required.

`--buffer-km KM`
: Optional buffer to apply around the hull in kilometers.

### Example

```bash
python scripts/grids/create_grid_table.py \
  --min-lon -10 --min-lat 35 --max-lon -5 --max-lat 40 \
  --spacing-km 25 --prefix N --output grid_nodes.parquet
```

## Details

1. Hull polygons can be buffered to ensure the grid fully covers the desired
   area while keeping all nodes within the buffered boundary.
2. Grids are built in the appropriate UTM zone for accurate spacing before being
   transformed back to WGS84 coordinates.
3. The resulting GeoParquet includes metadata compliant with the GeoParquet
   1.1.0 specification, enabling seamless use with GIS tools and Athena.

## Additional Notes

- The scripts assume `aws`, `docker`, and `jq` are available in the PATH.
- Temporary files are created locally and removed after upload, but failed runs
  may leave behind `grid_nodes_*.parquet` files that can be cleaned manually.

## Visualizing the grid

You can preview the generated grid points in an interactive HTML map using the
Dockerized helper script:

```bash
# Clustered markers (default) with auto-zoom to extent
./scripts/grids/view_grid.sh \
  --input path/to/grid_nodes.parquet \
  --output grid_map.html \
  --tiles "OpenStreetMap" \
  --title "Grid Preview"

# Disable clustering and draw small circle markers
./scripts/grids/view_grid.sh \
  --input path/to/grid_nodes.parquet \
  --no-cluster --marker-size 3

# For very dense grids, limit the number of points rendered
./scripts/grids/view_grid.sh \
  --input path/to/grid_nodes.parquet \
  --sample 5000
```

Open `grid_map.html` to inspect the nodes. The script reads `node_id` and
`geometry` (WKB) from the GeoParquet file and fits the map view to the grid
extent.
