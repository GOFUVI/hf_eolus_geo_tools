import os
import re
import re
import math
from datetime import datetime
import pandas as pd
import geopandas as gpd
import importlib.util
from typing import Optional
import shapely
from shapely import wkb as _shp_wkb, wkt as _shp_wkt
import uuid
from itertools import islice
import streamlit as st
import folium
from folium.plugins import HeatMap
from pystac import Catalog, Collection, Item
from pystac.utils import make_absolute_href
import branca.colormap as bcm
import html
import json

# Environment configuration
STAC_PATH = os.environ.get("STAC_PATH", "/data/catalog.json")
WIND_DIR = os.environ.get("WIND_DIRECTION_COLUMN")
WIND_SPEED = os.environ.get("WIND_SPEED_COLUMN")
SCALAR_COL = os.environ.get("SCALAR_COLUMN")
TIME_COL = os.environ.get("TIME_COLUMN")
# Comma-separated list of potential time columns (higher priority than auto-detect)
TIME_COLS_ENV = os.environ.get("TIME_COLUMNS")
# Max unique instants to show directly in a dropdown before falling back to date->time selection
try:
    TIME_MAX_INSTANTS = int(os.environ.get("TIME_MAX_INSTANTS", "200"))
except Exception:
    TIME_MAX_INSTANTS = 200
try:
    ITEMS_PAGE_SIZE_DEFAULT = int(os.environ.get("ITEMS_PAGE_SIZE", "100"))
except Exception:
    ITEMS_PAGE_SIZE_DEFAULT = 100
try:
    MAX_POINTS_DEFAULT = int(os.environ.get("MAX_POINTS", "500"))
except Exception:
    MAX_POINTS_DEFAULT = 500

st.set_page_config(page_title="STAC Geoparquet Browser", layout="wide")

# Default colormap name (bright on dark background)
DEFAULT_COLORMAP_NAME = os.environ.get("COLORMAP", "YlOrRd")
DEBUG_MODE = str(os.environ.get("STAC_DEBUG", "")).strip().lower() in ("1", "true", "yes", "on")
# Parquet engine preference: 'auto' | 'pyarrow' | 'fastparquet'
PARQUET_ENGINE_PREF = str(os.environ.get("PARQUET_ENGINE", "auto")).strip().lower()
DISABLE_PYARROW = str(os.environ.get("DISABLE_PYARROW", "")).strip().lower() in ("1", "true", "yes", "on")


def get_colormap_by_name(name):
    # Try ColorBrewer 9-class maps (e.g., YlOrRd_09) first, then continuous maps
    try:
        brewer_attr = "{}".format(name.replace(' ', '').replace('-', '_') + "_09")
        cm = getattr(bcm.linear, brewer_attr, None)
        if cm is not None:
            return cm
    except Exception:
        pass
    try:
        cont_attr = name.lower()
        cm = getattr(bcm.linear, cont_attr, None)
        if cm is not None:
            return cm
    except Exception:
        pass
    # Fallback to YlOrRd then viridis
    return getattr(bcm.linear, "YlOrRd_09", bcm.linear.viridis)


def _rerun():
    """Compat helper for Streamlit rerun across versions."""
    try:
        # Newer Streamlit
        st.rerun()
    except Exception:
        try:
            # Older Streamlit
            st.experimental_rerun()  # type: ignore[attr-defined]
        except Exception:
            # Last resort: toggle a nonce in session state
            st.session_state["_force_rerun_nonce"] = st.session_state.get("_force_rerun_nonce", 0) + 1


def _fit_bounds_from_geoms(m, geoms):
    try:
        if geoms is None or len(geoms) == 0:
            return
        minx, miny, maxx, maxy = geoms.total_bounds
        if any(map(lambda v: v is None, [minx, miny, maxx, maxy])):
            return
        # Pad in case of a single point or zero-area bounds
        pad_x = max(1e-4, (maxx - minx) * 0.05)
        pad_y = max(1e-4, (maxy - miny) * 0.05)
        sw = [miny - pad_y, minx - pad_x]
        ne = [maxy + pad_y, maxx + pad_x]
        m.fit_bounds([sw, ne])
    except Exception:
        pass


def add_color_legend(m, cm, vmin, vmax, label, stops=7, is_dark=True):
    try:
        # Sample colors and build gradient CSS
        colors = []
        positions = []
        rng = float(vmax) - float(vmin)
        rng = rng if rng != 0 else 1.0
        for i in range(max(2, int(stops))):
            t = i / float(max(1, stops - 1))
            val = float(vmin) + rng * t
            c = cm(val)
            colors.append(c)
            positions.append(int(round(t * 100)))
        gradients = ", ".join(["{} {}%".format(colors[i], positions[i]) for i in range(len(colors))])

        # Tick labels
        tick_vals = [float(vmin) + rng * (i / float(max(1, stops - 1))) for i in range(stops)]
        ticks_html = "".join(["<span>{:.2f}</span>".format(tv) for tv in tick_vals])

        uid = str(uuid.uuid4()).replace("-", "")
        overlay_bg = "rgba(0,0,0,0.55)" if is_dark else "rgba(255,255,255,0.85)"
        text_color = "#fff" if is_dark else "#000"
        border_col = "rgba(255,255,255,.7)" if is_dark else "rgba(0,0,0,.4)"
        text_shadow = "0 1px 2px rgba(0,0,0,.7)" if is_dark else "0 1px 0 rgba(255,255,255,.6)"
        legend_html = (
            "<div id=\"legend-box-{}\" style=\"position: fixed; z-index: 9999; top: 10px; right: 10px; "
            "min-width: 260px; max-width: 420px; padding: 0; background: transparent; border-radius: 4px;\">".format(uid) +
            "<details open style=\"background:{}; padding:6px 10px; border-radius:4px; color:{}; font-size:12px; font-family:sans-serif; box-shadow:0 1px 4px rgba(0,0,0,.6);\">".format(overlay_bg, text_color) +
            "<summary style=\"font-weight:600; list-style:none; cursor:pointer; display:flex; align-items:center; justify-content:space-between;\">" +
            "<span style=\"color:{}; text-shadow:{};\">{}</span>".format(text_color, text_shadow, label) +
            "<span style=\"margin-left:12px;\">▾</span></summary>" +
            "<div style=\"margin-top:6px;\">" +
            "<div style=\"height: 10px; border: 1px solid {}; background: linear-gradient(to right, {});\"></div>".format(border_col, gradients) +
            "<div style=\"display:flex; justify-content:space-between; margin-top:4px; color:{};\">{}".format(
                text_color,
                ticks_html.replace("<span>", "<span style='color:{};text-shadow:{}'>".format(text_color, text_shadow))
            ) +
            "</div></details></div>"
        )
        m.get_root().html.add_child(folium.Element(legend_html))
    except Exception:
        # Fallback to default legend if custom fails
        try:
            cm.caption = label
            cm.add_to(m)
        except Exception:
            pass

@st.cache_resource
def load_root(path):
    # Allow passing a directory and prefer catalog.json over collection.json
    resolved = path
    if os.path.isdir(path):
        cat_candidate = os.path.join(path, "catalog.json")
        col_candidate = os.path.join(path, "collection.json")
        if os.path.exists(cat_candidate):
            resolved = cat_candidate
        elif os.path.exists(col_candidate):
            resolved = col_candidate
        else:
            raise FileNotFoundError(
                "No catalog.json or collection.json found under directory: {}".format(path)
            )

    # Prefer loading as Catalog; fall back to Collection
    try:
        return Catalog.from_file(resolved)
    except Exception:
        return Collection.from_file(resolved)

def try_load_root(path):
    """Try to load a STAC root from a path or directory; return None if not loadable."""
    try:
        return load_root(path)
    except Exception:
        return None

def _dir_has_stac(path):
    try:
        return os.path.exists(os.path.join(path, "catalog.json")) or os.path.exists(os.path.join(path, "collection.json"))
    except Exception:
        return False

def _normalize_start_path(p: str) -> str:
    # If a file is provided, browse starting from its directory
    try:
        return p if os.path.isdir(p) else os.path.dirname(p)
    except Exception:
        return p


def _get_rel_href(obj, rel: str, default_base: Optional[str] = None) -> Optional[str]:
    """Return absolute href for a given rel from a STAC object if present."""
    try:
        base = getattr(obj, "get_self_href", lambda: None)() or default_base
        for lk in getattr(obj, "links", []) or []:
            try:
                if lk.rel != rel:
                    continue
                # Link may have absolute target
                abs_href = None
                try:
                    abs_href = lk.get_absolute_href()  # type: ignore[attr-defined]
                except Exception:
                    abs_href = None
                if abs_href:
                    return abs_href
                href = getattr(lk, "target", None) or getattr(lk, "href", None)
                if href and base:
                    return make_absolute_href(str(href), str(base))
            except Exception:
                continue
    except Exception:
        pass
    return None


def try_load_items_from_dir(path):
    """Return a list of STAC Items if the directory contains item JSONs.

    Non-recursive. Ignores files that are not valid STAC Items.
    """
    items = []
    try:
        if not os.path.isdir(path):
            return items
        for name in sorted(os.listdir(path)):
            if not name.lower().endswith(".json"):
                continue
            fp = os.path.join(path, name)
            if not os.path.isfile(fp):
                continue
            try:
                it = Item.from_file(fp)
                items.append(it)
            except Exception:
                continue
    except Exception:
        pass
    return items


def find_loose_item_hrefs_near(href_or_dir: str) -> list[str]:
    """Best-effort discovery of item JSON files under an `items/` folder next to a catalog/collection.

    - Starts at the directory containing `href_or_dir` (if it's a file), else the directory itself.
    - Looks for `items/` and scans recursively for `*.json` that parse as STAC Items.
    - Returns a list of absolute paths to candidate item JSON files.
    """
    try:
        base = href_or_dir
        if os.path.isfile(base):
            base = os.path.dirname(base)
        items_root = os.path.join(base, "items")
        if not os.path.isdir(items_root):
            return []
        hrefs: list[str] = []
        for root_dir, _dirs, files in os.walk(items_root):
            for fn in files:
                if not fn.lower().endswith(".json"):
                    continue
                fp = os.path.join(root_dir, fn)
                # Probe quickly whether it is an Item
                try:
                    _ = Item.from_file(fp)
                    hrefs.append(fp)
                except Exception:
                    continue
        hrefs.sort()
        return hrefs
    except Exception:
        return []

@st.cache_data
def load_asset(href):
    """Load a GeoParquet robustly, with selectable Parquet engine to avoid SIGILL on some ARM CPUs.

    Selection order:
    - If PARQUET_ENGINE=fastparquet or DISABLE_PYARROW=1: try fastparquet first, then GeoPandas fallback,
      and skip PyArrow path unless everything else fails.
    - If PARQUET_ENGINE=pyarrow: try PyArrow single-file first, then GeoPandas fallback, then fastparquet.
    - If PARQUET_ENGINE=auto (default): try PyArrow single-file, then GeoPandas, then fastparquet.
    """
    last_err: Optional[Exception] = None

    def _try_fastparquet():
        nonlocal last_err
        try:
            if importlib.util.find_spec("fastparquet") is not None:
                return _read_geoparquet_via_fastparquet(href)
        except Exception as e:
            last_err = e
        return None

    def _try_pyarrow_single():
        nonlocal last_err
        try:
            return _read_geoparquet_via_pyarrow_singlefile(href)
        except Exception as e:
            last_err = e
            return None

    def _try_geopandas_default():
        nonlocal last_err
        try:
            return gpd.read_parquet(href)
        except Exception as e:
            last_err = e
            return None

    prefer_fastparquet = DISABLE_PYARROW or (PARQUET_ENGINE_PREF == "fastparquet")
    prefer_pyarrow = PARQUET_ENGINE_PREF == "pyarrow"

    # Fastparquet-first path
    if prefer_fastparquet:
        out = _try_fastparquet()
        if out is not None:
            return out
        # Avoid importing PyArrow unless other options fail
        out = _try_geopandas_default()
        if out is not None:
            return out
        # As a last resort, try PyArrow single-file
        out = _try_pyarrow_single()
        if out is not None:
            return out
        if last_err is not None:
            raise last_err
        raise RuntimeError("Failed to load GeoParquet asset: no available reader succeeded")

    # PyArrow-first path (explicit)
    if prefer_pyarrow:
        out = _try_pyarrow_single()
        if out is not None:
            return out
        out = _try_geopandas_default()
        if out is not None:
            return out
        out = _try_fastparquet()
        if out is not None:
            return out
        if last_err is not None:
            raise last_err
        raise RuntimeError("Failed to load GeoParquet asset: no available reader succeeded")

    # Auto: PyArrow single-file → GeoPandas → fastparquet
    out = _try_pyarrow_single()
    if out is not None:
        return out
    out = _try_geopandas_default()
    if out is not None:
        return out
    out = _try_fastparquet()
    if out is not None:
        return out
    if last_err is not None:
        raise last_err
    raise RuntimeError("Failed to load GeoParquet asset: no available reader succeeded")


def _detect_geometry_column(df) -> Optional[tuple[str, str]]:
    """Detect geometry column and format.

    Returns (column_name, format), where format is 'wkb' or 'wkt'.
    """
    try:
        cols = list(df.columns)
    except Exception:
        return None

    # Helper to test a sample value
    def _is_wkb_sample(x) -> bool:
        try:
            if not isinstance(x, (bytes, bytearray)):
                return False
            # Try parsing first sample
            _ = _shp_wkb.loads(x)
            return True
        except Exception:
            return False

    def _is_wkt_sample(x) -> bool:
        try:
            if not isinstance(x, str):
                return False
            s = x.strip().upper()
            if not s:
                return False
            if not (s.startswith("POINT") or s.startswith("LINESTRING") or s.startswith("POLYGON")
                    or s.startswith("MULTI") or s.startswith("GEOMETRY")):
                return False
            _ = _shp_wkt.loads(x)
            return True
        except Exception:
            return False

    candidates = []
    for c in cols:
        try:
            s = df[c].dropna()
            if s.empty:
                continue
            v = s.iloc[0]
            if _is_wkb_sample(v):
                candidates.append((c, "wkb"))
            elif _is_wkt_sample(v):
                candidates.append((c, "wkt"))
        except Exception:
            continue

    # Prefer a column explicitly named 'geometry'
    for c, fmt in candidates:
        if c.lower() == "geometry":
            return c, fmt
    return candidates[0] if candidates else None


def _series_from_wkb(series):
    try:
        from shapely import from_wkb as _from_wkb
        return _from_wkb(series)
    except Exception:
        return series.apply(lambda b: _shp_wkb.loads(b) if isinstance(b, (bytes, bytearray)) else None)


def _series_from_wkt(series):
    try:
        from shapely import from_wkt as _from_wkt
        return _from_wkt(series)
    except Exception:
        return series.apply(lambda s: _shp_wkt.loads(s) if isinstance(s, str) and s.strip() else None)


def _read_geoparquet_via_fastparquet(path):
    import pandas as _pd

    df = _pd.read_parquet(path, engine="fastparquet")
    detected = _detect_geometry_column(df)
    if not detected:
        # No obvious geometry found; return a plain GeoDataFrame without geometry
        return gpd.GeoDataFrame(df)

    geom_col, fmt = detected
    if fmt == "wkb":
        geom_series = _series_from_wkb(df[geom_col])
    else:
        geom_series = _series_from_wkt(df[geom_col])

    # Drop source geometry column if present and construct GeoDataFrame
    data = df.drop(columns=[geom_col]) if geom_col in df.columns else df
    # Default to EPSG:4326 if CRS not discoverable in this fallback path
    return gpd.GeoDataFrame(data, geometry=geom_series, crs="EPSG:4326")


def _read_geoparquet_via_pyarrow_singlefile(path):
    """Read a single Parquet file with PyArrow (no dataset), reconstructing geometry.

    This avoids PyArrow's dataset engine injecting partition columns from the
    directory path (e.g., `pos_bragg=0/`), which can collide with real columns
    inside the file.
    """
    import pyarrow.parquet as pq  # type: ignore[import-not-found]

    # Force single-file read by opening the file directly (no dataset engine)
    pf = pq.ParquetFile(path)
    table = pf.read()
    df = table.to_pandas()

    detected = _detect_geometry_column(df)
    if not detected:
        # No obvious geometry found; return a plain GeoDataFrame without geometry
        return gpd.GeoDataFrame(df)

    geom_col, fmt = detected
    if fmt == "wkb":
        geom_series = _series_from_wkb(df[geom_col])
    else:
        geom_series = _series_from_wkt(df[geom_col])

    data = df.drop(columns=[geom_col]) if geom_col in df.columns else df
    gdf = gpd.GeoDataFrame(data, geometry=geom_series)

    # Try to set CRS from GeoParquet metadata; otherwise default to EPSG:4326
    try:
        meta = table.schema.metadata or {}
        if b"geo" in meta:
            try:
                geo_meta = json.loads(meta[b"geo"].decode("utf-8"))
                primary = geo_meta.get("primary_column") or geom_col
                col_meta = (geo_meta.get("columns") or {}).get(primary, {})
                crs_def = col_meta.get("crs") or col_meta.get("srs")
                if isinstance(crs_def, dict):
                    wkt = crs_def.get("wkt") or crs_def.get("value")
                    if isinstance(wkt, str) and wkt.strip():
                        gdf.set_crs(wkt, allow_override=True, inplace=True)
                elif isinstance(crs_def, str) and crs_def.strip():
                    gdf.set_crs(crs_def, allow_override=True, inplace=True)
                else:
                    gdf.set_crs("EPSG:4326", allow_override=True, inplace=True)
            except Exception:
                gdf.set_crs("EPSG:4326", allow_override=True, inplace=True)
        else:
            gdf.set_crs("EPSG:4326", allow_override=True, inplace=True)
    except Exception:
        # Best-effort default CRS if anything fails
        try:
            gdf.set_crs("EPSG:4326", allow_override=True, inplace=True)
        except Exception:
            pass

    return gdf


def resolve_asset_href(asset, item, stac_path):
    # Prefer absolute HREF provided by pystac if available
    try:
        abs_href = asset.get_absolute_href()
        if abs_href:
            return abs_href
    except Exception:
        pass

    href = asset.href or ""
    # Resolve relative to item self href if present
    try:
        item_self = item.get_self_href()
    except Exception:
        item_self = None
    if item_self:
        try:
            return make_absolute_href(href, item_self)
        except Exception:
            base = os.path.dirname(item_self)
            return os.path.normpath(os.path.join(base, href))

    # Fallback: resolve relative to STAC_PATH (file or directory)
    base_dir = stac_path if os.path.isdir(stac_path) else os.path.dirname(stac_path)
    return os.path.normpath(os.path.join(base_dir, href))


def _extract_table_columns_meta(collection, item, asset_key, asset):
    """
    Return a mapping: column_name -> metadata dict from STAC Table extension.

    Looks for `table:columns` primarily on the selected Asset. Falls back to:
    - Item properties (non-standard, but seen in the wild)
    - Collection `item_assets[asset_key]` definition (via Item Assets extension)
    """
    meta = {}

    def _parse_columns(cols):
        result = {}
        try:
            for c in cols or []:
                try:
                    name = c.get("name") or c.get("column")
                except Exception:
                    name = None
                if not name:
                    continue
                result[name] = {
                    "description": c.get("description") or c.get("descr") or c.get("title"),
                    "type": c.get("type"),
                    "unit": c.get("unit") or c.get("units"),
                }
        except Exception:
            pass
        return result

    # 1) Asset extra fields
    try:
        cols = None
        if hasattr(asset, "extra_fields") and isinstance(asset.extra_fields, dict):
            cols = asset.extra_fields.get("table:columns") or asset.extra_fields.get("table_columns")
        if not cols:
            # Try full dict in case extension fields were flattened differently
            ad = None
            try:
                ad = asset.to_dict()  # type: ignore[attr-defined]
            except Exception:
                ad = None
            if isinstance(ad, dict):
                cols = ad.get("table:columns") or ad.get("table_columns")
        meta.update(_parse_columns(cols))
    except Exception:
        pass

    # 2) Item-level (non-standard, fallback only)
    try:
        props = getattr(item, "properties", {}) or {}
        if isinstance(props, dict):
            meta.update(_parse_columns(props.get("table:columns") or props.get("table_columns")))
    except Exception:
        pass

    # 3) Collection item_assets definition
    try:
        extra = getattr(collection, "extra_fields", {}) or {}
        if isinstance(extra, dict):
            item_assets = extra.get("item_assets") or {}
            if isinstance(item_assets, dict):
                asset_def = item_assets.get(asset_key) or {}
                if isinstance(asset_def, dict):
                    meta.update(_parse_columns(asset_def.get("table:columns") or asset_def.get("table_columns")))
    except Exception:
        pass

    return meta


def _format_column_option(col_name, col_meta):
    """Display label for column select options, including description if present."""
    m = col_meta.get(col_name) if isinstance(col_meta, dict) else None
    desc = (m or {}).get("description")
    if desc:
        return "{} — {}".format(col_name, desc)
    return col_name


def _legend_label(col_name, col_meta):
    m = col_meta.get(col_name) if isinstance(col_meta, dict) else None
    desc = (m or {}).get("description")
    unit = (m or {}).get("unit")
    pieces = [col_name]
    if desc:
        pieces.append(desc)
    if unit:
        pieces.append("[{}]".format(unit))
    # Escape HTML to avoid breaking legend
    txt = " — ".join(pieces)
    return html.escape(txt)


def _dedupe_preserve(seq):
    seen = set()
    out = []
    for x in seq:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _auto_detect_time_columns(gdf, col_meta):
    """Return candidate time columns without heuristics.

    Only uses environment variables:
    - `TIME_COLUMN`
    - `TIME_COLUMNS` (comma/semicolon/space separated)
    Filters to columns present in `gdf` and preserves provided order.
    """
    candidates = []
    if TIME_COL:
        candidates.append(TIME_COL)
    if TIME_COLS_ENV:
        parts = re.split(r"[;,\s]+", TIME_COLS_ENV.strip())
        candidates.extend([p for p in parts if p])

    # Keep those present in gdf and de-dupe
    candidates = [c for c in candidates if c in gdf.columns]
    return _dedupe_preserve(candidates)

def main():
    st.title("STAC Geoparquet Browser")

    # Selectable STAC path in session (can be a file or a directory)
    if "stac_selected_path" not in st.session_state:
        st.session_state["stac_selected_path"] = STAC_PATH
    if "browse_mode" not in st.session_state:
        st.session_state["browse_mode"] = False

    selected_path = st.session_state["stac_selected_path"]
    root = try_load_root(selected_path)

    # If not a STAC directory/file, or explicitly browsing, let the user pick a folder
    if (st.session_state.get("browse_mode") or (root is None)):
        base_dir = _normalize_start_path(selected_path)
        # Ensure browse mode is active if root isn't loadable
        if root is None:
            st.session_state["browse_mode"] = True
        st.sidebar.subheader("Select STAC Folder")

        if "fs_cwd" not in st.session_state:
            st.session_state["fs_cwd"] = base_dir

        # Ensure cwd stays under base_dir
        def _is_under(base, path):
            try:
                base = os.path.abspath(base)
                path = os.path.abspath(path)
                return os.path.commonpath([base, path]) == base
            except Exception:
                return False

        cwd = st.session_state["fs_cwd"]
        if not _is_under(base_dir, cwd):
            cwd = base_dir
            st.session_state["fs_cwd"] = cwd

        st.sidebar.caption("Browsing: {}".format(cwd))
        cols = st.sidebar.columns([1, 1, 1])
        with cols[0]:
            if st.button("⬆ Up", use_container_width=True, disabled=(os.path.abspath(cwd) == os.path.abspath(base_dir))):
                parent = os.path.dirname(cwd)
                if _is_under(base_dir, parent):
                    st.session_state["fs_cwd"] = parent
                    _rerun()
        with cols[1]:
            if st.button("Cancel", use_container_width=True):
                st.session_state["browse_mode"] = False
                _rerun()
        with cols[2]:
            pass

        # List subdirectories to navigate
        try:
            entries = sorted([d for d in os.listdir(cwd) if os.path.isdir(os.path.join(cwd, d))])
        except Exception:
            entries = []
        options = [".. (parent)"] + entries
        choice = st.sidebar.selectbox("Subfolders", options)
        if choice:
            if choice == ".. (parent)":
                pass  # up handled by button
            else:
                new_cwd = os.path.join(cwd, choice)
                if _is_under(base_dir, new_cwd):
                    st.session_state["fs_cwd"] = new_cwd
                    _rerun()

        # Offer to open STAC if current folder has catalog.json/collection.json
        if _dir_has_stac(cwd):
            if st.sidebar.button("Open STAC here", type="primary"):
                st.session_state["stac_selected_path"] = cwd
                st.session_state["browse_mode"] = False
                st.session_state.pop("stac_nav_stack", None)
                _rerun()

        st.info("Select a folder that contains catalog.json or collection.json to browse the STAC.")
        st.stop()

    # Expose a small control to change or explore the root path
    with st.sidebar.expander("STAC Source", expanded=False):
        st.caption("Current: {}".format(selected_path))
        new_path = st.text_input("STAC path (file or directory)", value=selected_path)
        c1, c2 = st.sidebar.columns([1, 1])
        with c1:
            if st.button("Reload"):
                st.session_state["stac_selected_path"] = new_path
                st.session_state.pop("stac_nav_stack", None)
                _rerun()
        with c2:
            if st.button("Browse folders"):
                st.session_state["stac_selected_path"] = new_path
                st.session_state["fs_cwd"] = _normalize_start_path(new_path)
                st.session_state["browse_mode"] = True
                _rerun()

    # Hierarchical STAC navigation down to collections using STAC relations
    selected_collection = None
    items_from_catalog_level = False
    loose_item_hrefs: list[str] = []

    # Track current catalog href in session; start at the selected root (supports Catalog or Collection)
    if "current_catalog_href" not in st.session_state:
        try:
            st.session_state["current_catalog_href"] = root.get_self_href() or selected_path
        except Exception:
            st.session_state["current_catalog_href"] = selected_path
    curr_href = st.session_state["current_catalog_href"]
    try:
        curr_catalog = Catalog.from_file(curr_href)
    except Exception:
        try:
            curr_catalog = Collection.from_file(curr_href)
        except Exception as e:
            st.error("Failed to load STAC at '{}': {}".format(curr_href, e))
            st.stop()

    # Parent/root navigation based on rel links
    parent_href = _get_rel_href(curr_catalog, "parent", curr_href)
    root_href = _get_rel_href(curr_catalog, "root", curr_href)
    nav_cols = st.sidebar.columns([1, 1])
    with nav_cols[0]:
        if st.button("← Parent", disabled=(parent_href is None), use_container_width=True):
            if parent_href:
                st.session_state["current_catalog_href"] = parent_href
                _rerun()
    with nav_cols[1]:
        if st.button("⟲ Root", disabled=(root_href is None), use_container_width=True):
            if root_href:
                st.session_state["current_catalog_href"] = root_href
                _rerun()
    try:
        label = curr_catalog.title or curr_catalog.id or os.path.basename(curr_href)
    except Exception:
        label = os.path.basename(curr_href)
    st.sidebar.caption("Current catalog: {}".format(label))

    # Immediate subcatalogs and collections (using rel=child internally)
    debug = {
        "current_href": curr_href,
        "current_type": type(curr_catalog).__name__,
        "self_href": getattr(curr_catalog, "get_self_href", lambda: None)(),
    }

    # Try direct children via PySTAC
    try:
        subcats = list(curr_catalog.get_children()) if curr_catalog is not None else []
    except Exception as e:
        debug["get_children_error"] = str(e)
        subcats = []
    debug["children_count_get_children"] = len(subcats)

    # Fallback 1: resolve rel=child links from the in-memory links
    loaded = []
    child_hrefs_mem = []
    raw_child_links: list[tuple[str, Optional[str]]] = []
    if not subcats and curr_catalog is not None:
        try:
            for lk in getattr(curr_catalog, "links", []) or []:
                if getattr(lk, "rel", None) != "child":
                    continue
                href = None
                try:
                    href = lk.get_absolute_href()  # type: ignore[attr-defined]
                except Exception:
                    href = None
                if not href:
                    base = getattr(curr_catalog, "get_self_href", lambda: None)() or curr_href
                    href = make_absolute_href(getattr(lk, "target", None) or getattr(lk, "href", None), base)
                if href:
                    child_hrefs_mem.append(href)
            for h in child_hrefs_mem:
                try:
                    try:
                        loaded.append(Catalog.from_file(h))
                    except Exception:
                        loaded.append(Collection.from_file(h))
                except Exception:
                    continue
            if loaded:
                subcats = loaded
        except Exception as e:
            debug["fallback_mem_links_error"] = str(e)
        debug["child_hrefs_from_links"] = child_hrefs_mem
        debug["children_count_from_links"] = len(loaded)

    # Fallback 2: parse the JSON file directly to extract rel=child links
    loaded = []
    child_hrefs_file = []
    if not subcats:
        try:
            with open(curr_href, "r", encoding="utf-8") as f:
                doc = json.load(f)
            links = doc.get("links") or []
            base = curr_href
            for lk in links:
                try:
                    if lk.get("rel") != "child":
                        continue
                    href = lk.get("href")
                    if not href:
                        continue
                    abs_href = make_absolute_href(href, base)
                    child_hrefs_file.append(abs_href)
                    raw_child_links.append((abs_href, lk.get("title") or lk.get("id")))
                except Exception:
                    continue
            for h in child_hrefs_file:
                try:
                    try:
                        loaded.append(Catalog.from_file(h))
                    except Exception:
                        loaded.append(Collection.from_file(h))
                except Exception:
                    continue
            if loaded:
                subcats = loaded
        except Exception as e:
            debug["fallback_file_parse_error"] = str(e)
        debug["child_hrefs_from_file"] = child_hrefs_file
        debug["children_count_from_file"] = len(loaded)
    debug["raw_child_links_count"] = len(raw_child_links)
    try:
        level_colls = list(curr_catalog.get_collections()) if curr_catalog is not None else []
    except Exception as e:
        debug["get_collections_error"] = str(e)
        level_colls = []
    debug["collections_count"] = len(level_colls)

    # Early debug panel so it shows even if we stop later
    # Console-only debug (no UI panel)
    if DEBUG_MODE:
        try:
            print("[STAC DEBUG]", json.dumps(debug)[:2000], flush=True)
        except Exception:
            pass

    if subcats:
        st.sidebar.caption("Subcatalogs ({})".format(len(subcats)))
        def _on_change_subcat():
            sc = st.session_state.get("subcat_choice")
            if sc:
                href = sc.get_self_href() or make_absolute_href("catalog.json", curr_href)
                st.session_state["current_catalog_href"] = href
                _rerun()
        st.sidebar.selectbox(
            "Subcatalog",
            subcats,
            key="subcat_choice",
            format_func=lambda c: (getattr(c, "title", None) or getattr(c, "id", None) or "(unnamed)"),
            on_change=_on_change_subcat,
        )

    if level_colls:
        st.sidebar.caption("Collections ({})".format(len(level_colls)))
        coll_options = [None] + level_colls
        def _format_coll(c):
            if c is None:
                return "— Select a collection —"
            return getattr(c, "title", None) or getattr(c, "id", None) or "(unnamed)"
        selected_collection = st.sidebar.selectbox(
            "Collection",
            coll_options,
            format_func=_format_coll,
            index=0,
        )
    else:
        # Detect Items linked directly from this catalog (rel=item) without recursion
        try:
            item_links_present = any(getattr(lk, "rel", None) == "item" for lk in (curr_catalog.links or []))
        except Exception as e:
            debug["item_links_detect_error"] = str(e)
            item_links_present = False

        # Prefer navigation; do not auto-list Items here
        if subcats or level_colls:
            if item_links_present:
                st.sidebar.checkbox("Show items at this level", value=False, key="show_items_here")
                items_from_catalog_level = bool(st.session_state.get("show_items_here"))
        else:
            if item_links_present:
                st.sidebar.info("This catalog has Items directly. Showing Items without a collection.")
                items_from_catalog_level = True
            else:
                # If there are child links but none could be loaded, inform the user clearly
                try:
                    child_links_count = sum(1 for lk in (curr_catalog.links or []) if getattr(lk, "rel", None) == "child")
                except Exception:
                    child_links_count = 0
                if child_links_count > 0:
                    err_msg = debug.get("get_children_error") or "Child links could not be loaded."
                    st.error("Found {} child links, but they could not be opened. {}".format(child_links_count, err_msg))
                else:
                    st.caption("No collections at this level.")

    # Determine the source of items to paginate: from a selected collection, or from the
    # current catalog level if it contains items directly
    collection = selected_collection

    # Paginated items to avoid loading all at once
    if "items_page_num" not in st.session_state:
        st.session_state["items_page_num"] = 1
    curr_page = int(st.session_state["items_page_num"])

    page_size = st.sidebar.number_input(
        "Items per page", min_value=10, max_value=1000,
        value=ITEMS_PAGE_SIZE_DEFAULT, step=10, key="items_page_size"
    )
    # Reset to first page when page size changes
    if st.session_state.get("last_items_page_size") != page_size:
        st.session_state["items_page_num"] = 1
        st.session_state["last_items_page_size"] = page_size
        curr_page = 1

    start = (curr_page - 1) * int(page_size)
    end = start + int(page_size)
    debug["pagination"] = {"page": curr_page, "page_size": int(page_size), "start": start, "end": end}
    if collection is not None and collection is not False:
        try:
            items_iter = collection.get_items()
        except Exception:
            items_iter = collection.get_all_items()
    else:
        if items_from_catalog_level:
            # Build an iterator only over items directly linked at this level
            try:
                # Define a lazy generator to resolve only the requested page of rel=item links
                def _iter_item_links_paginated(catalog, start_idx, end_idx):
                    base = catalog.get_self_href() or selected_path
                    i = -1
                    for lk in (catalog.links or []):
                        if getattr(lk, "rel", None) != "item":
                            continue
                        i += 1
                        if i < start_idx:
                            continue
                        if i >= end_idx:
                            break
                        href = None
                        try:
                            href = lk.get_absolute_href()  # type: ignore[attr-defined]
                        except Exception:
                            href = None
                        if not href:
                            href = make_absolute_href(getattr(lk, "target", None) or getattr(lk, "href", None), base)
                        if not href:
                            continue
                        try:
                            yield Item.from_file(href)
                        except Exception:
                            continue

                items_iter = _iter_item_links_paginated(curr_catalog, start, end + 1)
            except Exception:
                items_iter = iter(())
        else:
            if subcats or level_colls:
                st.info("Select a subcatalog or a collection to continue.")
                st.stop()
            st.warning("No collection selected.")
            st.stop()
    buf = list(islice(items_iter, start, end + 1))
    items_page = buf[: int(page_size)]
    has_next = len(buf) > int(page_size)
    debug["items_page_counts"] = {"fetched": len(items_page), "has_next": bool(has_next)}

    nav_cols = st.sidebar.columns([1, 1, 2])
    with nav_cols[0]:
        if st.button("◀", key="items_prev", disabled=(curr_page <= 1), use_container_width=True):
            st.session_state["items_page_num"] = max(1, curr_page - 1)
    with nav_cols[1]:
        if st.button("▶", key="items_next", disabled=(not has_next), use_container_width=True):
            st.session_state["items_page_num"] = curr_page + 1
    with nav_cols[2]:
        st.markdown("Page {}".format(curr_page))

    if not items_page:
        st.sidebar.warning("No items on this page. Adjust page size or go to previous page.")
        if curr_page > 1:
            st.session_state["items_page_num"] = curr_page - 1
        st.stop()

    item = st.sidebar.selectbox("Item", items_page, format_func=lambda i: i.id)

    # Accept both GeoParquet and Parquet assets (by media_type or file extension)
    def _is_parquet_asset(a):
        mt = (a.media_type or "").lower()
        href = (a.href or "").lower()
        return (
            "parquet" in mt
            or href.endswith(".parquet")
            or href.endswith(".geoparquet")
            or href.endswith(".parq")
        )

    geopq_assets = {k: a for k, a in item.assets.items() if _is_parquet_asset(a)}
    if not geopq_assets:
        st.warning("This item does not contain Parquet/GeoParquet assets.")
        st.stop()

    asset_key = st.sidebar.selectbox("Asset", list(geopq_assets.keys()))
    asset = geopq_assets.get(asset_key)
    if asset is None:
        st.warning("No valid asset selected.")
        st.stop()

    href = resolve_asset_href(asset, item, selected_path)
    # Clear and actionable errors when reading GeoParquet fails
    if not os.path.exists(href):
        st.error("Asset file not found: {}. Check relative paths in the STAC and your mounted volume.".format(href))
        if DEBUG_MODE:
            try:
                print("[STAC DEBUG] missing_asset=", href, flush=True)
            except Exception:
                pass
        st.stop()

    try:
        gdf = load_asset(href)
    except Exception as e:
        st.error(
            "Failed to load GeoParquet asset at '{}': {}. "
            "Tips: verify the file is a valid (Geo)Parquet, the path is correct relative to the item, "
            "and that the image has a Parquet engine (pyarrow or fastparquet).".format(href, e)
        )
        if DEBUG_MODE:
            try:
                print("[STAC DEBUG] geoparquet_read_error path=", href, " error=", str(e), flush=True)
            except Exception:
                pass
        st.stop()

    # Column metadata from STAC Table extension (if available)
    col_meta = _extract_table_columns_meta(collection, item, asset_key, asset)

    # Time filtering (supports multiple possible columns)
    # Column metadata from STAC Table extension (if available)
    # NOTE: `col_meta` computed above
    time_candidates = _auto_detect_time_columns(gdf, col_meta)
    time_column = None
    if time_candidates:
        # Prefer explicit TIME_COLUMN if present among candidates, else first
        default_idx = 0
        if TIME_COL and TIME_COL in time_candidates:
            default_idx = time_candidates.index(TIME_COL)
        time_column = st.sidebar.selectbox(
            "Time column",
            time_candidates,
            index=default_idx,
            format_func=lambda c: _format_column_option(c, col_meta),
        )

    if time_column:
        # Allow adjusting threshold in UI (defaults to env var)
        time_max_instants_ui = st.sidebar.number_input(
            "Max unique instants", min_value=10, max_value=10000,
            value=TIME_MAX_INSTANTS, step=10
        )
        # Show selected column description (if available)
        try:
            tmeta = col_meta.get(time_column, {}) if isinstance(col_meta, dict) else {}
            if tmeta.get("description") or tmeta.get("unit"):
                desc = tmeta.get("description") or ""
                unit = tmeta.get("unit")
                extra = " ({})".format(unit) if unit else ""
                st.sidebar.caption("Time: {}{}".format(desc, extra))
        except Exception:
            pass
        # Parse robustly; coerce invalid rows to NaT
        ts = pd.to_datetime(gdf[time_column], errors="coerce", utc=False)
        # Remove timezone for Streamlit widget compatibility
        try:
            ts = ts.dt.tz_localize(None)
        except Exception:
            pass
        valid_mask = ts.notna()
        if not valid_mask.any():
            st.sidebar.warning("Time column '{}' has no valid values.".format(time_column))
        else:
            instants = pd.Series(ts[valid_mask]).dropna()
            instants = instants.sort_values().drop_duplicates()
            # If the number of unique instants is manageable, let user pick an instant directly
            if len(instants) > 0 and len(instants) <= int(time_max_instants_ui):
                options = [t.to_pydatetime() if hasattr(t, "to_pydatetime") else t for t in instants]
                idx = len(options) - 1  # default to the latest
                chosen_dt = st.sidebar.selectbox(
                    "Instant", options, index=idx,
                    format_func=lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
                )
                gdf = gdf.loc[valid_mask].copy()
                gdf = gdf[(ts[valid_mask] == pd.Timestamp(chosen_dt)).values].copy()
                if gdf.empty:
                    st.info("No data available for the selected instant.")
                    st.stop()
            else:
                # Fallback: choose date first, then time available in that date
                dates = ts.dt.date
                unique_dates = sorted(pd.Series(dates[valid_mask]).dropna().unique())
                if not unique_dates:
                    st.sidebar.warning("No valid dates available for filtering.")
                else:
                    d_idx = len(unique_dates) - 1
                    chosen_date = st.sidebar.selectbox(
                        "Date", unique_dates, index=d_idx, format_func=lambda d: d.isoformat()
                    )
                    day_mask = dates[valid_mask] == chosen_date
                    day_instants = pd.Series(ts[valid_mask][day_mask]).dropna().sort_values().drop_duplicates()
                    if len(day_instants) == 0:
                        st.info("No data available for the selected date.")
                        st.stop()
                    t_idx = len(day_instants) - 1
                    time_options = [
                        t.to_pydatetime() if hasattr(t, "to_pydatetime") else t for t in day_instants
                    ]
                    chosen_time = st.sidebar.selectbox(
                        "Time", time_options, index=t_idx, format_func=lambda d: d.strftime("%H:%M:%S")
                    )
                    gdf = gdf.loc[valid_mask].copy()
                    final_mask = (ts[valid_mask] == pd.Timestamp(chosen_time)).values
                    gdf = gdf.loc[final_mask].copy()
                    if gdf.empty:
                        st.info("No data available for the selected time.")
                        st.stop()

    # Choose basemap and colormap for scalar/vector
    basemap_names = [
        ("Dark", "CartoDB dark_matter"),
        ("Gray", "CartoDB positron"),
        ("OSM", "OpenStreetMap"),
    ]
    basemap_labels = [b[0] for b in basemap_names]
    # Default to Gray (slightly lighter than dark)
    default_basemap_idx = 1
    basemap_label = st.sidebar.selectbox("Basemap", basemap_labels, index=default_basemap_idx)
    basemap_tiles = dict(basemap_names)[basemap_label]

    # Choose colormap for scalar/vector
    cmap_options = ["YlOrRd", "YlGnBu", "Viridis", "Plasma", "Magma", "Inferno"]
    try:
        default_cmap_idx = cmap_options.index(DEFAULT_COLORMAP_NAME)
    except ValueError:
        default_cmap_idx = 0
    cmap_name = st.sidebar.selectbox("Color map", cmap_options, index=default_cmap_idx)

    map_type = st.sidebar.selectbox("Map type", ["scalar", "vector", "density"])

    m = folium.Map(zoom_start=2, tiles=basemap_tiles, prefer_canvas=True)
    is_dark_basemap = basemap_label == "Dark"

    if map_type == "scalar":
        # Limit selector to numeric-like columns (excluding geometry)
        geom_name = getattr(gdf.geometry, "name", "geometry")
        numeric_candidates = [
            c for c in gdf.columns
            if c != geom_name and pd.to_numeric(gdf[c], errors="coerce").notna().sum() > 0
        ]
        default_col = SCALAR_COL if (SCALAR_COL and SCALAR_COL in numeric_candidates) else None
        if not numeric_candidates and not default_col:
            st.warning("No numeric columns detected for the scalar map.")
        else:
            options = numeric_candidates or [default_col]
            index = options.index(default_col) if default_col in options else 0
            column = st.sidebar.selectbox(
                "Scalar column",
                options,
                index=index,
                format_func=lambda c: _format_column_option(c, col_meta),
            )
            # Show selected column description (if available)
            try:
                sel_meta = col_meta.get(column, {})
                if sel_meta.get("description") or sel_meta.get("unit"):
                    desc = sel_meta.get("description") or ""
                    unit = sel_meta.get("unit")
                    extra = " ({})".format(unit) if unit else ""
                    st.sidebar.caption("{}{}".format(desc, extra))
            except Exception:
                pass
            max_points = st.sidebar.number_input(
                "Max points to draw", min_value=500, max_value=200000,
                value=MAX_POINTS_DEFAULT, step=500
            )

            # Coerce to numeric values
            vals = pd.to_numeric(gdf[column], errors="coerce")
            geom = gdf.geometry
            # Use representative points for non-point geometries
            pts = geom.copy()
            try:
                non_points = ~geom.geom_type.isin(["Point"]) | geom.is_empty | geom.isna()
            except Exception:
                non_points = geom.is_empty | geom.isna()
            if non_points.any():
                try:
                    pts.loc[non_points] = geom.loc[non_points].representative_point()
                except Exception:
                    pass
            valid = vals.notna() & pts.notna()
            if not valid.any():
                st.warning("No valid numeric values found for the scalar map.")
            else:
                v_all = vals[valid]
                p_all = pts[valid]
                # Sample to cap the number of drawn points
                if len(v_all) > int(max_points):
                    sample_idx = v_all.sample(int(max_points), random_state=42).index
                    v = v_all.loc[sample_idx]
                    p = p_all.loc[sample_idx]
                    st.caption("Showing {} of {} points".format(len(v), len(v_all)))
                else:
                    v, p = v_all, p_all
                vmin, vmax = float(v.min()), float(v.max())
                base_cm = get_colormap_by_name(cmap_name)
                cm = base_cm.scale(vmin, vmax)
                for i, value in v.items():
                    try:
                        y, x = p.loc[i].y, p.loc[i].x
                    except Exception:
                        continue
                    color = cm(float(value))
                    folium.CircleMarker(
                        location=[y, x], radius=3, color=color, fill=True, fill_color=color,
                    popup="{}: {}".format(column, value)
                    ).add_to(m)
                # Add colorbar legend
                add_color_legend(m, cm, vmin, vmax, _legend_label(column, col_meta), is_dark=is_dark_basemap)
                _fit_bounds_from_geoms(m, p)
    elif map_type == "vector":
        # Limit to numeric-like columns (excluding geometry)
        geom_name = getattr(gdf.geometry, "name", "geometry")
        numeric_candidates = [
            c for c in gdf.columns
            if c != geom_name and pd.to_numeric(gdf[c], errors="coerce").notna().sum() > 0
        ]
        if not numeric_candidates:
            st.warning("No numeric columns detected for the wind map.")
            st.stop()

        default_dir = WIND_DIR if (WIND_DIR and WIND_DIR in numeric_candidates) else None
        default_spd = WIND_SPEED if (WIND_SPEED and WIND_SPEED in numeric_candidates) else None

        dir_index = numeric_candidates.index(default_dir) if default_dir in numeric_candidates else 0
        spd_index = numeric_candidates.index(default_spd) if default_spd in numeric_candidates else 0

        dir_col = st.sidebar.selectbox(
            "Direction column",
            numeric_candidates,
            index=dir_index,
            format_func=lambda c: _format_column_option(c, col_meta),
        )
        spd_col = st.sidebar.selectbox(
            "Speed column",
            numeric_candidates,
            index=spd_index,
            format_func=lambda c: _format_column_option(c, col_meta),
        )

        # Column descriptions
        try:
            d_meta = col_meta.get(dir_col, {})
            if d_meta.get("description") or d_meta.get("unit"):
                desc = d_meta.get("description") or ""
                unit = d_meta.get("unit")
                extra = " ({})".format(unit) if unit else ""
                st.sidebar.caption("Direction: {}{}".format(desc, extra))
        except Exception:
            pass
        try:
            s_meta = col_meta.get(spd_col, {})
            if s_meta.get("description") or s_meta.get("unit"):
                desc = s_meta.get("description") or ""
                unit = s_meta.get("unit")
                extra = " ({})".format(unit) if unit else ""
                st.sidebar.caption("Speed: {}{}".format(desc, extra))
        except Exception:
            pass

        dir_vals = pd.to_numeric(gdf[dir_col], errors="coerce")
        spd_vals = pd.to_numeric(gdf[spd_col], errors="coerce")
        geom = gdf.geometry
        pts = geom.copy()
        try:
            non_points = ~geom.geom_type.isin(["Point"]) | geom.is_empty | geom.isna()
        except Exception:
            non_points = geom.is_empty | geom.isna()
        if non_points.any():
            try:
                pts.loc[non_points] = geom.loc[non_points].representative_point()
            except Exception:
                pass
        valid = dir_vals.notna() & spd_vals.notna() & pts.notna()
        if not valid.any():
            st.warning("No valid data found for the wind map.")
        else:
            max_vectors = st.sidebar.number_input(
                "Max vectors to draw", min_value=500, max_value=200000,
                value=MAX_POINTS_DEFAULT, step=500
            )
            # Color by speed like scalar
            v_all = spd_vals[valid]
            p_all = pts[valid]
            # Sample to cap the number of vectors
            if len(v_all) > int(max_vectors):
                sample_idx = v_all.sample(int(max_vectors), random_state=42).index
                v = v_all.loc[sample_idx]
                p = p_all.loc[sample_idx]
                dir_vals = dir_vals.loc[sample_idx]
                st.caption("Showing {} of {} vectors".format(len(v), len(v_all)))
            else:
                v, p = v_all, p_all
            vmin, vmax = float(v.min()), float(v.max())
            base_cm = get_colormap_by_name(cmap_name)
            cm = base_cm.scale(vmin, vmax)
            for i in p.index:
                try:
                    lon, lat = p.loc[i].x, p.loc[i].y
                except Exception:
                    continue
                speed = float(v.loc[i])
                direction = math.radians(float(dir_vals.loc[i]))

                # Draw speed as scalar colored point
                color = cm(speed)
                folium.CircleMarker(
                    location=[lat, lon], radius=3, color=color, fill=True, fill_color=color,
                    popup="{}: {}".format(spd_col, speed)
                ).add_to(m)

                # Draw unit arrow for direction (transparent so it doesn't mask color)
                arrow_len = 0.07  # degrees (approx.)
                lat_rad = math.radians(lat)
                dx = (arrow_len * math.sin(direction)) / max(1e-6, abs(math.cos(lat_rad)))
                dy = arrow_len * math.cos(direction)
                end = (lat + dy, lon + dx)
                arrow_color = "#ffffff" if is_dark_basemap else "#000000"
                arrow_opacity = 0.7 if is_dark_basemap else 0.65
                folium.PolyLine([(lat, lon), end], color=arrow_color, weight=2, opacity=arrow_opacity).add_to(m)

                # Arrow head: two short segments at ~25 degrees from the shaft
                head_len = arrow_len * 0.35
                head_ang = math.radians(25)
                for sign in (+1, -1):
                    ang = direction + sign * head_ang
                    hx = (head_len * math.sin(ang)) / max(1e-6, abs(math.cos(lat_rad)))
                    hy = head_len * math.cos(ang)
                    hpt = (end[0] - hy, end[1] - hx)
                    folium.PolyLine([end, hpt], color=arrow_color, weight=2, opacity=arrow_opacity).add_to(m)
            # Add colorbar legend for speed
            add_color_legend(m, cm, vmin, vmax, _legend_label(spd_col, col_meta), is_dark=is_dark_basemap)
            _fit_bounds_from_geoms(m, pts[valid])
    else:  # density
        geom = gdf.geometry
        pts = geom.copy()
        try:
            non_points = ~geom.geom_type.isin(["Point"]) | geom.is_empty | geom.isna()
        except Exception:
            non_points = geom.is_empty | geom.isna()
        if non_points.any():
            try:
                pts.loc[non_points] = geom.loc[non_points].representative_point()
            except Exception:
                pass
        points = []
        for g in pts:
            try:
                points.append((g.y, g.x))
            except Exception:
                continue
        if not points:
            st.warning("No points could be derived for the density map.")
        else:
            HeatMap(points).add_to(m)
            # Fit bounds using geometry points
            try:
                _fit_bounds_from_geoms(m, pts[pts.notna()])
            except Exception:
                pass

    st.components.v1.html(m._repr_html_(), height=700)

    # Console-only debug (no UI panel)
    if DEBUG_MODE:
        try:
            print("[STAC DEBUG]", json.dumps(debug)[:2000], flush=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
