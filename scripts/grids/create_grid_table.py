#!/usr/bin/env python3
"""Create a regular grid and write it as GeoParquet."""

import argparse
import json
import pandas as pd
import numpy as np
from shapely.geometry import Point
import shapely.wkb as wkb
from pyproj import CRS, Transformer
import pyarrow as pa
import pyarrow.parquet as pq

# CRS84 definition copied from ingestion scripts for GeoParquet metadata
CRS84 = {
    "$schema": "https://proj.org/schemas/v0.5/projjson.schema.json",
    "type": "GeographicCRS",
    "name": "WGS 84 longitude-latitude",
    "datum": {
        "type": "GeodeticReferenceFrame",
        "name": "World Geodetic System 1984",
        "ellipsoid": {
            "name": "WGS 84",
            "semi_major_axis": 6378137,
            "inverse_flattening": 298.257223563,
        },
    },
    "coordinate_system": {
        "subtype": "ellipsoidal",
        "axis": [
            {"name": "Geodetic longitude", "abbreviation": "Lon", "direction": "east", "unit": "degree"},
            {"name": "Geodetic latitude", "abbreviation": "Lat", "direction": "north", "unit": "degree"},
        ],
    },
    "id": {"authority": "OGC", "code": "CRS84"},
}


def determine_utm_zone(lon: float, lat: float) -> CRS:
    """Return the UTM CRS for a given longitude and latitude."""
    zone = int((lon + 180) / 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return CRS.from_epsg(epsg)


def build_grid(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
               spacing_km: float, prefix: str) -> pd.DataFrame:
    """Build grid points within bounding box with given spacing."""
    wgs84 = CRS.from_epsg(4326)
    utm = determine_utm_zone((min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0)
    to_utm = Transformer.from_crs(wgs84, utm, always_xy=True)
    to_wgs84 = Transformer.from_crs(utm, wgs84, always_xy=True)

    # Convert bounds to UTM and apply inward buffer of half spacing
    spacing_m = spacing_km * 1000.0
    half = spacing_m / 2.0
    minx, miny = to_utm.transform(min_lon, min_lat)
    maxx, maxy = to_utm.transform(max_lon, max_lat)
    minx += half
    miny += half
    maxx -= half
    maxy -= half
    if minx > maxx or miny > maxy:
        raise ValueError("Bounding box too small for the requested spacing")

    xs = np.arange(minx, maxx + spacing_m, spacing_m)
    ys = np.arange(miny, maxy + spacing_m, spacing_m)

    records = []
    node = 1
    for y in ys:
        for x in xs:
            lon, lat = to_wgs84.transform(x, y)
            records.append((f"{prefix}{node}", lon, lat))
            node += 1

    df = pd.DataFrame(records, columns=["node_id", "longitude", "latitude"])
    return df


def main():
    parser = argparse.ArgumentParser(description="Create grid GeoParquet from bounding box")
    parser.add_argument("--min-lon", type=float)
    parser.add_argument("--min-lat", type=float)
    parser.add_argument("--max-lon", type=float)
    parser.add_argument("--max-lat", type=float)
    parser.add_argument("--spacing-km", type=float)
    parser.add_argument("--prefix")
    parser.add_argument("--output", required=True)
    parser.add_argument("--manual-csv")
    args = parser.parse_args()

    if args.manual_csv and not args.prefix and not all(
        v is not None for v in [args.min_lon, args.min_lat, args.max_lon, args.max_lat, args.spacing_km]
    ):
        # Manual-only mode: read CSV and compute bounds
        grid_df = pd.read_csv(args.manual_csv)[["node_id", "longitude", "latitude"]]
        grid_df = grid_df.drop_duplicates(subset="node_id")
        # Compute bounds from actual data for GeoParquet bbox
        min_lon = float(grid_df["longitude"].min())
        min_lat = float(grid_df["latitude"].min())
        max_lon = float(grid_df["longitude"].max())
        max_lat = float(grid_df["latitude"].max())
    else:
        # Grid generation requires all bounding arguments and prefix
        missing = [
            name
            for name, val in [
                ("--min-lon", args.min_lon),
                ("--min-lat", args.min_lat),
                ("--max-lon", args.max_lon),
                ("--max-lat", args.max_lat),
                ("--spacing-km", args.spacing_km),
                ("--prefix", args.prefix),
            ]
            if val is None
        ]
        if missing:
            parser.error("Grid generation requires: " + " ".join(missing))
        grid_df = build_grid(
            args.min_lon, args.min_lat, args.max_lon, args.max_lat, args.spacing_km, args.prefix
        )
        if args.manual_csv:
            manual_df = pd.read_csv(args.manual_csv)[["node_id", "longitude", "latitude"]]
            grid_df = pd.concat([grid_df, manual_df], ignore_index=True)
            grid_df = grid_df.drop_duplicates(subset="node_id")
        # Compute bounds from actual data (grid plus optional manual) for GeoParquet bbox
        min_lon = float(grid_df["longitude"].min())
        min_lat = float(grid_df["latitude"].min())
        max_lon = float(grid_df["longitude"].max())
        max_lat = float(grid_df["latitude"].max())

    # Convert to WKB and drop lon/lat after computing bbox
    grid_df["geometry"] = grid_df.apply(
        lambda r: wkb.dumps(Point(r.longitude, r.latitude), hex=False), axis=1
    )
    grid_df = grid_df[["node_id", "geometry"]]

    table = pa.Table.from_pandas(
        grid_df, schema=pa.schema([("node_id", pa.string()), ("geometry", pa.binary())]), preserve_index=False
    )
    meta = dict(table.schema.metadata or {})
    bbox = [min_lon, min_lat, max_lon, max_lat]
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Point"],
                "crs": CRS84,
                "bbox": bbox,
                "orientation": "counterclockwise",
                "edges": "spherical",
            }
        },
    }
    meta[b"geo"] = json.dumps(geo).encode("utf-8")
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, args.output)


if __name__ == "__main__":
    main()
