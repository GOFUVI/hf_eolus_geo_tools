#!/usr/bin/env python3
"""Add GeoParquet metadata to Parquet files.

Supports two modes:
- Local mode: process a local directory of Parquet files (no AWS required).
- S3 mode: process an S3 prefix (requires boto3 and AWS credentials).
"""

import argparse
import json
import os
import tempfile
from typing import Tuple

import pyarrow.parquet as pq
import shapely.wkb as wkb
from shapely.ops import unary_union

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
            {
                "name": "Geodetic longitude",
                "abbreviation": "Lon",
                "direction": "east",
                "unit": "degree",
            },
            {
                "name": "Geodetic latitude",
                "abbreviation": "Lat",
                "direction": "north",
                "unit": "degree",
            },
        ],
    },
    "id": {"authority": "OGC", "code": "CRS84"},
}


def parse_s3(uri: str) -> Tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError("URI must start with s3://")
    bucket_key = uri[5:]
    parts = bucket_key.split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) == 2 else ""
    return bucket, prefix.rstrip("/") + "/"


def list_parquet_objects(s3, bucket: str, prefix: str):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # Skip S3 pseudo-directories and Athena result folders
            if key.endswith("/") or "/query_results/" in key:
                continue
            # Do not rely on extension; downstream will validate
            yield key


def compute_bbox_s3(files, s3, bucket, geometry_column: str):
    geoms = []
    for key in files:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            s3.download_file(bucket, key, tmp.name)
            table = pq.read_table(tmp.name, columns=[geometry_column])
            for g in table.column(geometry_column).to_pylist():
                if g is None:
                    continue
                geoms.append(wkb.loads(g))
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    if not geoms:
        return [0, 0, 0, 0]
    union = unary_union(geoms)
    minx, miny, maxx, maxy = union.bounds
    return [minx, miny, maxx, maxy]


def patch_file_s3(key, bbox, s3, bucket, geometry_column: str, geometry_types):
    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        s3.download_file(bucket, key, tmp.name)
        table = pq.read_table(tmp.name)
        meta = dict(table.schema.metadata or {})
        geo = {
        "version": "1.0.0",
        "primary_column": geometry_column,
        "columns": {
            geometry_column: {
                "encoding": "WKB",
                "geometry_types": geometry_types,
                "crs": CRS84,
                "bbox": bbox,
            }
        },
        }
        meta[b"geo"] = json.dumps(geo).encode("utf-8")
        table = table.replace_schema_metadata(meta)
        pq.write_table(table, tmp.name)
        s3.upload_file(tmp.name, bucket, key)
        return True
    except Exception:
        return False
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def list_parquet_local(root_dir: str):
    for base, _dirs, files in os.walk(root_dir):
        for f in files:
            # Skip auxiliary files and hidden markers
            if f.startswith('.') or f.startswith('_') or f == 'SUCCESS':
                continue
            p = os.path.join(base, f)
            # Skip empty files
            try:
                if os.path.getsize(p) == 0:
                    continue
            except OSError:
                continue
            # Do not rely on extension; downstream will validate by attempting to read
            yield p


def compute_bbox_local(files, geometry_column: str):
    geoms = []
    for path in files:
        try:
            table = pq.read_table(path, columns=[geometry_column])
            for g in table.column(geometry_column).to_pylist():
                if g is None:
                    continue
                geoms.append(wkb.loads(g))
        except Exception:
            continue
    if not geoms:
        return [0, 0, 0, 0]
    union = unary_union(geoms)
    minx, miny, maxx, maxy = union.bounds
    return [minx, miny, maxx, maxy]


def patch_file_local(path, bbox, geometry_column: str, geometry_types):
    try:
        table = pq.read_table(path)
    except Exception:
        return False
    meta = dict(table.schema.metadata or {})
    geo = {
        "version": "1.0.0",
        "primary_column": geometry_column,
        "columns": {
            geometry_column: {
                "encoding": "WKB",
                "geometry_types": geometry_types,
                "crs": CRS84,
                "bbox": bbox,
            }
        },
    }
    meta[b"geo"] = json.dumps(geo).encode("utf-8")
    table = table.replace_schema_metadata(meta)
    pq.write_table(table, path)
    return True


def detect_geometry_types_from_sample(files, geometry_column: str, max_geoms: int = 200):
    seen = set()
    count = 0
    for path in files:
        try:
            table = pq.read_table(path, columns=[geometry_column])
            for g in table.column(geometry_column).to_pylist():
                if g is None:
                    continue
                try:
                    shp = wkb.loads(g)
                except Exception:
                    continue
                t = shp.geom_type
                # Map shapely names to GeoParquet names (they match)
                seen.add(t)
                count += 1
                if count >= max_geoms:
                    break
        except Exception:
            continue
        if count >= max_geoms:
            break
    if not seen:
        # Fallback to broadest type
        return ["Geometry"]
    # Normalize Multi vs single types present
    return sorted(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--s3-path", required=False, help="s3://bucket/prefix of Parquet dataset")
    parser.add_argument("--local-path", required=False, help="Local directory containing Parquet files")
    parser.add_argument("--geometry-column", required=False, default="geometry", help="Name of the geometry column (default: geometry)")
    args = parser.parse_args()

    if bool(args.s3_path) == bool(args.local_path):
        raise SystemExit("Provide exactly one of --s3-path or --local-path")

    if args.local_path:
        files = list(list_parquet_local(args.local_path))
        geometry_types = detect_geometry_types_from_sample(files, args.geometry_column)
        bbox = compute_bbox_local(files, args.geometry_column)
        patched = 0
        attempted = 0
        for path in files:
            attempted += 1
            if patch_file_local(path, bbox, args.geometry_column, geometry_types):
                patched += 1
        # Simple verification on first parquet-able file (schema-only)
        sample_meta_printed = False
        errors = []
        for path in files:
            try:
                schema = pq.read_schema(path)
                meta = schema.metadata or {}
                print(f"Patched {patched}/{attempted} files. Sample: {path}")
                print("HAS_GEO=", b"geo" in meta)
                if b"geo" in meta:
                    try:
                        print("GEO_JSON=", (meta[b"geo"].decode())[:400])
                    except Exception:
                        pass
                sample_meta_printed = True
                break
            except Exception as e:
                errors.append(f"{path}: {e}")
                continue
        if not sample_meta_printed:
            print(f"Patched {patched}/{attempted} files. No readable Parquet files found for verification.")
            for msg in errors[:5]:
                print("VERIFY_ERROR:", msg)
    else:
        # Lazy import to avoid requiring boto3 when not needed
        import boto3  # type: ignore

        bucket, prefix = parse_s3(args.s3_path)
        s3 = boto3.client("s3")
        files = list(list_parquet_objects(s3, bucket, prefix))
        # For S3 mode, detect geometry types using a few files downloaded temporarily
        # Reuse local detection by downloading a small sample
        tmpdir = tempfile.TemporaryDirectory()
        sample_paths = []
        for i, key in enumerate(files):
            if i >= 5:
                break
            p = os.path.join(tmpdir.name, f"sample_{i}.parquet")
            try:
                s3.download_file(bucket, key, p)
                sample_paths.append(p)
            except Exception:
                continue
        geometry_types = detect_geometry_types_from_sample(sample_paths, args.geometry_column)
        bbox = compute_bbox_s3(files, s3, bucket, args.geometry_column)
        attempted = 0
        patched = 0
        for key in files:
            attempted += 1
            if patch_file_s3(key, bbox, s3, bucket, args.geometry_column, geometry_types):
                patched += 1
        print(f"Patched {patched}/{attempted} S3 objects under s3://{bucket}/{prefix}")


if __name__ == "__main__":
    main()
