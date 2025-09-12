#!/usr/bin/env python3
"""Compute combined convex hull from WKB point sets."""

import argparse
import json
import pandas as pd
from shapely import wkb
from shapely.geometry import MultiPoint, mapping


def hull_from_csv(path: str):
    """Load WKB hex strings from CSV and return convex hull geometry."""
    df = pd.read_csv(path)
    col = df.columns[0]
    points = [wkb.loads(bytes.fromhex(h)) for h in df[col].dropna()]
    if not points:
        raise ValueError(f"No geometries found in {path}")
    return MultiPoint(points).convex_hull


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="CSV files with WKB hex strings")
    parser.add_argument("--operation", choices=["union", "intersection"], default="union",
                        help="How to combine per-table hulls")
    parser.add_argument("--output", required=True, help="Output GeoJSON file")
    args = parser.parse_args()

    hulls = [hull_from_csv(f) for f in args.inputs]
    result = hulls[0]
    for h in hulls[1:]:
        if args.operation == "union":
            result = result.union(h)
        else:
            result = result.intersection(h)

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(result),
                "properties": {},
            }
        ],
        "crs": {
            "type": "name",
            "properties": {"name": "EPSG:4326"},
        },
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(geojson, f)


if __name__ == "__main__":
    main()
