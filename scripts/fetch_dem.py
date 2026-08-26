# scripts/fetch_dem.py
"""
Download Copernicus GLO-30 DEM tiles covering the Lagos ward boundary
extent, merge them, and clip to that extent (with a small buffer).

Source: Copernicus DEM GLO-30, public AWS Open Data bucket
(s3://copernicus-dem-30m, no auth required). Tiles are 1x1 degree,
named by their lower-left corner, e.g. Copernicus_DSM_COG_10_N06_00_E002_00_DEM
("10" = 1 arc-second grid spacing ~ 30m; the bucket name uses meters,
the filename uses arc-seconds).
"""
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.merge import merge
from rasterio.mask import mask
from shapely.geometry import box

BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"
OUT_PATH = "data/raw/elevation/lagos_dem_glo30.tif"
TILE_CACHE_DIR = Path("data/raw/elevation/_tile_cache")
BUFFER_DEG = 0.05  # ~5.5 km margin around the ward extent, for flow routing near edges

S3_BASE = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"


def tile_name(lat_deg, lon_deg):
    ns = "N" if lat_deg >= 0 else "S"
    ew = "E" if lon_deg >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_deg):02d}_00_{ew}{abs(lon_deg):03d}_00_DEM"


def download_tile(name):
    TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = TILE_CACHE_DIR / f"{name}.tif"
    if dest.exists():
        return dest
    url = f"{S3_BASE}/{name}/{name}.tif"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def main():
    wards = gpd.read_file(BOUNDARY_PATH)
    minx, miny, maxx, maxy = wards.total_bounds
    minx, miny, maxx, maxy = minx - BUFFER_DEG, miny - BUFFER_DEG, maxx + BUFFER_DEG, maxy + BUFFER_DEG
    print(f"Buffered extent: ({minx:.4f}, {miny:.4f}) - ({maxx:.4f}, {maxy:.4f})")

    lat_tiles = range(math.floor(miny), math.floor(maxy) + 1)
    lon_tiles = range(math.floor(minx), math.floor(maxx) + 1)
    names = [tile_name(lat, lon) for lat in lat_tiles for lon in lon_tiles]
    print(f"Tiles needed: {names}")

    tile_paths = []
    for name in names:
        print(f"Fetching {name} ...")
        tile_paths.append(download_tile(name))

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

    clip_geom = [box(minx, miny, maxx, maxy)]
    tmp_path = Path(OUT_PATH).with_suffix(".mosaic.tif")
    Path(OUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(tmp_path, "w", **out_meta) as dst:
        dst.write(mosaic)
    with rasterio.open(tmp_path) as src:
        clipped, clipped_transform = mask(src, clip_geom, crop=True)
        clipped_meta = src.meta.copy()
        clipped_meta.update(
            height=clipped.shape[1],
            width=clipped.shape[2],
            transform=clipped_transform,
            compress="lzw",
            predictor=3,
        )
        with rasterio.open(OUT_PATH, "w", **clipped_meta) as dst:
            dst.write(clipped)
    tmp_path.unlink()

    with rasterio.open(OUT_PATH) as src:
        data = src.read(1, masked=True)
        print(f"\nSaved: {OUT_PATH}")
        print(f"CRS: {src.crs}")
        print(f"Shape: {src.height} x {src.width}")
        print(f"Resolution (deg): {src.res}")
        print(f"Elevation range (m): {float(data.min())} to {float(data.max())}")
        print(f"NaN/masked fraction: {float(np.ma.getmaskarray(data).mean()):.4f}")


if __name__ == "__main__":
    main()
