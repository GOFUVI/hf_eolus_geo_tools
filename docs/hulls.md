# Convex Hull Utilities

## Overview

The `scripts/hulls` directory provides tools to compute convex hulls from
geometries stored in AWS Athena tables. The shell script `convex_hulls.sh`
retrieves distinct WKB point geometries from one or more tables, computes a
convex hull for each set using a Dockerized Python environment, and combines the
hulls via union or intersection. The final polygon is written to a GeoJSON file
chosen by the user.

## Requirements

- Bash with standard Unix utilities
- AWS CLI with appropriate credentials
- Docker to run the Python environment
- Python packages installed inside the container: `shapely`, `pandas`

## `convex_hulls.sh`

### Options

`--profile PROFILE`
: AWS CLI profile to use. *(required)*

`--database DATABASE`
: Default Athena database. Optional if each table in `--tables` is fully
qualified as `database.table`.

`--tables TABLE1[,TABLE2,...]`
: Comma-separated list of source tables containing WKB point geometries. Each
item can be `TABLE` (uses `--database`) or `DATABASE.TABLE`.

`--output-file PATH`
: Local path for the resulting GeoJSON polygon. *(required)*

`--output-location S3_PATH`
: S3 location for Athena query results. *(required)*

`--operation union|intersection`
: How to combine individual hulls. Default: `union`.

`--region REGION`
: AWS region. If omitted, the region from the AWS profile is used.

`--help`
: Display usage information.

### Example

```bash
# Example using a default database for unqualified names
./scripts/hulls/convex_hulls.sh \
  --profile my-aws \
  --database geodata \
  --tables cities,stations \
  --output-file hull.geojson \
  --output-location s3://my-bucket/athena-results/ \
  --operation intersection

# Example mixing tables from multiple databases
./scripts/hulls/convex_hulls.sh \
  --profile my-aws \
  --tables geodata.cities,admin.stations,analytics.poi \
  --output-file hull.geojson \
  --output-location s3://my-bucket/athena-results/
```

## Details

1. Each input table is queried for distinct geometries to minimize processing.
2. Convex hulls are computed with Shapely inside a temporary Python container.
3. The resulting GeoJSON follows the standard RFC 7946 specification and can be
   used with common GIS tools.
