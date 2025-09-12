#!/usr/bin/env python3
"""
Visualize one or more grids stored in GeoParquet files as an interactive HTML map.

- Accepts multiple inputs; combines all grid points (dedup by node_id).
- Auto-zooms to the combined extent.
- Renders grid points either clustered (default) or as circle markers.

Example:
  python scripts/grids/visualize_grid.py --input grid_A.parquet --input grid_B.parquet --output grid_map.html
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import folium
from folium.plugins import MarkerCluster
import pyarrow.parquet as pq
import shapely.wkb as swkb


def read_points_from_parquet(path: Path) -> Tuple[List[str], List[Tuple[float, float]], Optional[Tuple[float, float, float, float]]]:
    """Return (node_ids, (lon, lat) pairs, bbox) from a GeoParquet with WKB Point geometries.

    bbox is (min_lon, min_lat, max_lon, max_lat) if present in GeoParquet metadata; otherwise None.
    """
    table = pq.read_table(path, columns=["node_id", "geometry"])  # type: ignore[arg-type]
    node_ids: List[str] = table.column("node_id").to_pylist()  # type: ignore[assignment]
    geoms_raw: List[bytes] = table.column("geometry").to_pylist()  # type: ignore[assignment]

    coords: List[Tuple[float, float]] = []
    for wkb in geoms_raw:
        if wkb is None:
            continue
        g = swkb.loads(wkb)
        coords.append((float(g.x), float(g.y)))

    # Try to read GeoParquet bbox from metadata
    bbox = None
    meta = table.schema.metadata or {}
    if b"geo" in meta:
        try:
            geo_meta = json.loads(meta[b"geo"].decode("utf-8"))
            bbox_list = geo_meta.get("columns", {}).get("geometry", {}).get("bbox")
            if isinstance(bbox_list, list) and len(bbox_list) == 4:
                bbox = tuple(float(x) for x in bbox_list)  # type: ignore[assignment]
        except Exception:
            bbox = None

    return node_ids, coords, bbox


def compute_bounds(coords: Iterable[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    count = 0
    for lon, lat in coords:
        count += 1
        if lon < minx:
            minx = lon
        if lat < miny:
            miny = lat
        if lon > maxx:
            maxx = lon
        if lat > maxy:
            maxy = lat
    if count == 0:
        raise ValueError("No coordinates available to compute bounds")
    return (minx, miny, maxx, maxy)


def make_map(
    node_ids: List[str],
    coords: List[Tuple[float, float]],
    tiles: str = "OpenStreetMap",
    title: Optional[str] = None,
    cluster: bool = True,
    marker_size: int = 3,
    sample: int = 0,
) -> folium.Map:
    # Optionally sample to reduce heavy maps
    if sample and sample > 0 and sample < len(coords):
        ids = node_ids[:sample]
        xy = coords[:sample]
    else:
        ids = node_ids
        xy = coords

    m = folium.Map(location=[0, 0], zoom_start=2, tiles=tiles, control_scale=True)

    if cluster:
        mc = MarkerCluster(name="Grid", disableClusteringAtZoom=12)
        mc.add_to(m)
        for i, (lon, lat) in enumerate(xy):
            tooltip = ids[i] if i < len(ids) else None
            folium.Marker(location=[lat, lon], tooltip=tooltip).add_to(mc)
    else:
        fg = folium.FeatureGroup(name="Grid")
        fg.add_to(m)
        for i, (lon, lat) in enumerate(xy):
            tooltip = ids[i] if i < len(ids) else None
            folium.CircleMarker(
                location=[lat, lon],
                radius=marker_size,
                color="#2c7fb8",
                fill=True,
                fillColor="#41b6c4",
                fillOpacity=0.7,
                opacity=0.8,
                tooltip=tooltip,
            ).add_to(fg)

    # Fit bounds
    try:
        minx, miny, maxx, maxy = compute_bounds(xy)
        m.fit_bounds([[miny, minx], [maxy, maxx]])
    except Exception:
        pass

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
    p = argparse.ArgumentParser(description="Visualize one or more grid GeoParquet files as an auto-zoomed HTML map")
    p.add_argument(
        "--input",
        action="append",
        required=True,
        help="Path to GeoParquet file (repeatable). Columns required: node_id (STRING), geometry (WKB)",
    )
    p.add_argument("--output", default="grid_map.html", help="Output HTML map path")
    p.add_argument("--tiles", default="OpenStreetMap", help="Basemap tiles (e.g., 'OpenStreetMap', 'CartoDB positron')")
    p.add_argument("--title", default=None, help="Optional title displayed on map")
    p.add_argument("--cluster", action="store_true", help="Cluster markers (default)")
    p.add_argument("--no-cluster", dest="cluster", action="store_false", help="Disable clustering and render circle markers")
    p.add_argument("--marker-size", type=int, default=3, help="Circle marker radius when not clustering")
    p.add_argument("--sample", type=int, default=0, help="Optional limit of points to render (0 = all)")
    p.set_defaults(cluster=True)
    args = p.parse_args()

    # Collect from multiple inputs
    seen: Dict[str, Tuple[float, float]] = {}
    for pth in args.input:
        in_path = Path(pth)
        if not in_path.exists():
            raise FileNotFoundError(f"Input file not found: {in_path}")
        ids, xy, _ = read_points_from_parquet(in_path)
        for i, coord in enumerate(xy):
            # Deduplicate by node_id if available; if lengths mismatch, fallback to indexing
            key = ids[i] if i < len(ids) else f"idx_{len(seen)}"
            if key not in seen:
                seen[key] = coord

    if not seen:
        raise ValueError("No points found in provided parquet files")

    node_ids = list(seen.keys())
    coords = list(seen.values())

    m = make_map(
        node_ids=node_ids,
        coords=coords,
        tiles=args.tiles,
        title=args.title,
        cluster=args.cluster,
        marker_size=args.marker_size,
        sample=args.sample,
    )

    out = Path(args.output)
    m.save(str(out))
    print(f"[OK] Map saved to {out}")


if __name__ == "__main__":
    main()
