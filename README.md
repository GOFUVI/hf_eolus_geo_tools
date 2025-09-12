# HF-EOLUS Geospatial Processing Tools

## Overview

**HF-EOLUS Geospatial Processing Tools** is a set of command‑line scripts to transform and analyze large geospatial point datasets using cloud‑optimized formats. While born in the HF‑radar context, the utilities are generic: they operate on Athena tables that expose a binary `geometry` column and on prepared local files where applicable, and they produce GeoParquet assets with optional STAC metadata[\[1\]][1]. By leaning on open standards, the toolkit saves data in a **compact, analysis‑ready format** and describes it with **portable metadata**, so teams can use off‑the‑shelf analytics and quickly discover what they need.

**What does this repository do?** It provides a step‑by‑step pipeline — implemented as shell scripts — to go from raw tabular geospatial data (e.g., sensor/model outputs or analytics results) to analysis‑ready assets. Key features include:

-   **GeoParquet Conversion:** All output datasets are stored as Parquet files with embedded geospatial information (coordinates, geometry, CRS, etc.) following the **GeoParquet v1.1** specification[\[2\]][2]. This format stores geometries (points, polygons, etc.) in a binary column (e.g. WKB) along with coordinate reference metadata, making the files self-describing and directly readable by GIS software. Using columnar Parquet yields highly compressed files and fast query performance for large datasets.

-   **STAC Catalog Metadata:** The toolkit can generate a static **STAC catalog** (JSON) describing the outputs. STAC indexes data by space, time, and properties[\[3\]][3]. Each product (or time step) becomes a STAC **Item** with links to the Parquet asset, and Items are grouped into **Collections** for organization. This enables interoperability with STAC‑compatible tools — users can search and access the data via common libraries (e.g., PySTAC) or STAC browsers instead of handling files manually.

By adopting GeoParquet for storage and STAC for metadata, these tools avoid custom formats and fit naturally alongside other geospatial datasets[\[1\]][1]. A single time slice from a dense sensor network can contain **tens of thousands of points**, and a few weeks can exceed **millions**[\[1\]][1] — far beyond what CSV handles efficiently. Converting to GeoParquet cuts storage and accelerates columnar queries, while STAC makes it easy to find observations by region and time without a separate database. In short, this toolkit provides an end‑to‑end path to standardize, store, and catalog geospatial point data for analysis.

## Requirements

To use these scripts, you will need a Unix-like environment (Linux or macOS recommended). Python code is executed inside Docker containers, so no local Python setup is required. The main requirements are:

-   **Operating System:** Linux (or macOS with GNU tools). The scripts are Bash shell scripts and use common UNIX utilities. Windows users can run them via WSL2 or Docker Desktop.

-   **Docker:** Required. All Python steps run in containers (e.g., `python:3.11-slim`) and install needed packages inside the container on the fly. You do not need to install Python, Shapely, PyArrow, etc. on the host.

-   **AWS CLI:** Required for the core pipeline that uses AWS services (Athena/S3/Glue): hulls, grid table creation to S3/Athena, mapping, aggregation and finalization. Optional only if you use local‑only utilities (viewers, local grid generation, local GeoParquet metadata, STAC from local dirs).

-   **jq:** Required when running mapping/aggregation/finalization scripts that parse AWS CLI JSON output. Optional for local‑only utilities.

Ensure that command-line `bash` and coreutils are available (on most Linux/macOS they are by default).

## Installation

1.  **Obtain the Code:** Clone this repository or download the ZIP. If using the Zenodo archive, download the released package and extract it. The key scripts are in the `scripts/` directory of the repository. For example:

<!-- -->

    git clone https://github.com/GOFUVI/hf_eolus_geo_tools.git
    cd hf_eolus_geo_tools

This will place the suite of scripts into `hf_eolus_geo_tools/scripts/` along with a `docs/` folder containing further documentation.

1.  **Install Dependencies:** Ensure **Docker** is installed and available in your PATH. To run the pipeline against AWS (Athena/S3/Glue), install and configure the **AWS CLI** and `jq`. If you only use local utilities (viewers, local grid/metadata), you may skip AWS CLI/`jq`. No local Python installation is required.

Make sure you have `bash` and standard UNIX tools on your PATH. On macOS, you may need to install GNU versions of certain utilities (or use `brew install coreutils`) if differences arise, but generally the scripts aim to be portable.

1.  **Prepare Your Data:** The tools are dataset‑agnostic; any tabular point data can be processed. Depending on how you plan to run the pipeline:

   - **AWS/Athena pipeline:** Ensure your source data is available as an Athena table with a binary WKB `geometry` column (WGS84/CRS84 recommended), a unique `rowid`, and the columns you want to aggregate (including an event time column such as `timestamp`). The scripts will compute a coverage hull from your points, create a grid table, map rows to grid nodes, and aggregate.

   - **Local utilities:** If you are working locally, prepare Parquet or CSV files with either a WKB geometry column or explicit `longitude, latitude` columns. You can generate a grid GeoParquet locally and visualize hulls/grids; aggregation and mapping steps are designed for Athena.

Organize folders for intermediate outputs (GeoParquet) and final products (e.g., a STAC catalog). Most configuration (paths, grid spacing, table names) is passed via command‑line flags.

With the code in place and environment set up, you are ready to run the processing pipeline on your data.

## Usage and Workflow Example

The typical workflow is divided into four main stages, each performed by a dedicated shell script. The natural order is: **Coverage Hull -\> Grid Generation -\> Data Mapping -\> Aggregation**. In each stage, a script processes input data and produces a GeoParquet asset (or assets), and in one stage a STAC catalog is also created to index the results. Below we describe each step with example CLI usage. (Run each script from the repository's base directory or adjust paths accordingly.)

**Note:** All example commands assume the scripts are in the `scripts/` subdirectory. You might need to prepend `bash scripts/` to run them, or make them executable with `chmod +x` and call directly. Use the `-h` or `--help` flag on any script to see full usage and options.

### 1. Coverage Hull Generation

*Script:* `scripts/hulls/convex_hulls.sh` — **Generate coverage hull polygons** from Athena tables. For full usage and options, see [docs/hulls.md].

**Description:** The first step delineates the geographic footprint of your input dataset. Starting from point geometries stored in one or more AWS Athena tables (each exposing a `geometry` column), the script computes a **convex hull** for each table and then combines them via union or intersection to produce a final footprint polygon. This approach gives a clean envelope around all observed points; if you need a tighter outline (concave hull), that would require a different tool outside this utility.

The output is a GeoJSON file containing the hull geometry. Typically, each run will produce one polygon representing the dataset's footprint. You can keep this GeoJSON alongside your artifacts and use it directly in the next step to constrain grid creation.

**Inputs:** One or more Athena tables that contain a `geometry` column (WKB points). Tables may be provided fully qualified (e.g., `database.table`) or with a default `--database`.

You will specify the AWS profile, database/tables, and how to combine per-table hulls (`--operation union|intersection`).

**Usage Example:**

    # Build a convex hull from two Athena tables and write GeoJSON
    bash scripts/hulls/convex_hulls.sh \
        --profile my-aws \
        --database geodata \
        --tables cities,stations \
        --output-file outputs/footprint_hull.geojson \
        --output-location s3://my-bucket/athena-results/ \
        --operation union

After this step, you will have a GeoJSON polygon that delineates the dataset's footprint. You can preview it with the provided viewer (`scripts/hulls/view_hull.sh`) or load it in GIS software to verify the extent. It will be used in the next stage to define the grid area.

### 2. Grid Generation

*Script:* `scripts/grids/create_grid_table.sh` — **Generate analysis grid points** within the coverage area and publish them as a GeoParquet-backed Athena table. For full usage and options, see [docs/grids.md].

**Description:** With the footprint from step 1 in hand (a GeoJSON hull), this step defines a regular grid of points covering that area. You control the spacing in kilometers and can optionally apply a buffer around the hull. Under the hood, a Dockerized Python helper builds a GeoParquet file, then the script uploads it to S3 and creates an Athena table with columns `node_id` and `geometry` (WKB, WGS84). The resulting grid becomes the spatial scaffold for mapping and aggregation.

The primary mode uses a hull file to generate points constrained to the polygon; alternatively, you can provide a manual CSV of nodes (`node_id,longitude,latitude`) to use on its own or to augment the generated grid.

Grid origin and numbering

- Start corner: The generator projects the hull’s bounding box to UTM, insets it by half the requested spacing, and starts at the lower‑left (south‑west) corner of that inset box. It then steps east (x) and proceeds row by row northward (y).
- ID assignment: `node_id` is built as `<prefix><sequential_number>` in that row‑major order. No zero‑padding is applied by default.
- Why numbers aren’t consecutive: After creating the full lattice inside the bounding box, points outside the actual hull polygon are dropped. This filtering happens after numbering, so gaps appear in the numeric suffix. If you append manual nodes (`--manual-csv`) or run in `--mode append`, you can also end up with non‑consecutive IDs by design.
- Implication: Treat `node_id` as a unique identifier, not as an ordering or coordinate proxy. For spatial ordering, sort by latitude/longitude (or geometry) rather than by `node_id` lexicographically.

**Inputs:**

- AWS profile, database, output table name, S3 path for the grid dataset, and an S3 location for Athena query results.
- Either a GeoJSON hull from step 1 (`--hull-file`) with a node prefix and spacing, or a `--manual-csv` with explicit nodes. Model grid import is not supported by this utility.

**Usage Example (from hull):**

    bash scripts/grids/create_grid_table.sh \
      --profile my-aws \
      --database geodata \
      --hull-file outputs/footprint_hull.geojson \
      --node-prefix G \
      --grid-spacing-km 10 \
      --output-table grid_nodes \
      --table-location s3://my-bucket/grids/grid_nodes/ \
      --output-location s3://my-bucket/athena-results/

This generates points every ~10 km within the hull, writes a GeoParquet dataset to the specified S3 location, and registers the Athena table `geodata.grid_nodes`.

**Usage Example (manual nodes only):**

    bash scripts/grids/create_grid_table.sh \
      --profile my-aws \
      --database geodata \
      --manual-csv data/custom_nodes.csv \
      --output-table grid_nodes \
      --table-location s3://my-bucket/grids/grid_nodes/ \
      --output-location s3://my-bucket/athena-results/

You can preview a local GeoParquet grid with the viewer:

    bash scripts/grids/view_grid.sh --input path/to/grid_nodes.parquet --output grid_map.html

The grid dataset follows GeoParquet conventions: the `geometry` column encodes Point features in WKB and declares WGS84 as the CRS[\[2\]][2]. For option details, see [docs/grids.md].

### 3. Data Mapping

*Script:* `scripts/mapping/geo_mapping.sh` — **Link dataset rows to grid nodes** within a configurable search radius. For full usage and options, see [docs/mapping.md].

**Description:** This step connects your source data to the grid built in step 2 by finding, for each input row, nearby grid node(s) within a given distance. It runs a CTAS query in Athena that pre-filters candidates with a latitude/longitude window and then applies geodesic `ST_Distance` on spherical geographies. The result is a compact link table you can join with your data or grid to drive downstream aggregation and analysis. This utility is generic: it works with any Athena table that has a binary WKB `geometry` column and a unique `rowid`.

**Outputs:** A Parquet-backed Athena table containing `rowid, node_id` pairs stored at the S3 prefix you specify. Use these links to aggregate measurements by node, compute summaries on the grid, or join with additional attributes.

**Inputs:**

- Output database for the mapping table, S3 bucket/prefix for Parquet data, and output table name.
- Input data table with columns `rowid` and `geometry` (WKB), and the grid table from step 2 with `node_id` and `geometry`.
- Optional per-table databases if your data and grid live in different Athena databases.
- Search radius in kilometers (default 10), AWS CLI profile, and an optional log directory.

**Usage Example:**

    bash scripts/mapping/geo_mapping.sh \
      --db-name geodata \
      --data-table sensor_points \
      --grid-table grid_nodes \
      --bucket-name my-bucket \
      --output-prefix mappings/sensor_points/ \
      --output-table sensor_node_links \
      --distance-km 5 \
      --profile my-aws

After this step, you have a table of row-to-node links ready for analysis. For grid table structure and creation details, see [docs/grids.md].

Accuracy and trade‑offs

- Distance model: Mapping casts both geometries to Trino/Athena’s spherical geography and evaluates `ST_Distance` (great‑circle distance on a sphere with mean Earth radius). Implementations typically use the haversine or related spherical law‑of‑cosines formulation[\[5\]][5].
- Expected error: Relative to ellipsoidal WGS84 geodesics (e.g., Karney/Vincenty), spherical great‑circle distances are usually very close for kilometer‑scale ranges — on the order of meters over ~10 km, and commonly below ~0.1% — but errors increase for very long paths and near the poles/antimeridian[\[6\]][6].
- Why this choice: The spherical model is SIMD‑friendly and scales well to millions of row‑node checks inside Athena. The query first does a fast lat/lon window pre‑filter to minimize the number of `ST_Distance` evaluations, then applies the geodesic test.
- Higher‑fidelity options: If you require ellipsoidal accuracy, compute distances outside Athena using a geodesic library (e.g., GeographicLib/Karney) or reduce the search radius while compensating with a denser grid. Treat this as a precision vs. throughput trade‑off.

### 4. Aggregation and Analysis

*Scripts:* `scripts/aggregation/aggregate_core.sh` (core), with optional wrappers `aggregate_direction_wrapper.sh` (directional variables) and `aggregate_projection_wrapper.sh` (projection toward a point), plus `finalize_geoparquet.sh` to consolidate and add GeoParquet metadata. For full usage and options, see [docs/aggregation.md].

**Description:** With the grid from step 2 and the row→node links from step 3, this step summarizes numeric columns by time and `node_id`. The core script runs a CTAS in Athena joining the data table to the grid table through the mapping table, computing statistics such as mean, median, standard deviation, min/max, counts, and MAD for each selected column. The result is a Parquet dataset in S3 with an Athena table keyed by `timestamp`, `node_id`, and the node `geometry`; optionally partitioned by existing data columns.

When angles are present (e.g., wind direction), the directional wrapper converts to sine/cosine before aggregation and reconstructs angles and circular dispersion afterwards. If you need to project a column onto the line from each node to a reference point (e.g., along-track/line-of-sight component), the projection wrapper applies that transform first and then delegates to the core.

After the CTAS, the finalizer consolidates files (one per partition) and writes GeoParquet metadata so assets are self-describing in GIS tools and compatible with Athena.

**Usage examples:**

    # Aggregate columns by timestamp and node_id
    bash scripts/aggregation/aggregate_core.sh \
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

    # Directional variables (angles in degrees), optionally magnitude-weighted
    bash scripts/aggregation/aggregate_direction_wrapper.sh \
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

    # Project a column toward a geographic point and aggregate
    bash scripts/aggregation/aggregate_projection_wrapper.sh \
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

    # Consolidate and add GeoParquet metadata (optional but recommended)
    bash scripts/aggregation/finalize_geoparquet.sh \
      --db-name geodata \
      --bucket-name my-bucket \
      --output-prefix aggregates/sensors/value12/ \
      --output-table sensors_value12_by_node \
      --partition-cols date \
      --profile my-aws

After aggregation you'll have a per-node time series ready for further analysis or publication. To package it as a portable STAC catalog, use `scripts/aggregation/build_stac_catalog.sh` to stage Parquet under `assets/` and generate `collection.json` and nested items.

## STAC Catalog and Data Specifications

Outputs follow the **HF‑EOLUS GeoParquet and STAC conventions**[\[4\]][4]. In short:

- **GeoParquet (OGC v1.1):** Parquet with a `geo` metadata block describing geometry columns and CRS. We store geometries in WKB (typically column `geometry`) with WGS84/CRS84; files are self‑describing and open directly in GIS/GeoPandas. Columnar storage yields compact size and fast, selective reads[\[2\]][2].

- **STAC 1.0 + Table Extension:** Static JSON catalog describing Parquet assets. Structure: Catalog → Collections → Items. Each Item has a geometry (e.g., footprint or grid extent), datetime, and links to one or more Parquet assets; the Table Extension lists schema columns so users understand fields without opening files[\[3\]][3]. Works with STAC Browser and PySTAC.

- **How we apply it:** Every Parquet produced (hulls, grids, mappings, aggregates) can be registered as an Item asset; aggregated products may form their own Collections or Items. Catalogs are portable and can be hosted anywhere as static files.

For complete details and examples, see the HF‑EOLUS specification repository referenced above.

## Example: End-to-End Workflow

To illustrate a complete HF‑radar use case, suppose we process one month of radials from two stations (ABCD and WXYZ).

1.  **Hull:** Compute convex hulls from Athena and combine them (union) into a single coverage polygon.

        bash scripts/hulls/convex_hulls.sh \
          --profile my-aws \
          --database hf \
          --tables radials_abcd,radials_wxyz \
          --output-file outputs/hf_coverage.geojson \
          --output-location s3://my-bucket/athena-results/ \
          --operation union

2.  **Grid:** Create a 10 km grid within the hull and register it as an Athena table.

        bash scripts/grids/create_grid_table.sh \
          --profile my-aws \
          --database geodata \
          --hull-file outputs/hf_coverage.geojson \
          --node-prefix G \
          --grid-spacing-km 10 \
          --output-table grid_nodes \
          --table-location s3://my-bucket/grids/grid_nodes/ \
          --output-location s3://my-bucket/athena-results/

3.  **Mapping:** Link each station’s radials to the nearest grid nodes (5 km search radius). Here, raw radials live in database `hf` while the grid and outputs are in `geodata`.

        bash scripts/mapping/geo_mapping.sh \
          --db-name geodata \
          --data-db-name hf \
          --grid-db-name geodata \
          --data-table radials_abcd \
          --grid-table grid_nodes \
          --bucket-name my-bucket \
          --output-prefix mappings/hf/abcd/ \
          --output-table radials_abcd_node_links \
          --distance-km 5 \
          --profile my-aws

        bash scripts/mapping/geo_mapping.sh \
          --db-name geodata \
          --data-db-name hf \
          --grid-db-name geodata \
          --data-table radials_wxyz \
          --grid-table grid_nodes \
          --bucket-name my-bucket \
          --output-prefix mappings/hf/wxyz/ \
          --output-table radials_wxyz_node_links \
          --distance-km 5 \
          --profile my-aws

4.  **Aggregation:** Compute per‑node time‑series statistics. For radials, treat direction as angular (degrees) weighted by speed.

        bash scripts/aggregation/aggregate_direction_wrapper.sh \
          --db-name geodata \
          --data-db-name hf \
          --grid-db-name geodata \
          --mapping-db-name geodata \
          --data-table radials_abcd \
          --grid-table grid_nodes \
          --mapping-table radials_abcd_node_links \
          --columns radial_speed,radial_dir_deg \
          --direction-cols radial_dir_deg \
          --magnitude-cols radial_speed \
          --bucket-name my-bucket \
          --output-prefix aggregates/hf/abcd/ \
          --output-table abcd_radials_by_node \
          --partition-cols date \
          --profile my-aws

        bash scripts/aggregation/finalize_geoparquet.sh \
          --db-name geodata \
          --bucket-name my-bucket \
          --output-prefix aggregates/hf/abcd/ \
          --output-table abcd_radials_by_node \
          --partition-cols date \
          --profile my-aws

Optionally, package the aggregated dataset as a STAC catalog for sharing:

        bash scripts/aggregation/build_stac_catalog.sh \
          --collection HF-Radar-ABCD-2023-01 \
          --s3-uri s3://my-bucket/aggregates/hf/abcd/ \
          --profile my-aws \
          --zip-file abcd_catalog.zip

You now have analysis‑ready HF‑radar statistics per grid node, stored as GeoParquet and, if desired, indexed by a portable STAC catalog.

## Conclusion

This repository provides a concise, script-driven workflow to turn large geospatial point datasets and related model/sensor outputs into analysis‑ready assets. Following the steps above, you can go from delineating coverage areas, building analysis grids, and linking rows to grid nodes, to producing aggregated products and optional STAC catalogs. The focus on open standards keeps results portable and efficient: GeoParquet for compact, self‑describing storage and STAC for discoverability and interoperability.

For details on the underlying conventions, see the HF‑EOLUS specification repository[\[4\]][4]. If questions arise, consult the `docs/` folder for per‑script guides or reach out to the maintainers. We hope these tools help teams across domains — environmental monitoring, earth observation, mobility, and beyond — work more easily with cloud‑native geospatial data.

## Utility Documentation

- Hulls: [docs/hulls.md]
- Grids: [docs/grids.md]
- Mapping: [docs/mapping.md]
- Aggregation: [docs/aggregation.md]

## Acknowledgements

This work has been funded by the HF-EOLUS project (TED2021-129551B-I00), financed by MICIU/AEI /10.13039/501100011033 and by the European Union NextGenerationEU/PRTR - BDNS 598843 - Component 17 - Investment I3. Members of the Marine Research Centre (CIM) of the University of Vigo have participated in the development of this repository.


## Disclaimer

This software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and noninfringement. In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.


## References

- [Overview][1]
- [GeoParquet spec][2]
- [STAC spec][3]
- [HF‑EOLUS GeoParquet and STAC spec repository (README)][4]
- [Trino/Presto geospatial functions (spherical geography, ST_Distance)][5]
- [Karney 2013: Algorithms for geodesics on the ellipsoid (GeographicLib)][6]

[1]: https://github.com/GOFUVI/hf_eolus_geoparquet_stac_specs/blob/HEAD/overview.md
[2]: https://github.com/GOFUVI/hf_eolus_geoparquet_stac_specs/blob/HEAD/geoparquet_specs.md
[3]: https://github.com/GOFUVI/hf_eolus_geoparquet_stac_specs/blob/HEAD/stac_specs.md
[4]: https://github.com/GOFUVI/hf_eolus_geoparquet_stac_specs/blob/HEAD/README.md
[5]: https://trino.io/docs/current/functions/geospatial.html
[6]: https://geographiclib.sourceforge.io/geodesic.html

[docs/hulls.md]: docs/hulls.md
[docs/grids.md]: docs/grids.md
[docs/mapping.md]: docs/mapping.md
[docs/aggregation.md]: docs/aggregation.md

---
<p align="center">
  <a href="https://next-generation-eu.europa.eu/">
    <img src="logos/EN_Funded_by_the_European_Union_RGB_POS.png" alt="Funded by the European Union" height="80"/>
  </a>
  <a href="https://planderecuperacion.gob.es/">
    <img src="logos/LOGO%20COLOR.png" alt="Logo Color" height="80"/>
  </a>
  <a href="https://www.aei.gob.es/">
    <img src="logos/logo_aei.png" alt="AEI Logo" height="80"/>
  </a>
  <a href="https://www.ciencia.gob.es/">
    <img src="logos/MCIU_header.svg" alt="MCIU Header" height="80"/>
  </a>
  <a href="https://cim.uvigo.gal">
    <img src="logos/Logotipo_CIM_original.png" alt="CIM logo" height="80"/>
  </a>
  <a href="https://www.iim.csic.es/">
    <img src="logos/IIM.svg" alt="IIM logo" height="80"/>
  </a>

  
</p>
