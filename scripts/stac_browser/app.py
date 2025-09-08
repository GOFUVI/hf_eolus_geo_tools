import os
import re
import re
import math
from datetime import datetime
import pandas as pd
import geopandas as gpd
import uuid
from itertools import islice
import streamlit as st
import folium
from folium.plugins import HeatMap
from pystac import Catalog, Collection
from pystac.utils import make_absolute_href
import branca.colormap as bcm
import html

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

@st.cache_data
def load_asset(href):
    return gpd.read_parquet(href)


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

    root = load_root(STAC_PATH)
    # If the root is a Catalog, list immediate child collections to avoid deep traversal; if it is a
    # Collection, use it directly.
    if isinstance(root, Collection):
        collections = [root]
    else:  # Catalog
        try:
            children = list(root.get_children())
            collections = [c for c in children if isinstance(c, Collection)]
            # Fallback: if none found, try all collections (may be slow)
            if not collections:
                collections = list(root.get_all_collections())
        except Exception:
            collections = list(root.get_all_collections())

    collection = st.sidebar.selectbox(
        "Collection", collections, format_func=lambda c: c.id
    )

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
    try:
        items_iter = collection.get_items()
    except Exception:
        items_iter = collection.get_all_items()
    buf = list(islice(items_iter, start, end + 1))
    items_page = buf[: int(page_size)]
    has_next = len(buf) > int(page_size)

    nav_cols = st.sidebar.columns([1, 1, 2])
    with nav_cols[0]:
        if st.button("◀", key="items_prev", disabled=(curr_page <= 1), use_container_width=True):
            st.session_state["items_page_num"] = max(1, curr_page - 1)
    with nav_cols[1]:
        if st.button("▶", key="items_next", disabled=(not has_next), use_container_width=True):
            st.session_state["items_page_num"] = curr_page + 1
    with nav_cols[2]:
        st.markdown("Página {}".format(curr_page))

    if not items_page:
        st.sidebar.warning("No hay Items en esta página. Ajusta tamaño o usa Prev.")
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
        st.warning("Este Item no contiene assets Parquet/GeoParquet.")
        st.stop()

    asset_key = st.sidebar.selectbox("Asset", list(geopq_assets.keys()))
    asset = geopq_assets.get(asset_key)
    if asset is None:
        st.warning("No se ha seleccionado ningún asset válido.")
        st.stop()

    href = resolve_asset_href(asset, item, STAC_PATH)
    gdf = load_asset(href)

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
            "Máx. instantes únicos", min_value=10, max_value=10000,
            value=TIME_MAX_INSTANTS, step=10
        )
        # Show selected column description (if available)
        try:
            tmeta = col_meta.get(time_column, {}) if isinstance(col_meta, dict) else {}
            if tmeta.get("description") or tmeta.get("unit"):
                desc = tmeta.get("description") or ""
                unit = tmeta.get("unit")
                extra = " ({})".format(unit) if unit else ""
                st.sidebar.caption("Tiempo: {}{}".format(desc, extra))
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
            st.sidebar.warning("La columna de tiempo '{}' no tiene valores válidos.".format(time_column))
        else:
            instants = pd.Series(ts[valid_mask]).dropna()
            instants = instants.sort_values().drop_duplicates()
            # If the number of unique instants is manageable, let user pick an instant directly
            if len(instants) > 0 and len(instants) <= int(time_max_instants_ui):
                options = [t.to_pydatetime() if hasattr(t, "to_pydatetime") else t for t in instants]
                idx = len(options) - 1  # default to the latest
                chosen_dt = st.sidebar.selectbox(
                    "Instante", options, index=idx,
                    format_func=lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
                )
                gdf = gdf.loc[valid_mask].copy()
                gdf = gdf[(ts[valid_mask] == pd.Timestamp(chosen_dt)).values].copy()
                if gdf.empty:
                    st.info("No hay datos para el instante seleccionado.")
                    st.stop()
            else:
                # Fallback: choose date first, then time available in that date
                dates = ts.dt.date
                unique_dates = sorted(pd.Series(dates[valid_mask]).dropna().unique())
                if not unique_dates:
                    st.sidebar.warning("No hay fechas válidas para filtrar.")
                else:
                    d_idx = len(unique_dates) - 1
                    chosen_date = st.sidebar.selectbox(
                        "Fecha", unique_dates, index=d_idx, format_func=lambda d: d.isoformat()
                    )
                    day_mask = dates[valid_mask] == chosen_date
                    day_instants = pd.Series(ts[valid_mask][day_mask]).dropna().sort_values().drop_duplicates()
                    if len(day_instants) == 0:
                        st.info("No hay datos para la fecha seleccionada.")
                        st.stop()
                    t_idx = len(day_instants) - 1
                    time_options = [
                        t.to_pydatetime() if hasattr(t, "to_pydatetime") else t for t in day_instants
                    ]
                    chosen_time = st.sidebar.selectbox(
                        "Hora", time_options, index=t_idx, format_func=lambda d: d.strftime("%H:%M:%S")
                    )
                    gdf = gdf.loc[valid_mask].copy()
                    final_mask = (ts[valid_mask] == pd.Timestamp(chosen_time)).values
                    gdf = gdf.loc[final_mask].copy()
                    if gdf.empty:
                        st.info("No hay datos para la hora seleccionada.")
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
            st.warning("No se detectaron columnas numéricas para el mapa escalar.")
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
                "Máx. puntos a dibujar", min_value=500, max_value=200000,
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
                st.warning("No hay valores numéricos válidos para el mapa escalar.")
            else:
                v_all = vals[valid]
                p_all = pts[valid]
                # Sample to cap the number of drawn points
                if len(v_all) > int(max_points):
                    sample_idx = v_all.sample(int(max_points), random_state=42).index
                    v = v_all.loc[sample_idx]
                    p = p_all.loc[sample_idx]
                    st.caption("Mostrando {} de {} puntos".format(len(v), len(v_all)))
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
            st.warning("No se detectaron columnas numéricas para el mapa de viento.")
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
                st.sidebar.caption("Dir: {}{}".format(desc, extra))
        except Exception:
            pass
        try:
            s_meta = col_meta.get(spd_col, {})
            if s_meta.get("description") or s_meta.get("unit"):
                desc = s_meta.get("description") or ""
                unit = s_meta.get("unit")
                extra = " ({})".format(unit) if unit else ""
                st.sidebar.caption("Vel: {}{}".format(desc, extra))
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
            st.warning("No hay datos válidos para el mapa de viento.")
        else:
            max_vectors = st.sidebar.number_input(
                "Máx. vectores a dibujar", min_value=500, max_value=200000,
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
                st.caption("Mostrando {} de {} vectores".format(len(v), len(v_all)))
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
            st.warning("No se pudieron derivar puntos para el mapa de densidad.")
        else:
            HeatMap(points).add_to(m)
            # Fit bounds using geometry points
            try:
                _fit_bounds_from_geoms(m, pts[pts.notna()])
            except Exception:
                pass

    st.components.v1.html(m._repr_html_(), height=700)


if __name__ == "__main__":
    main()
