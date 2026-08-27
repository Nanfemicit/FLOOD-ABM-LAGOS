# scripts/fetch_rainfall.py
"""
Decompress the cached CHIRPS-2.0 africa_monthly rasters for 2025, clip each
to the Lagos ward boundary extent, and write one GeoTIFF per month.

Source: UCSB Climate Hazards Center public data server
(data.chc.ucsb.edu/products/CHIRPS-2.0/africa_monthly/tifs/), no auth wall.
The .tif.gz files are pre-downloaded into
data/raw/rainfall/_source_cache/ (gitignored, regenerable) since the
server is slow and this script may be re-run during clipping iteration.
"""
import gzip
import shutil
from pathlib import Path

import geopandas as gpd
import rasterio
from rasterio.mask import mask
from shapely.geometry import box

BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"
SOURCE_CACHE = Path("data/raw/rainfall/_source_cache")
OUT_DIR = Path("data/raw/rainfall")
YEAR = 2025
MONTHS = [f"{m:02d}" for m in range(1, 13)]
NODATA = -9999.0


def main():
    wards = gpd.read_file(BOUNDARY_PATH)
    minx, miny, maxx, maxy = wards.total_bounds
    clip_geom = [box(minx, miny, maxx, maxy)]
    print(f"Ward extent: ({minx:.4f}, {miny:.4f}) - ({maxx:.4f}, {maxy:.4f})")

    for month in MONTHS:
        gz_path = SOURCE_CACHE / f"chirps-v2.0.{YEAR}.{month}.tif.gz"
        tif_path = SOURCE_CACHE / f"chirps-v2.0.{YEAR}.{month}.tif"
        with gzip.open(gz_path, "rb") as f_in, open(tif_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        with rasterio.open(tif_path) as src:
            clipped, transform = mask(src, clip_geom, crop=True, nodata=NODATA)
            meta = src.meta.copy()
            meta.update(
                height=clipped.shape[1],
                width=clipped.shape[2],
                transform=transform,
                nodata=NODATA,
                compress="lzw",
            )
            out_path = OUT_DIR / f"lagos_chirps_{YEAR}_{month}.tif"
            with rasterio.open(out_path, "w", **meta) as dst:
                dst.write(clipped)
            print(f"{YEAR}-{month}: saved {out_path} shape={clipped.shape[1:]}")

        tif_path.unlink()


if __name__ == "__main__":
    main()
