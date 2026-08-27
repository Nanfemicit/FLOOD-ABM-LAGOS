# scripts/fetch_landcover.py
"""
Download ESA WorldCover v200 (2021) 10m tiles covering the Lagos ward
extent, merge them, and mask directly to the ward union polygon (not a
bounding box -- applying the DEM buffer-vs-mask lesson from the start
this time instead of fixing it after the fact).

Source: ESA WorldCover v200, public AWS Open Data bucket
(s3://esa-worldcover, no auth required). Tiles are 3x3 degree, named by
their lower-left corner, e.g. ESA_WorldCover_10m_2021_v200_N06E000_Map.tif.
"""
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.merge import merge
from rasterio.mask import mask

BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"
OUT_PATH = "data/raw/landcover/lagos_landcover.tif"
TILE_CACHE_DIR = Path("data/raw/landcover/_source_cache")
TILE_SIZE_DEG = 3

S3_BASE = "https://esa-worldcover.s3.amazonaws.com/v200/2021/map"


def tile_name(lat_deg, lon_deg):
    ns = "N" if lat_deg >= 0 else "S"
    ew = "E" if lon_deg >= 0 else "W"
    return f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat_deg):02d}{ew}{abs(lon_deg):03d}_Map"


def verify_tile(path):
    """Actually open and read the raster, not just check size/exit code."""
    try:
        with rasterio.open(path) as src:
            data = src.read(1)
            if data.size == 0:
                return False
            return True
    except Exception as e:
        print(f"  verify failed for {path}: {e}")
        return False


def download_tile(name, max_attempts=4):
    TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = TILE_CACHE_DIR / f"{name}.tif"
    if dest.exists() and verify_tile(dest):
        print(f"  {name}: already cached and verified")
        return dest

    url = f"{S3_BASE}/{name}.tif"
    for attempt in range(1, max_attempts + 1):
        print(f"  {name}: downloading (attempt {attempt})...")
        try:
            with requests.get(url, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
        except requests.exceptions.RequestException as e:
            print(f"  {name}: request failed ({e}), retrying")
            continue
        if verify_tile(dest):
            print(f"  {name}: verified OK")
            return dest
        print(f"  {name}: failed verification, retrying")
    raise RuntimeError(f"Could not download a valid copy of {name} after {max_attempts} attempts")


def main():
    wards = gpd.read_file(BOUNDARY_PATH)
    minx, miny, maxx, maxy = wards.total_bounds
    print(f"Ward extent: ({minx:.4f}, {miny:.4f}) - ({maxx:.4f}, {maxy:.4f})")

    lat_tiles = range(
        TILE_SIZE_DEG * math.floor(miny / TILE_SIZE_DEG),
        TILE_SIZE_DEG * (math.floor(maxy / TILE_SIZE_DEG) + 1),
        TILE_SIZE_DEG,
    )
    lon_tiles = range(
        TILE_SIZE_DEG * math.floor(minx / TILE_SIZE_DEG),
        TILE_SIZE_DEG * (math.floor(maxx / TILE_SIZE_DEG) + 1),
        TILE_SIZE_DEG,
    )
    names = [tile_name(lat, lon) for lat in lat_tiles for lon in lon_tiles]
    print(f"Tiles needed: {names}")

    tile_paths = [download_tile(name) for name in names]

    srcs = [rasterio.open(p) for p in tile_paths]
    mosaic, out_transform = merge(srcs)
    out_meta = srcs[0].meta.copy()
    out_meta.update(
        driver="GTiff",
        height=mosaic.shape[1],
        width=mosaic.shape[2],
        transform=out_transform,
    )
    for s in srcs:
        s.close()

    tmp_path = Path(OUT_PATH).with_suffix(".mosaic.tif")
    Path(OUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(tmp_path, "w", **out_meta) as dst:
        dst.write(mosaic)

    ward_union = [wards.union_all()]
    with rasterio.open(tmp_path) as src:
        clipped, clipped_transform = mask(src, ward_union, crop=True)
        clipped_meta = src.meta.copy()
        clipped_meta.update(
            height=clipped.shape[1],
            width=clipped.shape[2],
            transform=clipped_transform,
            compress="lzw",
        )
        with rasterio.open(OUT_PATH, "w", **clipped_meta) as dst:
            dst.write(clipped)
    tmp_path.unlink()

    if not verify_tile(OUT_PATH):
        raise RuntimeError(f"Output {OUT_PATH} failed post-write verification")

    with rasterio.open(OUT_PATH) as src:
        data = src.read(1)
        print(f"\nSaved: {OUT_PATH}")
        print(f"CRS: {src.crs}")
        print(f"Shape: {src.height} x {src.width}")
        print(f"Resolution (deg): {src.res}")
        print(f"nodata: {src.nodata}")
        vals, counts = np.unique(data, return_counts=True)
        print(f"Unique values present: {dict(zip(vals.tolist(), counts.tolist()))}")


if __name__ == "__main__":
    main()
