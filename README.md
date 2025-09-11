# HF-EOLUS Geospatial Processing Tools

## Overview

**HF-EOLUS Geospatial Processing Tools** is a collection of command-line scripts designed to transform and analyze high-frequency (HF) radar measurements and related model data using modern cloud-optimized geospatial formats. The HF-EOLUS project deals with two major data streams -- (1) coastal HF radar observations of ocean surface conditions, and (2) meteorological model outputs -- which produce massive volumes of geospatial data that must be stored and accessed efficiently[\[1\]][][\[2\]]. To address this, the tools convert raw data into **GeoParquet** files (an OGC standard for geospatial **Parquet** data) and organize them with **STAC** (SpatioTemporal Asset Catalog) metadata[\[1\]][][\[2\]]. By using these open standards, the toolkit ensures that HF radar and model datasets are saved in a **compact, analysis-ready format** and described with **standardized metadata**, allowing scientists to leverage off-the-shelf analytics tools and easily discover data of interest[\[1\]][][\[2\]].

**What does this repository do?** In essence, it provides a step-by-step pipeline -- implemented as a series of shell scripts -- to go from raw HF radar outputs (and optional model data) to analysis-ready geospatial assets. Key features include:

-   **GeoParquet Conversion:** All output datasets are stored as Parquet files with embedded geospatial information (coordinates, geometry, CRS, etc.) following the **GeoParquet v1.1** specification[\[3\]]. This format stores geometries (points, polygons, etc.) in a binary column (e.g. WKB) along with coordinate reference metadata, making the files self-describing and directly readable by GIS software. Using columnar Parquet yields highly compressed files and fast query performance for large datasets.

-   **STAC Catalog Metadata:** The toolkit can generate a static **STAC catalog** (as JSON files) describing the output data. STAC provides a standardized way to index data by space, time, and properties[\[4\]]. Each data product (or time step) becomes a STAC **Item** with links to the Parquet asset, and Items are grouped into **Collections** or catalogs for organization. This enables interoperability with STAC-compatible tools -- researchers can search and access HF-EOLUS data via common libraries (e.g. PySTAC) or STAC browsers instead of dealing with files manually.

By adopting GeoParquet for storage and STAC for metadata, these tools avoid custom formats and facilitate easy integration of HF radar measurements with other geospatial datasets[\[1\]]. For example, a single half-hour HF radar file can contain **tens of thousands of individual ocean current measurements**, and a week of data can exceed **one million points**[\[5\]] -- far too many for traditional CSV or text files to handle efficiently. Converting such data to GeoParquet reduces storage size and speeds up analysis by enabling SQL-like queries on the data. Meanwhile, STAC metadata makes it straightforward to find all radar observations in a given region or time range without needing a separate database. In summary, **HF-EOLUS Geo Tools** provides an end-to-end solution to *standardize, store, and catalog* HF radar and model data for scientific use.

## Requirements

To use these scripts, you will need a Unix-like environment (Linux or macOS recommended). Python code is executed inside Docker containers, so no local Python setup is required. The main requirements are:

-   **Operating System:** Linux (or macOS with GNU tools). The scripts are Bash shell scripts and use common UNIX utilities. Windows users can run them via WSL2 or Docker Desktop.

-   **Docker:** Required. All Python steps run in containers (e.g., `python:3.11-slim`) and install needed packages inside the container on the fly. You do not need to install Python, Shapely, PyArrow, etc. on the host.

-   **AWS CLI (optional):** Needed for steps that interact with S3/Athena (e.g., uploading data, creating tables). Configure with an AWS profile if you plan to use those features.

-   **jq (optional):** Some scripts parse AWS CLI JSON output locally and require `jq`.

Ensure that command-line `bash` and coreutils are available (on most Linux/macOS they are by default).

## Installation

1.  **Obtain the Code:** Clone this repository or download the ZIP. If using the Zenodo archive, download the released package and extract it. The key scripts are in the `scripts/` directory of the repository. For example:

<!-- -->

    git clone https://github.com/GOFUVI/hf_eolus_geo_tools.git
    cd hf_eolus_geo_tools

This will place the suite of scripts into `hf_eolus_geo_tools/scripts/` along with a `docs/` folder containing further documentation.

1.  **Install Dependencies:** Ensure **Docker** is installed and available in your PATH. If you plan to use S3/Athena steps, also install and configure the **AWS CLI** (and `jq` if your OS doesn’t include it). No local Python installation is required.

Make sure you have `bash` and standard UNIX tools on your PATH. On macOS, you may need to install GNU versions of certain utilities (or use `brew install coreutils`) if differences arise, but generally the scripts aim to be portable.

1.  **Configure Data Inputs:** Prepare the input data required for each step of the workflow (detailed below). Typically this means:

2.  For HF radar data: Gather the radar *radial metrics* files or their converted Parquet equivalents. If you have raw CODAR LLUV files, you might first run the HF Radial ingestion pipeline (in HF-EOLUS) to get Parquet files, or ensure the scripts can read the LLUV format directly (the current tools expect data in Parquet or CSV form).

3.  For model data (if using the grid/mapping with model output): Obtain the model output file(s) covering the region and time of interest (e.g., a NetCDF file of winds). Ensure you know the grid's projection or have latitude/longitude coordinates for grid points available.

Some configuration (like specifying station IDs, file paths, grid resolution, etc.) is done via command-line arguments to the scripts. It may be convenient to organize a folder for intermediate outputs (GeoParquet files) and final outputs (STAC catalog) prior to running the workflow.

With the code in place and environment set up, you are ready to run the processing pipeline on your data.

## Usage and Workflow Example

The typical workflow is divided into four main stages, each performed by a dedicated shell script. The natural order is: **Coverage Hull -\> Grid Generation -\> Data Mapping -\> Aggregation**. In each stage, a script processes input data and produces a GeoParquet asset (or assets), and in one stage a STAC catalog is also created to index the results. Below we describe each step with example CLI usage. (Run each script from the repository's base directory or adjust paths accordingly.)

**Note:** All example commands assume the scripts are in the `scripts/` subdirectory. You might need to prepend `bash scripts/` to run them, or make them executable with `chmod +x` and call directly. Use the `-h` or `--help` flag on any script to see full usage and options.

### 1. Coverage Hull Generation

*Script:* `scripts/hulls/convex_hulls.sh` — **Generate coverage hull polygons** from Athena tables.

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

*Script:* `scripts/grids/create_grid_table.sh` — **Generate analysis grid points** within the coverage area and publish them as a GeoParquet-backed Athena table.

**Description:** With the footprint from step 1 in hand (a GeoJSON hull), this step defines a regular grid of points covering that area. You control the spacing in kilometers and can optionally apply a buffer around the hull. Under the hood, a Dockerized Python helper builds a GeoParquet file, then the script uploads it to S3 and creates an Athena table with columns `node_id` and `geometry` (WKB, WGS84). The resulting grid becomes the spatial scaffold for mapping and aggregation.

The primary mode uses a hull file to generate points constrained to the polygon; alternatively, you can provide a manual CSV of nodes (`node_id,longitude,latitude`) to use on its own or to augment the generated grid.

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

The grid dataset follows GeoParquet conventions: the `geometry` column encodes Point features in WKB and declares WGS84 as the CRS[\[3\]]. For option details, see `docs/grids.md`.

### 3. Radar Data Mapping

*Script:* `scripts/mapping/geo_mapping.sh` — **Link dataset rows to grid nodes** within a configurable search radius.

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

After this step, you have a table of row-to-node links ready for analysis. For grid table structure and creation details, see `docs/grids.md`.

### 4. Aggregation and Analysis

*Scripts:* `scripts/aggregation/aggregate_core.sh` (core), with optional wrappers `aggregate_direction_wrapper.sh` (directional variables) and `aggregate_projection_wrapper.sh` (projection toward a point), plus `finalize_geoparquet.sh` to consolidate and add GeoParquet metadata.

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

The outputs produced by this toolkit adhere to the **HF-EOLUS GeoParquet and STAC specifications**[\[8\]][][\[6\]]. Here we provide a brief summary of these standards (refer to the official spec repository for full details):

-   **GeoParquet (OGC v1.1):** GeoParquet is an extension of Apache Parquet for geospatial data. Each Parquet file includes a special `geo` metadata object that defines geometry columns and the Coordinate Reference System. In our files, geometries are stored in a binary form (Well-Known Binary) in a column (often named `geometry`) at the top level[\[3\]]. The metadata specifies the geometry type (e.g., Point or Polygon), the CRS (usually WGS84 latitude-longitude), and bounding boxes, among other things. This means any GeoParquet file produced (hulls, grids, maps) can be opened by GIS tools or libraries like GeoPandas and will automatically recognize the geometry and coordinate system. By using Parquet, we gain efficient compression and the ability to query subset of columns -- for instance, one can read just the `u_current` and `v_current` fields from a currents Parquet without loading the entire dataset, which is ideal for cloud or large-scale analytics[\[9\]][][\[10\]].

-   **STAC (SpatioTemporal Asset Catalog):** STAC is a JSON-based specification for cataloging geospatial assets. Our STAC catalog follows **STAC 1.0.0** core with the **Table Extension** (to describe tabular data like Parquet schemas) and some custom fields defined by HF-EOLUS. The catalog is organized as:

-   A top-level **Catalog** (or Catalogs by data type) that links to one or more Collections.

-   One or more **Collections**, each representing a dataset or product type. For example, "HF Radar Surface Currents (30 min)" could be a collection, and "HF Radar Monthly Averages" another. Collections contain metadata common to the dataset: e.g., description, license, spatial extent (bounding box of the data), temporal extent, providers, etc.

-   Many **Items** within each Collection, each corresponding to a specific spatiotemporal slice of the data (analogous to an image scene or data file). In our case, an Item might represent all data for a particular timestamp (for radar snapshots) or a particular aggregated period. Each Item has its own geometry (e.g., the convex hull of that radar map's coverage) and timestamp, plus links to the data assets.

Every Parquet output is registered as an **asset** in some STAC Item. For instance, an Item for "2023-01-01 00:30 UTC currents" will have an asset pointing to `currents_20230101T0030Z.parquet` (just an example naming). The Item's properties include the station(s) involved, the time, and any processing flags. We also include the STAC **Table Extension** in Items/Collections to list the columns present in the Parquet and their meanings (e.g., columns `u_current` (unit: m/s, description: Eastward surface current) etc.), so users can understand the schema without opening the file[\[4\]].

Using STAC, a scientist can query the dataset by time or location. For example, they could search the catalog for Items from a specific month and bounding box, rather than manually filtering files. The STAC catalog produced is a static set of JSON files, so it can be hosted on a website or simply shared as-is. It is compatible with any STAC client -- for instance, one can use `pystac.Client.open(<catalog_path>)` in Python to load the catalog and iterate through Items programmatically.

**External Specification References:** For more details on the standards, see the [HF-EOLUS GeoParquet and STAC specification repository]. That repository contains comprehensive documentation of the conventions (GeoParquet metadata content, STAC layout, examples). The GeoParquet spec ensures our Parquet files meet interoperability requirements (geometry encoding, CRS specification, etc.)[\[3\]], and the STAC spec defines how we structure catalogs and items for HF radar data (including use of STAC extensions)[\[4\]]. By conforming to these, we align with international best practices and make the data *FAIR* (Findable, Accessible, Interoperable, Reusable).

## Example: End-to-End Workflow

To illustrate a complete use case, imagine we want to process one month of HF radar data from two stations and compare to a model:

1.  **Hull:** We run the hull script for each radar station to get their coverage polygons. For station A and B:

-   bash scripts/hull_generator.sh --station A --input A_points.parquet --output A_hull.parquet
        bash scripts/hull_generator.sh --station B --input B_points.parquet --output B_hull.parquet

    Suppose station A and B overlap in coverage; we could combine hulls or take the union if needed (the docs suggest the hull script can also output a merged hull if multiple stations given).

2.  **Grid:** Define a grid covering both A and B. If we have a model grid file (say a regional model), use that:

-   bash scripts/grid_generator.sh --hull A_hull.parquet --hull2 B_hull.parquet \
            --model-grid WindModel_Domain.nc --output radar_model_grid.parquet

    This yields `radar_model_grid.parquet` with points of the model grid that fall under the union of A and B's coverage.

3.  **Mapping:** Map radar data to grid and pull model data:

-   bash scripts/radar_mapping.sh --grid radar_model_grid.parquet \
            --radials A_radials.parquet B_radials.parquet \
            --model WindModel_Jan2023.nc --model-var U10,V10 \
            --start "2023-01-01" --end "2023-01-31" \
            --output currents_vs_model_Jan2023.parquet

    This reads the radials from station A and B for January 2023, computes total currents on each grid point (for each half-hour), and finds the corresponding model wind (U10,V10) at those points/times. The output Parquet might be partitioned by date. The script also creates a STAC collection (e.g., **collection:** "HF Radar Currents vs Model") and an item for each day or each file.

4.  **Aggregation:** Analyze the differences:

-   bash scripts/aggregate_analysis.sh --input currents_vs_model_Jan2023.parquet \
            --daily-stats --output currents_vs_model_stats.parquet

    This hypothetical command computes daily statistics such as mean bias and RMSE of radar vs model currents. The output `currents_vs_model_stats.parquet` could have one row per day (with no geometry, since it's an overall stat), and perhaps another output `currents_Jan2023_mean.parquet` for the spatial field of average currents over the month (with geometry for each grid cell). We manually add these as assets in the STAC catalog or the script could handle that, e.g., adding an item "January 2023 Mean" with an asset linking to `currents_Jan2023_mean.parquet`.

Now we have a complete dataset of radar-derived currents and their comparison to model, all in Parquet and described by STAC. A researcher can download the Parquet files and use pandas or a GIS tool to examine the data. Or they can load the STAC catalog into a tool like **STAC Browser** or a Jupyter notebook with PySTAC to query, for example, "find all times when the current speed exceeded 0.5 m/s at a certain location" without opening each file manually. The use of open standards (Parquet, STAC) means this pipeline's outputs can be readily integrated into larger workflows, such as machine learning pipelines (reading Parquet directly into TensorFlow/PyTorch) or interactive web maps (serving data via a STAC API).

## Conclusion

The HF-EOLUS Geo Tools repository provides a detailed, script-driven workflow for converting raw HF radar data (and related model data) into a form that is **immediately usable by scientists**. By following the steps above, users can reproduce the processing: from delineating radar coverage areas, creating analysis grids, mapping and merging data, to generating final summarized products. The emphasis on **standard formats** cannot be overstated -- the GeoParquet files ensure that the spatial data can be accessed efficiently (no more giant text files), and the STAC catalog ensures that as the data volume grows, it remains discoverable and well-organized.

For further reading on the underlying data specifications, see the HF-EOLUS project's spec documentation[\[8\]][][\[6\]]. And for any questions or issues using these tools, please refer to the documentation in the `docs/` folder (which includes more detailed guides for each script) or contact the project maintainers. We hope these tools enable oceanographers and meteorologists to more easily work with HF radar datasets in their research, taking advantage of the latest in cloud-native geospatial technology.

[\[1\]][] [\[2\]][] [\[5\]] overview.md

<https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/overview.md>

[\[3\]][] [\[9\]][] [\[10\]] geoparquet\_specs.md

<https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/geoparquet_specs.md>

[\[4\]] stac\_specs.md

<https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/stac_specs.md>

[\[6\]][] [\[7\]][] [\[8\]] README.md

<https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/README.md>

  [\[1\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/overview.md#L5-L13
  [\[2\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/overview.md#L23-L29
  [\[3\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/geoparquet_specs.md#L7-L15
  [\[4\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/stac_specs.md#L7-L15
  [\[5\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/overview.md#L15-L19
  [\[6\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/README.md#L18-L24
  [\[7\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/README.md#L5-L10
  [\[8\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/README.md#L2-L5
  [\[9\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/geoparquet_specs.md#L9-L17
  [\[10\]]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs/blob/5866885c54a5a941989b10387611d7f45c064dbd/geoparquet_specs.md#L20-L28
  [HF-EOLUS GeoParquet and STAC specification repository]: https://github.com/GOFUVI/hf_eouls_geoparquet_stac_specs
