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

*Script:* `hull_generator.sh` (for example) -- **Generate station coverage hull polygons** from input radar data.

**Description:** The first step computes the geographic area covered by an HF radar station's measurements. Using the set of all observation points from the radar (e.g., all echo locations over a period), the script derives a **hull polygon** that encloses the coverage area. By default, it uses a *concave hull* algorithm to tightly wrap around the outermost points (ensuring the shape follows the actual coverage outline, which may be concave along a coast). If data points are sparse or noisy at the edges, options are available to adjust hull tightness or filter out outliers before hull calculation.

The output is a GeoParquet file containing the hull geometry (or multiple geometries). Typically, each radar station will have one polygon representing its coverage area. This file conforms to the GeoParquet standard -- it has a geometry column (WKB format) with CRS WGS84 by default[\[3\]], and properties such as station ID, hull algorithm, date range of data used, etc.

**Inputs:** HF radar echo location data. This can be provided either as: - A Parquet or CSV file of point observations (with latitude/longitude for each echo), possibly the output of a prior ingestion step. - Or a directory of LLUV files (if the script supports reading them internally via Python/R). In most cases, using an intermediate Parquet of all points is more efficient.

You will specify the station (or input dataset) and any parameters for hull calculation (e.g., concave hull alpha value or whether to use convex hull). The script may accept arguments like `--station <ID>` and `--points-file <path>`.

**Usage Example:**

    # Generate coverage hull for station ABCD using points from a Parquet file
    bash scripts/hull_generator.sh --station ABCD \
        --input data/radials/ABCD_all_points.parquet \
        --output outputs/ABCD_hull.parquet \
        --concave 1.0

In this example, `--concave 1.0` might control the concavity factor (alpha) for the hull (1.0 could mean moderately concave; if omitted, a convex hull might be used by default). The script reads all points for station **ABCD** from the input Parquet, computes the hull polygon, and writes it to `ABCD_hull.parquet`. The output Parquet will contain at least one row (the hull geometry) with metadata fields (station ID, area, etc.).

After this step, you will have a geospatial polygon delineating the radar coverage. This polygon can be visualized on a map (by loading the Parquet in GIS software or using GeoPandas) to verify it correctly represents the station range. It will also be used in the next step to define the grid extent.

### 2. Grid Generation

*Script:* `grid_generator.sh` -- **Generate analysis grid points or cells** within the coverage area.

**Description:** The second step defines a uniform spatial grid covering the region of interest (usually the radar hull or an intersection of multiple radar hulls and possibly a model domain). This grid will serve as the target locations for mapping the radar data and performing comparisons with model outputs. The script can create either: - **Point grid:** a set of points (e.g., center of each grid cell) at specified resolution (e.g., every 0.05° in latitude and longitude, or a certain kilometer spacing). - **Polygon grid:** actual cell polygons (e.g., squares or hexagons) covering the area.

The grid generation will typically be constrained to the radar coverage polygon, so that points outside the coverage hull are excluded. If a meteorological model grid is provided, the script can also **import an existing grid** (e.g., from a NetCDF file of a model) and just filter it to the hull bounding box or polygon. In that case, the grid spacing and projection match the model's. Otherwise, you can specify a resolution or use a default.

The output is a GeoParquet file with the grid geometry for each cell (point or polygon) and attributes like `cell_id` or grid indices. All grid points have latitude/longitude (or the specified CRS) and collectively cover the hull area. This acts as a template onto which radar data will be interpolated or aggregated.

**Inputs:** Either: - The hull polygon from step 1 (Parquet or GeoJSON). The script will read the polygon to know the region to cover. - Parameters for grid spacing and extent. For example, `--resolution 0.05` degrees or `--dx 5 --dy 5` km, etc. If using a model grid, an input model file or grid definition might be provided (e.g., `--model-grid WRF_domain.nc`). - Optionally, a coordinate reference system (CRS) if different from WGS84. By default WGS84 lat/lon is assumed for the grid unless a projection is needed.

**Usage Example:**

    # Generate a latitude-longitude point grid at 0.05° resolution for station ABCD's area
    bash scripts/grid_generator.sh --hull outputs/ABCD_hull.parquet \
        --resolution 0.05 \
        --output outputs/ABCD_grid.parquet

This command takes the hull polygon of station ABCD and generates a grid of points spaced 0.05 degrees (\~5 km) in lat/lon covering that polygon. The resulting `ABCD_grid.parquet` will contain many points, each with columns like `latitude, longitude, geometry` (geometry could be identical to the point coordinates) and possibly an index or cell ID.

If using a model grid, usage might be:

    # Use an existing model grid from a NetCDF, filter to hull area
    bash scripts/grid_generator.sh --hull outputs/ABCD_hull.parquet \
        --model-grid data/WindModel_Grid.nc \
        --output outputs/ABCD_modelgrid.parquet

In this case, the script would read the model grid file (which contains lat/lon for each grid cell, or a known projection), select points within the hull, and output them. This ensures the radar data will be mapped onto the model's grid coordinates for direct comparison.

The grid Parquet dataset will follow GeoParquet conventions: for example, if points, the `geometry` column is of type Point for each grid location, and metadata declares WGS84 as the CRS[\[3\]]. Having the grid in Parquet makes it easy to join with radar data later or query specific locations if needed.

### 3. Radar Data Mapping

*Script:* `radar_mapping.sh` -- **Map HF radar observations to the grid and (optionally) combine with model data.**

**Description:** This is the core step where the high-frequency radar measurements are mapped onto the defined grid. There are two primary modes this script may handle: - **Vector Mapping (Total currents):** If multiple radars are available, the script can combine radial velocity components from different stations to compute full 2D current vectors on each grid point (similar to how CODAR totals are produced). In this mode, the script takes radial data from two or more stations, finds where their coverage overlaps on the grid, and solves for the u/v components at each grid cell (e.g., via least-squares). The result is a dataset of **vector currents on the grid** for each time step. - **Model Comparison Mapping:** If a model dataset is provided, the script maps each radar observation or derived vector to the nearest grid cell and pairs it with the corresponding model value. For example, it can project model winds or currents onto the radar radial direction (to get model "radial" components) and then compute differences. Alternatively, it interpolates radar-measured currents to the model grid points.

In practice, the script can produce time-indexed outputs. For each radar measurement time (say every 30 minutes), it will generate grid-based values. These could be in the form of separate output files per time or a partitioned Parquet where time is one of the partition keys.

**Outputs:** One or more GeoParquet files containing mapped data. Some possible output structures: - **Radar vector field Parquet:** Each row represents a grid cell at a given timestamp, with columns for the estimated U and V components of ocean current (or whatever quantity is measured) at that location/time, plus the geometry (point) of that grid cell. If multiple time steps, time could be another column or partition. This essentially is a gridded time series of the radar data. - **Difference or combined dataset Parquet:** If model data were included, the output might contain both the radar-derived value and model value at each grid cell/time, plus error metrics (difference, bias). For example, columns: `u_radar, v_radar, u_model, v_model, u_diff, v_diff` at each point/time.

All such outputs include spatial geometry and hence are GeoParquet-compliant. Time can be recorded as an ISO timestamp string or numeric datetime and will be included in STAC metadata later.

The mapping script may also create intermediate files like a mapping matrix or weights, but those are usually internal. The primary interest is the final mapped results.

**Inputs:** - The grid file from step 2 (`--grid outputs/ABCD_grid.parquet`). - Radar data: one or more input files containing radial observations. For example, two Parquet files for station ABCD and station WXYZ radials, each row having (time, location, radial\_speed, angle, etc.). If only one station is used, it might still map radial data to grid (though you can't get 2D vector from one station, you could still interpolate radial values onto grid points). - (Optional) Model data: a dataset of model values on the grid. This could be provided as a Parquet (if you pre-converted model output to Parquet) or directly from a model output file. If providing a model NetCDF, the script will read the necessary variable (e.g., U and V wind components) at the matching times and grid points. You might specify something like `--model data/WindModel_Output.nc` and `--model-var U10,V10` for wind components at 10m.

-   Additional arguments: time range to process (`--start 2023-01-01 --end 2023-01-31` for a month, for example) if you don't want the entire dataset, interpolation settings (nearest-neighbor or weighted average for mapping radars to grid), and quality filters (e.g., ignore radar data with high error flag).

**Usage Example (Vector Mapping):**

    # Map two radars into total vector currents on the grid for January 2023
    bash scripts/radar_mapping.sh --grid outputs/ABCD_grid.parquet \
        --radials station_ABCD.parquet station_WXYZ.parquet \
        --start "2023-01-01" --end "2023-01-31" \
        --output outputs/currents_202301.parquet

This would take radial data from station ABCD and WXYZ, within January 2023, and produce a Parquet of merged vector currents on the predefined grid. The output file `currents_202301.parquet` would be partitioned by date or contain a timestamp column. Each row might look like: {station\_pair: ABCD+WXYZ, time: 2023-01-01T00:30:00Z, lat, lon, u\_current, v\_current, ..., geometry}. The geometry column is the grid point (same as in the grid Parquet, allowing spatial joins).

**Usage Example (Radar vs Model Comparison):**

    # Map radar ABCD radials to model grid and compare with model currents
    bash scripts/radar_mapping.sh --grid outputs/ABCD_modelgrid.parquet \
        --radials station_ABCD.parquet \
        --model data/OceanModel.nc --model-var u_cur,v_cur \
        --output outputs/ABCD_vs_model.parquet

In this scenario, the script will project model u\_cur,v\_cur (current components) to the radial direction of station ABCD at each observation, or vice versa project radar vectors and find model at that point. The output might have columns like `radial_speed_observed` vs `radial_speed_model` for each grid cell and time, or direct vector component comparisons if we resolved vectors.

After the mapping step, you will have **analysis-ready geospatial data**: time-series on a uniform grid. This can be ingested into analysis tools or further aggregated. Importantly, this script can also generate the STAC metadata for the dataset: if configured, it will create a STAC **Collection** (describing the dataset as a whole) and STAC **Items** for each time period or data file. Each STAC Item will include links to the Parquet asset(s) for that time and relevant metadata (station IDs used, variables, etc.), using the HF-EOLUS STAC specification[\[6\]][][\[7\]]. This allows the collection of mapped data to be easily cataloged and shared. The STAC files (JSON) might be written to an output folder alongside the Parquet. For example, you might see `collection.json` and an `items/` directory with per-day item JSONs.

*(Under the hood, this script likely uses PySTAC to create the catalog entries programmatically. It populates fields like datetime, geospatial extent (using the grid bounds), and links each Parquet file. The STAC* *Table Extension* *may be used to describe the schema of the Parquet (so users know what columns like* `u_current` *mean), following the project's spec[\[6\]].)*\*

### 4. Aggregation and Analysis

*Script:* `aggregate_analysis.sh` -- **Aggregate mapped data and compute statistics or derived products.**

**Description:** The final stage performs any higher-level aggregation or statistical analysis on the mapped dataset. Depending on the project goals, this could include: - Computing **spatial statistics**: e.g., averaging currents over a time window to get mean flow patterns, or calculating variance, etc., at each grid point. - Computing **temporal statistics**: e.g., time-series of bias between radar and model, skill metrics like RMSE, correlations at each point. - Creating **derived gridded products**: e.g., a map of the difference between radar and model averaged over a month, or a map of data coverage (how many observations per grid cell).

This script likely reads the Parquet output from step 3 and performs group-by operations (using tools like Pandas or even DuckDB SQL) to summarize data. The results are then saved as new Parquet files (and possibly also as easy-to-visualize formats like CSV or GeoJSON for quick inspection).

For example, one output might be a Parquet with one row per grid cell containing the mean and standard deviation of the current components over the time range. Another output could be a Parquet of time-series metrics (one row per timestamp containing domain-wide averages).

**Outputs:** Depending on what is computed: - GeoParquet file of aggregated spatial data (geometry = grid cell, properties = statistics). For instance, `ABCD_currents_monthlyMean.parquet` with each grid point's monthly mean U and V. - CSV or Parquet of summary metrics (no geometry, just stats vs time or overall numbers). - Updated STAC catalog (if we produce new data products, we may add them as either new Collections or Items). If the aggregation yields a new layer (like "monthly mean currents"), a new STAC Item or Collection can be created for it. For simplicity, the script might just output data and not extend STAC (or it might update the existing catalog's collection to include links to the aggregated product as an additional asset or item).

**Inputs:** - The mapped data Parquet from step 3 (`--input outputs/currents_202301.parquet` for example). - Parameters specifying what aggregation to do (e.g., `--temporal-average 1M` to average over 1 month, or `--spatial-window 5` to do some smoothing). - If comparing multiple datasets, inputs for those as well.

**Usage Example:**

    # Aggregate January 2023 currents to monthly mean and compute radar-model differences
    bash scripts/aggregate_analysis.sh --input outputs/ABCD_vs_model.parquet \
        --spatial-avg mean --temporal-avg 1M \
        --output outputs/ABCD_Jan2023_stats.parquet

After running, suppose this produces `ABCD_Jan2023_stats.parquet` that contains for each grid cell: the mean radar current, mean model current, and their difference over January. The geometry column allows plotting these as maps (e.g., a map of bias where each grid cell is colored by difference). It may also output `ABCD_Jan2023_timeseries.csv` with overall RMSE per day.

Finally, if not already done in step 3, this stage can generate a **STAC catalog** or update it. Typically, if step 3 created a STAC Collection for the daily data, the aggregation script might create a new STAC Item (or Collection) for the aggregated result (e.g., an item representing the monthly average). The STAC metadata would document the processing (via STAC Processing Extension, perhaps) and link to the source data items. All STAC JSONs are written to a `stac/` directory in the output. You can validate the catalog using standard STAC validation tools or open it with a STAC browser to ensure it's correctly structured.

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
