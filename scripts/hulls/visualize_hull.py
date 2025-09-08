#!/usr/bin/env python3
"""
Render a HTML map showing a hull GeoJSON, auto-zoomed to its extent.

Usage (typically run via the companion shell script):
  python scripts/hulls/visualize_hull.py --input PATH_TO_GEOJSON --output hull_map.html

Notes:
  - Expects WGS84 (EPSG:4326) coordinates as produced by convex_hulls.py
  - Supports GeoJSON Geometry, Feature, or FeatureCollection with one/more features
"""

import argparse
import json
from pathlib import Path
from typing import List

import folium
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry


def _collect_geometries(obj) -> List[BaseGeometry]:
    """Extract shapely geometries from GeoJSON-like object."""
    geoms: List[BaseGeometry] = []
    if not isinstance(obj, dict):
        raise ValueError("Invalid GeoJSON input: expected dict at root")

    def from_any(o):
        if not isinstance(o, dict) or "type" not in o:
            return None
        t = o.get("type")
        if t == "FeatureCollection":
            for feat in o.get("features", []) or []:
                g = from_any(feat)
                if g is not None:
                    geoms.append(g)
            return None
        if t == "Feature":
            geom = o.get("geometry")
            if geom:
                return shape(geom)
            return None
        # Geometry
        return shape(o)

    g = from_any(obj)
    if g is not None:
        geoms.append(g)
    if not geoms:
        raise ValueError("No geometry found in input GeoJSON")
    return geoms


def _bounds_union(geoms: List[BaseGeometry]):
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for g in geoms:
        bxmin, bymin, bxmax, bymax = g.bounds
        minx = min(minx, bxmin)
        miny = min(miny, bymin)
        maxx = max(maxx, bxmax)
        maxy = max(maxy, bymax)
    return (minx, miny, maxx, maxy)


def make_map(geojson_obj: dict, tiles: str = "OpenStreetMap", title: str | None = None) -> folium.Map:
    geoms = _collect_geometries(geojson_obj)

    # Initial map (center arbitrary; we'll fit to bounds)
    m = folium.Map(location=[0.0, 0.0], zoom_start=2, tiles=tiles, control_scale=True)

    # Add data layer
    folium.GeoJson(
        geojson_obj,
        name="Hull",
        style_function=lambda feat: {
            "fillColor": "#3186cc",
            "color": "#0056a6",
            "weight": 2,
            "fillOpacity": 0.25,
        },
        highlight_function=lambda feat: {"weight": 3, "color": "#ff7800"},
        tooltip=folium.GeoJsonTooltip(fields=[], aliases=[]),
    ).add_to(m)

    # Fit to hull bounds
    minx, miny, maxx, maxy = _bounds_union(geoms)
    # folium expects [[south, west], [north, east]] = [[lat_min, lon_min], [lat_max, lon_max]]
    m.fit_bounds([[miny, minx], [maxy, maxx]])

    if title:
        title_html = f"""
        <div style='position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 9999; background: rgba(255,255,255,0.9); padding: 6px 10px; border-radius: 4px; font-family: sans-serif; font-size: 14px;'>
            {title}
        </div>
        """
        m.get_root().html.add_child(folium.Element(title_html))

    folium.LayerControl(position="topright").add_to(m)
    return m


def main():
    parser = argparse.ArgumentParser(description="Visualize hull GeoJSON as an auto-zoomed HTML map")
    parser.add_argument("--input", required=True, help="Path to hull GeoJSON file (output from convex_hulls.sh)")
    parser.add_argument("--output", default="hull_map.html", help="Output HTML map path")
    parser.add_argument("--tiles", default="OpenStreetMap", help="Basemap tiles (e.g., 'OpenStreetMap', 'CartoDB positron')")
    parser.add_argument("--title", default=None, help="Optional title displayed on map")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise FileNotFoundError(f"Input file not found: {in_path}")

    with in_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    m = make_map(data, tiles=args.tiles, title=args.title)

    out_path = Path(args.output)
    m.save(str(out_path))
    print(f"[OK] Map saved to {out_path}")


if __name__ == "__main__":
    main()
